"""普通 HTTP 客户端可完整管理持久定时任务。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan
from tianshu.models import Edict
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.edict import EdictSchedule


@pytest.fixture
async def scheduler_client():
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, app


async def _future_job(app) -> str:
    edict = Edict(
        goal="scheduled API task",
        title="每日巡检",
        schedule=EdictSchedule(
            type="once",
            at=datetime.now(UTC) + timedelta(hours=2),
            timezone="Asia/Shanghai",
        ),
    )
    app.state.storage.save_edict(edict)
    return await app.state.scheduler.schedule(edict)


async def test_list_hides_internal_immediate_execution_rows(scheduler_client):
    client, app = scheduler_client
    edict = Edict(
        goal="ordinary task",
        title="立即执行任务",
        schedule=EdictSchedule(type="immediate"),
    )
    app.state.storage.save_edict(edict)
    app.state.storage.save_scheduler_job("immediate-job", edict.id, "immediate")
    app.state.storage.complete_scheduler_job("immediate-job")

    listed = await client.get("/api/scheduler/jobs")

    assert listed.status_code == 200
    assert all(job["job_id"] != "immediate-job" for job in listed.json()["data"])
    assert app.state.storage.get_scheduler_job("immediate-job")["status"] == "completed"


async def test_pause_resume_update_history_and_cancel(scheduler_client):
    client, app = scheduler_client
    job_id = await _future_job(app)

    listed = await client.get("/api/scheduler/jobs")
    assert listed.status_code == 200
    job = next(item for item in listed.json()["data"] if item["job_id"] == job_id)
    assert job["title"] == "每日巡检"
    assert job["timezone"] == "Asia/Shanghai"
    assert job["status"] == "active"

    paused = await client.post(f"/api/scheduler/jobs/{job_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["data"]["status"] == "paused"

    updated = await client.patch(
        f"/api/scheduler/jobs/{job_id}",
        json={
            "schedule": {
                "type": "cron",
                "cron": "0 9 * * 1",
                "timezone": "Asia/Shanghai",
            }
        },
    )
    assert updated.status_code == 200

    resumed = await client.post(f"/api/scheduler/jobs/{job_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["data"]["status"] == "active"

    history = await client.get(f"/api/scheduler/jobs/{job_id}/runs")
    assert history.status_code == 200
    assert history.json()["data"] == []

    cancelled = await client.delete(f"/api/scheduler/jobs/{job_id}")
    assert cancelled.status_code == 200
    already_cancelled = await client.delete(f"/api/scheduler/jobs/{job_id}")
    assert already_cancelled.status_code == 409
    cannot_run_cancelled = await client.post(
        f"/api/scheduler/jobs/{job_id}/run-now",
        headers={"Idempotency-Key": "cancelled-schedule-run-now"},
    )
    assert cannot_run_cancelled.status_code == 409
    missing = await client.delete("/api/scheduler/jobs/missing")
    assert missing.status_code == 404


async def test_invalid_timezone_and_unknown_job_are_explicit(scheduler_client):
    client, app = scheduler_client
    job_id = await _future_job(app)

    invalid = await client.patch(
        f"/api/scheduler/jobs/{job_id}",
        json={
            "schedule": {
                "type": "cron",
                "cron": "0 9 * * *",
                "timezone": "Bogus/Zone",
            }
        },
    )
    assert invalid.status_code == 422

    unknown = await client.post("/api/scheduler/jobs/missing/pause")
    assert unknown.status_code == 404


async def test_run_now_requires_replay_identity(scheduler_client):
    client, app = scheduler_client
    job_id = await _future_job(app)

    missing = await client.post(f"/api/scheduler/jobs/{job_id}/run-now")
    assert missing.status_code == 422

    app.state.scheduler.run_now = AsyncMock(return_value=True)
    queued = await client.post(
        f"/api/scheduler/jobs/{job_id}/run-now",
        headers={"Idempotency-Key": "scheduler-run-now-api"},
    )
    assert queued.status_code == 200
    app.state.scheduler.run_now.assert_awaited_once_with(  # type: ignore[attr-defined]
        job_id,
        idempotency_key="scheduler-run-now-api",
    )


async def test_run_now_recovers_expired_attempt_to_failed_terminal(scheduler_client, monkeypatch):
    client, app = scheduler_client
    edict = Edict(
        goal="recover an interrupted immediate run",
        runtime={"retry_limit": 0},
        schedule=EdictSchedule(
            type="once",
            at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    app.state.storage.save_edict(edict)
    job_id = await app.state.scheduler.schedule(edict)
    dispatcher = app.state.run_dispatcher
    dispatch = dispatcher.dispatch
    monkeypatch.setattr(dispatcher, "dispatch", AsyncMock(return_value=False))
    queued = await client.post(
        f"/api/scheduler/jobs/{job_id}/run-now",
        headers={"Idempotency-Key": "scheduler-run-now-expired-attempt"},
    )
    assert queued.status_code == 200

    now = datetime.now(UTC)
    storage = app.state.storage
    with storage._lock:  # noqa: SLF001
        attempt = storage._conn.execute(  # noqa: SLF001
            "SELECT attempt_id, memorial_id FROM execution_attempts ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        assert attempt is not None
    claimed = storage.attempt_repo.claim(
        memorial_id=attempt["memorial_id"],
        owner_id="interrupted-worker",
        now=now,
        lease_seconds=30,
    )
    assert claimed is not None
    monkeypatch.setattr(dispatcher, "dispatch", dispatch)
    with storage._lock, storage._conn:  # noqa: SLF001
        storage._conn.execute(  # noqa: SLF001
            """
            UPDATE execution_attempts
            SET heartbeat_at=?, lease_expires_at=?, updated_at=?
            WHERE attempt_id=? AND status='claimed'
            """,
            (
                (now - timedelta(seconds=31)).isoformat(),
                (now - timedelta(seconds=1)).isoformat(),
                now.isoformat(),
                attempt["attempt_id"],
            ),
        )

    for _ in range(3):
        await app.state.run_reconciler.reconcile_once()

    history = await client.get(f"/api/scheduler/jobs/{job_id}/runs")
    assert history.status_code == 200
    run = history.json()["data"][0]
    assert run["execution_status"] == "failed"
    assert run["completed_at"] is not None
    assert run["error"] == "execution lease expired"
    assert storage.get_memorial(attempt["memorial_id"]).status.value == "failed"
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id=?",
            (attempt["attempt_id"],),
        ).fetchone()[0]
        == "dead_letter"
    )


async def test_run_now_demo_execution_reaches_terminal_without_lease_expiry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "demo")
    monkeypatch.setenv("TIANSHU_EVAL_MODE", "1")
    monkeypatch.setenv("TIANSHU_WORKSPACE_DIR", str(tmp_path / "workspace"))
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            edict = Edict(
                goal="return the deterministic scheduler acceptance result",
                runtime={"retry_limit": 0},
                schedule=EdictSchedule(
                    type="once",
                    at=datetime.now(UTC) + timedelta(hours=2),
                ),
            )
            app.state.storage.save_edict(edict)
            job_id = await app.state.scheduler.schedule(edict)

            queued = await client.post(
                f"/api/scheduler/jobs/{job_id}/run-now",
                headers={"Idempotency-Key": "scheduler-demo-golden-path"},
            )
            assert queued.status_code == 200
            await app.state.run_dispatcher.wait_until_idle()

            history = await client.get(f"/api/scheduler/jobs/{job_id}/runs")
            assert history.status_code == 200
            run = history.json()["data"][0]
            assert run["execution_status"] == "completed"
            assert run["completed_at"] is not None
            assert run["error"] is None


async def test_create_edict_accepts_user_schedule(scheduler_client):
    client, _app = scheduler_client
    target = datetime.now(UTC) + timedelta(hours=3)

    response = await client.post(
        "/api/edicts",
        headers={"Idempotency-Key": "scheduled-create-api"},
        json={
            "idempotency_key": "scheduled-create-api",
            "goal": "later through ordinary API",
            "schedule": {
                "type": "once",
                "at": target.isoformat(),
                "timezone": "Asia/Shanghai",
            },
        },
    )

    assert response.status_code in {200, 202}
    schedule = response.json()["data"]["schedule"]
    assert schedule["type"] == "once"
    assert schedule["timezone"] == "Asia/Shanghai"


async def test_create_edict_rejects_past_or_incomplete_schedule(scheduler_client):
    client, _app = scheduler_client
    base = {
        "goal": "do not silently run a broken schedule now",
    }

    past = await client.post(
        "/api/edicts",
        headers={"Idempotency-Key": "scheduled-create-past"},
        json={
            **base,
            "idempotency_key": "scheduled-create-past",
            "schedule": {
                "type": "once",
                "at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            },
        },
    )
    assert past.status_code == 422
    assert past.json()["detail"]["code"] == "schedule_time_must_be_future"

    incomplete = await client.post(
        "/api/edicts",
        headers={"Idempotency-Key": "scheduled-create-cron-missing"},
        json={
            **base,
            "idempotency_key": "scheduled-create-cron-missing",
            "schedule": {"type": "cron"},
        },
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["detail"]["code"] == "cron_expression_required"


async def test_create_rejects_recurring_long_task_but_accepts_once(scheduler_client):
    client, _app = scheduler_client
    long_task = {
        "goal": "run a governed outer loop",
        "acceptance": {},
        "execution_profile": "checkpointed",
    }

    recurring = await client.post(
        "/api/edicts",
        headers={"Idempotency-Key": "recurring-long-task"},
        json={
            **long_task,
            "schedule": {"type": "cron", "cron": "0 9 * * *"},
        },
    )
    assert recurring.status_code == 422
    assert recurring.json()["detail"]["code"] == "recurring_long_running_unsupported"

    target = datetime.now(UTC) + timedelta(hours=3)
    once = await client.post(
        "/api/edicts",
        headers={"Idempotency-Key": "once-long-task"},
        json={
            **long_task,
            "schedule": {
                "type": "once",
                "at": target.isoformat(),
                "concurrency_policy": "skip",
            },
        },
    )
    assert once.status_code in {200, 202}
    assert once.json()["data"]["schedule"]["type"] == "once"


async def test_create_rejects_allow_concurrency_for_long_task(scheduler_client):
    client, _app = scheduler_client
    response = await client.post(
        "/api/edicts",
        headers={"Idempotency-Key": "long-task-allow-policy"},
        json={
            "goal": "unsafe concurrent long task",
            "acceptance": {},
            "execution_profile": "checkpointed",
            "schedule": {
                "type": "once",
                "at": (datetime.now(UTC) + timedelta(hours=3)).isoformat(),
                "concurrency_policy": "allow",
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "long_running_concurrency_must_skip"


async def test_reschedule_cannot_turn_once_long_task_into_recurring(scheduler_client):
    client, app = scheduler_client
    edict = Edict(
        goal="single scheduled long task",
        acceptance=AcceptanceCriteria(),
        execution_profile="checkpointed",
        schedule=EdictSchedule(
            type="once",
            at=datetime.now(UTC) + timedelta(hours=2),
        ),
    )
    app.state.storage.save_edict(edict)
    job_id = await app.state.scheduler.schedule(edict)

    response = await client.patch(
        f"/api/scheduler/jobs/{job_id}",
        json={"schedule": {"type": "cron", "cron": "0 9 * * *"}},
    )

    assert response.status_code == 422
    assert "recurring schedules do not support long-running tasks" in response.json()["detail"]
