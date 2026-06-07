"""Integration tests for /api/universes endpoints."""
from __future__ import annotations

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


async def test_switch_makes_challenger_champion(client):
    mgr = client._transport.app.state.universe_manager
    g = mgr.ensure_genesis()
    ch = mgr.branch(g["id"], "exp")

    resp = await client.post(f"/api/universes/{ch['id']}/switch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["status"] == "champion"

    # Old champion should now be challenger
    all_universes = (await client.get("/api/universes")).json()["data"]
    statuses = {u["id"]: u["status"] for u in all_universes}
    assert statuses[g["id"]] == "challenger"
    assert statuses[ch["id"]] == "champion"


async def test_switch_nonexistent_universe(client):
    resp = await client.post("/api/universes/ghost-id/switch")
    assert resp.status_code == 400


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
