"""Q7:平台级默认下沉全局设置——edict 创建用全局 AgentConfig 打底,表单只覆盖差异。"""

from __future__ import annotations

from unittest.mock import patch

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


async def _create(client, body):
    idempotency_key = f"q7:{body['goal']}"
    with patch("tianshu.executor.agent.LLMClient"):
        resp = await client.post(
            "/api/edicts",
            headers={"Idempotency-Key": idempotency_key},
            json={**body, "idempotency_key": idempotency_key},
        )
    assert resp.status_code == 202, resp.text
    return resp.json()["data"]


class TestGlobalDefaults:
    async def test_agent_config_exposes_new_defaults(self, client):
        data = (await client.get("/api/agent-config")).json()["data"]
        for k in (
            "agent_max_concurrency",
            "agent_retry_limit",
            "agent_token_budget",
            "agent_cost_budget_cny",
        ):
            assert k in data

    async def test_edict_inherits_global_defaults(self, client):
        # 设全局默认
        await client.put(
            "/api/agent-config",
            json={
                "agent_timeout_seconds": 600,
                "agent_max_concurrency": 4,
                "agent_cost_budget_cny": 5.0,
            },
        )
        # 创建 edict 不带 runtime → 继承全局默认
        edict = await _create(client, {"goal": "inherit defaults"})
        rt = edict["runtime"]
        assert rt["timeout_seconds"] == 600
        assert rt["max_concurrency"] == 4
        assert rt["cost_budget_cny"] == 5.0

    async def test_edict_runtime_overrides_global(self, client):
        await client.put("/api/agent-config", json={"agent_timeout_seconds": 600})
        # 表单显式给 timeout=120 → 覆盖全局默认;未给的 max_concurrency 仍用全局默认
        edict = await _create(client, {"goal": "override one", "runtime": {"timeout_seconds": 120}})
        rt = edict["runtime"]
        assert rt["timeout_seconds"] == 120  # 覆盖
        assert rt["max_iterations"] == 20  # 全局默认打底(未改)

    async def test_update_rejects_out_of_range(self, client):
        resp = await client.put("/api/agent-config", json={"agent_max_concurrency": 99})
        assert resp.status_code == 422  # ge=1 le=8

    async def test_update_rejects_unwired_keqing_gateway(self, client):
        resp = await client.put("/api/agent-config", json={"keqing_gateway_enabled": True})
        assert resp.status_code == 409
        assert "unavailable" in resp.json()["detail"]

    async def test_update_rejects_unwired_automatic_skill_review(self, client):
        current = await client.get("/api/agent-config")
        assert current.json()["data"]["skill_review_enabled"] is False

        resp = await client.put("/api/agent-config", json={"skill_review_enabled": True})

        assert resp.status_code == 409
        assert "unavailable" in resp.json()["detail"]
