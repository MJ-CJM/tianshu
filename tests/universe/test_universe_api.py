"""Integration tests for /api/universes endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan


@pytest.fixture
async def client():
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_list_universes_empty(client):
    resp = await client.get("/api/universes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


async def test_list_universes_with_genesis(client):
    mgr = client._transport.app.state.universe_manager
    mgr.ensure_genesis()
    resp = await client.get("/api/universes")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["status"] == "champion"


async def test_get_universe_by_id(client):
    mgr = client._transport.app.state.universe_manager
    g = mgr.ensure_genesis()
    resp = await client.get(f"/api/universes/{g['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["id"] == g["id"]


async def test_get_universe_not_found(client):
    resp = await client.get("/api/universes/ghost-id")
    assert resp.status_code == 404


async def test_branch_universe(client):
    mgr = client._transport.app.state.universe_manager
    g = mgr.ensure_genesis()
    resp = await client.post(
        f"/api/universes/{g['id']}/branch",
        json={"name": "exp", "description": "test branch"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    challenger = body["data"]
    assert challenger["status"] == "challenger"
    assert challenger["parent_universe_id"] == g["id"]


async def test_branch_nonexistent_parent(client):
    resp = await client.post(
        "/api/universes/ghost-id/branch",
        json={"name": "exp"},
    )
    assert resp.status_code == 400


async def test_switch_is_stably_rejected_without_champion_mutation(client):
    mgr = client._transport.app.state.universe_manager
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")

    resp = await client.post(f"/api/universes/{ch['id']}/switch")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "promotion_preconditions_not_met"

    # Old champion should now be challenger
    all_universes = (await client.get("/api/universes")).json()["data"]
    statuses = {u["id"]: u["status"] for u in all_universes}
    assert statuses[g["id"]] == "champion"
    assert statuses[ch["id"]] == "challenger"


async def test_switch_nonexistent_universe(client):
    resp = await client.post("/api/universes/ghost-id/switch")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "promotion_preconditions_not_met"


async def test_archive_champion_returns_400(client):
    mgr = client._transport.app.state.universe_manager
    g = mgr.ensure_genesis()
    resp = await client.post(f"/api/universes/{g['id']}/archive")
    assert resp.status_code == 400


async def test_archive_challenger_succeeds(client):
    mgr = client._transport.app.state.universe_manager
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")

    resp = await client.post(f"/api/universes/{ch['id']}/archive")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "archived"


async def test_trigger_auto_propose_smoke(client):
    class _FakeEvolver:
        async def auto_propose_codes(self, trigger_source="manual"):
            return {"skipped": "disabled"}

    client._transport.app.state.universe_evolver = _FakeEvolver()
    resp = await client.post("/api/universes/propose-auto")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"] == {"skipped": "disabled"}


async def test_taiyi_report_get_is_read_only(client):
    app = client._transport.app
    generate = AsyncMock(side_effect=AssertionError("GET must not generate a report"))
    app.state.diagnostician.report = generate

    resp = await client.get("/api/universes/taiyi/report")

    assert resp.status_code == 200
    assert resp.json()["data"] == {
        "status": "not_generated",
        "report": None,
        "generated_at": None,
    }
    generate.assert_not_awaited()


async def test_taiyi_report_post_generates_and_get_reads_persisted_report(client):
    app = client._transport.app
    memorial = {
        "type": "taiyi.memorial",
        "title": "太医奏折",
        "summary": "太医巡诊,察得 1 处可调之症,详列于后。",
        "findings": [{"target": "src/tianshu/a.py", "hypothesis": "修正边界"}],
        "count": 1,
    }
    generate = AsyncMock(return_value=memorial)
    app.state.diagnostician.report = generate

    created = await client.post("/api/universes/taiyi/report")

    assert created.status_code == 200
    created_state = created.json()["data"]
    assert created_state["status"] == "ready"
    assert created_state["report"] == memorial
    assert created_state["generated_at"]
    generate.assert_awaited_once_with()

    fetched = await client.get("/api/universes/taiyi/report")
    assert fetched.status_code == 200
    assert fetched.json()["data"] == created_state
    generate.assert_awaited_once_with()


async def test_taiyi_report_post_is_blocked_in_eval_mode(client):
    app = client._transport.app
    app.state.settings.eval_mode = True
    generate = AsyncMock(side_effect=AssertionError("eval mode must not call the model"))
    app.state.diagnostician.report = generate

    resp = await client.post("/api/universes/taiyi/report")

    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "taiyi_generation_disabled_in_eval_mode"
    generate.assert_not_awaited()


async def test_diff_two_universes(client):
    mgr = client._transport.app.state.universe_manager
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")

    resp = await client.get(f"/api/universes/_diff?a={g['id']}&b={ch['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "personas" in body["data"]
    assert "skills" in body["data"]
    assert "config" in body["data"]
