"""Tests for POST /api/universes/{id}/promote-code endpoint.

Only error-path coverage: no real worktree / code variant is created.
Both cases error out before any deploy/restart side effects.
"""

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


async def test_promote_code_missing_universe(client):
    resp = await client.post("/api/universes/ghost-id/promote-code", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "promotion_preconditions_not_met"


async def test_promote_code_rejects_data_universe(client):
    await client.post("/api/universes/enable")
    listing = await client.get("/api/universes")
    champ = next(u for u in listing.json()["data"] if u["status"] == "champion")
    resp = await client.post(f"/api/universes/{champ['id']}/promote-code", json={})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "promotion_preconditions_not_met"
