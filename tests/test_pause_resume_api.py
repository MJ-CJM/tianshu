"""Edict pause/resume API 测试。"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan
from tianshu.models import TaskStatus


@pytest.fixture
async def client():
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _create_edict(client, *, idempotency_key: str) -> str:
    with patch("tianshu.executor.agent.LLMClient"):
        resp = await client.post(
            "/api/edicts",
            json={
                "idempotency_key": idempotency_key,
                "goal": "test goal",
                "acceptance": {"max_outer_iterations": 3},
            },
        )
    assert resp.status_code in (200, 201, 202)
    return resp.json()["data"]["id"]


async def test_pause_active_edict(client):
    eid = await _create_edict(client, idempotency_key="pause-resume-pause-active")
    resp = await client.post(f"/api/edicts/{eid}/pause")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["lifecycle_phase"] == "paused"
    assert body["data"]["effective_after"] == "current_round"


async def test_resume_paused_edict(client):
    eid = await _create_edict(client, idempotency_key="pause-resume-resume-paused")
    await client.post(f"/api/edicts/{eid}/pause")
    resp = await client.post(f"/api/edicts/{eid}/resume")
    assert resp.status_code == 200
    assert resp.json()["data"]["lifecycle_phase"] == "active"


async def test_pause_unknown_edict_returns_404(client):
    resp = await client.post("/api/edicts/nonexistent/pause")
    assert resp.status_code == 404


async def test_resume_unknown_edict_returns_404(client):
    resp = await client.post("/api/edicts/nonexistent/resume")
    assert resp.status_code == 404


async def test_resume_active_edict_is_idempotent(client):
    """resume 一个已经 active 的不报错（幂等）。"""
    eid = await _create_edict(client, idempotency_key="pause-resume-resume-active")
    resp = await client.post(f"/api/edicts/{eid}/resume")
    assert resp.status_code == 200
    assert resp.json()["data"]["lifecycle_phase"] == "active"


async def test_pause_active_edict_is_idempotent(client):
    """pause 一个已经 paused 的不报错（幂等）。"""
    eid = await _create_edict(client, idempotency_key="pause-resume-pause-idempotent")
    await client.post(f"/api/edicts/{eid}/pause")
    resp = await client.post(f"/api/edicts/{eid}/pause")
    assert resp.status_code == 200
    assert resp.json()["data"]["lifecycle_phase"] == "paused"


async def test_pause_complete_edict_returns_409(client):
    """已完成的 edict 不能 pause。"""
    eid = await _create_edict(client, idempotency_key="pause-resume-pause-complete")
    storage = client._transport.app.state.storage
    storage.update_edict_lifecycle_phase(eid, "complete")

    resp = await client.post(f"/api/edicts/{eid}/pause")
    assert resp.status_code == 409


async def test_resume_complete_edict_returns_409(client):
    """已完成的 edict 不能 resume。"""
    eid = await _create_edict(client, idempotency_key="pause-resume-resume-complete")
    storage = client._transport.app.state.storage
    storage.update_edict_lifecycle_phase(eid, "complete")

    resp = await client.post(f"/api/edicts/{eid}/resume")
    assert resp.status_code == 409


async def test_pause_winding_down_edict_returns_409(client):
    """winding_down edict 不能 pause（保护预算约束）。"""
    eid = await _create_edict(client, idempotency_key="pause-resume-pause-winding-down")
    storage = client._transport.app.state.storage
    storage.update_edict_lifecycle_phase(eid, "winding_down")
    resp = await client.post(f"/api/edicts/{eid}/pause")
    assert resp.status_code == 409


async def test_resume_winding_down_edict_returns_409(client):
    """winding_down edict 不能 resume（必须等收尾完）。"""
    eid = await _create_edict(client, idempotency_key="pause-resume-resume-winding-down")
    storage = client._transport.app.state.storage
    storage.update_edict_lifecycle_phase(eid, "winding_down")
    resp = await client.post(f"/api/edicts/{eid}/resume")
    assert resp.status_code == 409


@pytest.mark.parametrize("action", ["pause", "resume"])
async def test_completed_memorial_cannot_pause_or_resume(client, action):
    eid = await _create_edict(client, idempotency_key=f"pause-resume-terminal-{action}")
    storage = client._transport.app.state.storage
    memorial = storage.get_memorial_by_edict(eid)
    assert memorial is not None
    memorial.status = TaskStatus.COMPLETED
    storage.update_memorial(memorial)
    if action == "resume":
        storage.update_edict_lifecycle_phase(eid, "paused")

    response = await client.post(f"/api/edicts/{eid}/{action}")

    assert response.status_code == 409


@pytest.mark.parametrize("action", ["pause", "resume"])
async def test_non_open_edict_cannot_pause_or_resume(client, action):
    eid = await _create_edict(client, idempotency_key=f"pause-resume-closed-{action}")
    storage = client._transport.app.state.storage
    storage.update_edict_status(eid, "cancelled")
    if action == "resume":
        storage.update_edict_lifecycle_phase(eid, "paused")

    response = await client.post(f"/api/edicts/{eid}/{action}")

    assert response.status_code == 409


async def test_short_task_cannot_claim_pause_support(client):
    with patch("tianshu.executor.agent.LLMClient"):
        response = await client.post(
            "/api/edicts",
            json={
                "idempotency_key": "pause-resume-short-task",
                "goal": "one round",
            },
        )
    assert response.status_code in (200, 202)
    edict_id = response.json()["data"]["id"]

    paused = await client.post(f"/api/edicts/{edict_id}/pause")
    assert paused.status_code == 409


async def test_outer_loop_defaults_to_checkpoint_and_one_recovery(client):
    eid = await _create_edict(client, idempotency_key="pause-resume-safe-default")
    edict = client._transport.app.state.storage.get_edict(eid)

    assert edict.execution_profile == "checkpointed"
    assert edict.runtime.retry_limit >= 1


async def test_short_task_cannot_accept_outer_loop_steer(client):
    with patch("tianshu.executor.agent.LLMClient"):
        response = await client.post(
            "/api/edicts",
            json={
                "idempotency_key": "steer-short-task",
                "goal": "one round",
            },
        )
    edict_id = response.json()["data"]["id"]

    steered = await client.post(
        f"/api/edicts/{edict_id}/steer",
        json={"note": "do another round"},
    )
    assert steered.status_code == 409
