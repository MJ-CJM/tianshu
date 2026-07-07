"""hongluisi engine-preferences API 校验测试。"""

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


@pytest.mark.integration
async def test_patch_rejects_unknown_fetch_engine(client):
    resp = await client.patch(
        "/api/hongluisi/engine-preferences",
        json={"fetch_chain": ["bogus_engine"], "search_provider": "duckduckgo"},
    )
    assert resp.status_code == 400
    assert "unknown fetch engine" in resp.json()["detail"]


@pytest.mark.integration
async def test_patch_accepts_free_engines(client):
    resp = await client.patch(
        "/api/hongluisi/engine-preferences",
        json={
            "fetch_chain": ["scrapling", "local"],
            "search_provider": "duckduckgo",
            "fallback_mode": "on_error_or_empty",
            "scrapling_dynamic_enabled": False,
            "scrapling_stealthy_enabled": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["search_provider"] == "duckduckgo"
    assert body["scrapling_dynamic_enabled"] is False
