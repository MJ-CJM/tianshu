"""Production scheduler commits a durable fire before dispatching it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tianshu.application.scheduled_runs import PreparedFire
from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict, Memorial
from tianshu.scheduler.scheduler import Scheduler

_NOW = datetime(2026, 7, 16, 11, tzinfo=UTC)


class _Preparer:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.calls: list[dict[str, object]] = []

    def prepare(self, **kwargs: object) -> PreparedFire:
        self.order.append("prepare")
        self.calls.append(kwargs)
        return PreparedFire(
            fire_id="fire-1",
            job_id=str(kwargs["job_id"]),
            edict_id="edict-1",
            scheduled_at=kwargs["scheduled_at"],
            next_run=None,
            status="prepared",
            memorial_id="root-1",
            attempt_id="attempt-1",
            schedule_run_id="schedule-run-1",
            deduplicated=False,
        )

    def prepare_manual(self, **kwargs: object) -> PreparedFire:
        self.order.append("prepare_manual")
        self.calls.append(kwargs)
        return PreparedFire(
            fire_id="manual-fire-1",
            job_id=str(kwargs["job_id"]),
            edict_id="edict-1",
            scheduled_at=kwargs["scheduled_at"],
            next_run=_NOW,
            status="prepared",
            memorial_id="manual-root-1",
            attempt_id="manual-attempt-1",
            schedule_run_id="manual-run-1",
            deduplicated=False,
        )


class _Reconciler:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def reconcile_once(self) -> int:
        self.order.append("dispatch")
        return 1


async def test_immediate_fire_is_prepared_before_dispatch(storage) -> None:
    order: list[str] = []
    preparer = _Preparer(order)
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=_Reconciler(order),
        clock=lambda: _NOW,
    )
    edict = Edict(id="edict-1", goal="work")
    root = Memorial(id="root-1", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)

    await scheduler.schedule(
        edict,
        memorial_id=root.id,
        job_id="job-1",
        scheduled_at=_NOW,
    )

    assert order == ["prepare", "dispatch"]
    assert preparer.calls[0]["initial_memorial_id"] == root.id
    assert storage.get_scheduler_job("job-1")["next_run"] == _NOW.isoformat()


async def test_orphan_sweeper_is_diagnostic_only_in_managed_mode(storage) -> None:
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_Preparer([]),
        run_reconciler=_Reconciler([]),
        clock=lambda: _NOW,
    )
    edict = Edict(id="edict-1", goal="work")
    root = Memorial(id="root-1", edict_id=edict.id, instruction=edict.goal)
    storage.save_edict(edict)
    storage.save_memorial(root)
    before = storage.get_memorial(root.id)

    await scheduler._recover_orphan(root)  # noqa: SLF001

    assert storage.get_memorial(root.id) == before


async def test_run_now_uses_explicit_stable_key_without_moving_cursor(storage) -> None:
    order: list[str] = []
    preparer = _Preparer(order)
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=preparer,
        run_reconciler=_Reconciler(order),
        clock=lambda: _NOW,
    )
    edict = Edict(id="edict-1", goal="work")
    storage.save_edict(edict)
    storage.save_scheduler_job("job-1", edict.id, "immediate", next_run=_NOW)

    assert await scheduler.run_now("job-1", idempotency_key="manual-1")

    assert order == ["prepare_manual", "dispatch"]
    assert preparer.calls[0]["idempotency_key"] == "manual-1"


async def test_managed_run_now_rejects_missing_stable_key(storage) -> None:
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_Preparer([]),
        run_reconciler=_Reconciler([]),
        clock=lambda: _NOW,
    )
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_scheduler_job("job-1", "edict-1", "immediate", next_run=_NOW)

    with pytest.raises(ValueError, match="idempotency"):
        await scheduler.run_now("job-1")


@pytest.mark.parametrize("schedule_type", ["cron", "interval", "once"])
async def test_managed_resume_restores_cursor_and_only_starts_managed_loop(
    storage,
    monkeypatch,
    schedule_type: str,
) -> None:
    scheduler = Scheduler(
        EventBus(),
        storage,
        scheduled_run_preparer=_Preparer([]),
        run_reconciler=_Reconciler([]),
        clock=lambda: _NOW,
    )
    if schedule_type == "cron":
        schedule = {"type": "cron", "cron": "0 9 * * *"}
        job_kwargs = {"cron_expr": "0 9 * * *"}
    elif schedule_type == "interval":
        schedule = {"type": "interval", "interval_seconds": 3600}
        job_kwargs = {"interval_seconds": 3600}
    else:
        schedule = {"type": "once", "at": (_NOW + timedelta(hours=2)).isoformat()}
        job_kwargs = {}
    edict = Edict(id="edict-1", goal="work", schedule=schedule)
    storage.save_edict(edict)
    persisted_cursor = _NOW + timedelta(hours=1)
    storage.save_scheduler_job(
        "job-1",
        edict.id,
        schedule_type,
        next_run=persisted_cursor,
        **job_kwargs,
    )
    storage.set_scheduler_job_status("job-1", "paused")
    calls: list[str] = []

    async def managed(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args, kwargs
        calls.append("managed")

    async def legacy(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        del args, kwargs
        calls.append("legacy")

    monkeypatch.setattr(scheduler, "_managed_job_loop", managed)
    monkeypatch.setattr(scheduler, "_cron_loop", legacy)
    monkeypatch.setattr(scheduler, "_interval_loop", legacy)
    monkeypatch.setattr(scheduler, "_delayed_emit", legacy)

    assert await scheduler.resume("job-1") is True
    await scheduler._jobs["job-1"].task  # noqa: SLF001

    assert calls == ["managed"]
    assert scheduler._jobs["job-1"].next_run == persisted_cursor  # noqa: SLF001
    assert storage.get_scheduler_job("job-1")["next_run"] == persisted_cursor.isoformat()
