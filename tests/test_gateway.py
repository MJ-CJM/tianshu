"""Tests for Gateway API endpoints."""

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


class TestHealthEndpoint:
    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestEdictEndpoints:
    async def test_create_edict(self, client):
        with patch("tianshu.executor.agent.LLMClient"):
            resp = await client.post(
                "/api/edicts",
                json={"goal": "test goal"},
            )
        assert resp.status_code == 202
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["goal"] == "test goal"

    async def test_list_edicts(self, client):
        resp = await client.get("/api/edicts")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_get_edict_not_found(self, client):
        resp = await client.get("/api/edicts/nonexistent")
        assert resp.status_code == 404

    async def test_create_and_get_edict(self, client):
        with patch("tianshu.executor.agent.LLMClient"):
            create_resp = await client.post(
                "/api/edicts",
                json={"goal": "find me"},
            )
        edict_id = create_resp.json()["data"]["id"]

        get_resp = await client.get(f"/api/edicts/{edict_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["data"]["goal"] == "find me"

    async def test_create_edict_ignores_schedule_field(self, client):
        """颁发即时化：即使带 schedule 字段也被忽略（edict 恒 immediate）；定时改用 schedule_edict。"""
        with patch("tianshu.executor.agent.LLMClient"):
            resp = await client.post(
                "/api/edicts",
                json={
                    "goal": "每天 11 点推送天气",
                    "schedule": {"type": "cron", "cron": "0 11 * * *"},
                },
            )
        assert resp.status_code == 202
        assert resp.json()["data"]["schedule"]["type"] == "immediate"


class TestMemorialEndpoints:
    async def test_list_memorials(self, client):
        resp = await client.get("/api/memorials")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestConfigEndpoints:
    async def test_get_config(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        assert "model" in resp.json()["data"]

    async def test_get_agent_config(self, client):
        resp = await client.get("/api/agent-config")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "agent_max_iterations" in data

    async def test_list_configs(self, client):
        resp = await client.get("/api/configs")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "configs" in data
        assert "active_name" in data


class TestDecreeEndpoints:
    async def test_create_decree_invalid_memorial(self, client):
        resp = await client.post(
            "/api/decrees",
            json={"memorial_id": "nonexistent", "action": "approve"},
        )
        assert resp.status_code == 404
