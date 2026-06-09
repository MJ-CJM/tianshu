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


async def test_propose_code_requires_fields(client):
    resp = await client.post("/api/universes/propose-code", json={})
    assert resp.status_code == 400


async def test_propose_code_disabled_by_default(client):
    resp = await client.post(
        "/api/universes/propose-code",
        json={"target_path": "src/tianshu/planner/x.py", "hypothesis": "improve"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "disabled"


async def test_eval_runs_any_id_returns_200_list(client):
    """GET /eval-runs for any id returns 200 with a list (empty is fine)."""
    resp = await client.get("/api/universes/nonexistent-id/eval-runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


async def test_eval_runs_after_enable_returns_list(client):
    """After enabling, champion exists; its eval-runs list is empty but valid."""
    await client.post("/api/universes/enable")
    listing = await client.get("/api/universes")
    champ = next(u for u in listing.json()["data"] if u["status"] == "champion")
    resp = await client.get(f"/api/universes/{champ['id']}/eval-runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)


async def test_code_diff_data_universe_returns_400(client):
    """code-diff on a genesis/data universe (no code_ref) must return 400."""
    await client.post("/api/universes/enable")
    listing = await client.get("/api/universes")
    champ = next(u for u in listing.json()["data"] if u["status"] == "champion")
    resp = await client.get(f"/api/universes/{champ['id']}/code-diff")
    assert resp.status_code == 400
