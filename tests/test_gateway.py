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

    async def test_create_edict_with_cron_schedule_keeps_timezone(self, client):
        with patch("tianshu.executor.agent.LLMClient"):
            resp = await client.post(
                "/api/edicts",
                json={
                    "goal": "每天 11 点推送天气",
                    "schedule": {
                        "type": "cron",
                        "cron": "0 11 * * *",
                        "timezone": "Asia/Shanghai",
                    },
                },
            )
        assert resp.status_code == 202
        schedule = resp.json()["data"]["schedule"]
        assert schedule["type"] == "cron"
        assert schedule["cron"] == "0 11 * * *"
        assert schedule["timezone"] == "Asia/Shanghai"

    async def test_create_edict_with_once_schedule_keeps_at(self, client):
        with patch("tianshu.executor.agent.LLMClient"):
            resp = await client.post(
                "/api/edicts",
                json={
                    "goal": "明天 9 点提醒",
                    "schedule": {
                        "type": "once",
                        "at": "2026-12-01T09:00:00+08:00",
                    },
                },
            )
        assert resp.status_code == 202
        schedule = resp.json()["data"]["schedule"]
        assert schedule["type"] == "once"
        assert schedule["at"] is not None
        assert "2026-12-01" in schedule["at"]

    async def test_create_edict_once_naive_at_treated_as_shanghai(self, client):
        """无时区偏移的 at 应按北京时间解释，而非 UTC（否则偏移 8 小时）。"""
        from datetime import datetime

        with patch("tianshu.executor.agent.LLMClient"):
            resp = await client.post(
                "/api/edicts",
                json={
                    "goal": "今天下午两点二十二",
                    "schedule": {"type": "once", "at": "2026-05-21 14:22:00"},
                },
            )
        assert resp.status_code == 202
        at = resp.json()["data"]["schedule"]["at"]
        # 解析回来：应等于北京时间 14:22（= UTC 06:22），不是 UTC 14:22
        parsed = datetime.fromisoformat(at)
        assert parsed.utcoffset().total_seconds() == 8 * 3600
        assert parsed.hour == 14 and parsed.minute == 22

    async def test_create_edict_rejects_invalid_at(self, client):
        with patch("tianshu.executor.agent.LLMClient"):
            resp = await client.post(
                "/api/edicts",
                json={
                    "goal": "x",
                    "schedule": {"type": "once", "at": "not a datetime"},
                },
            )
        assert resp.status_code == 400
        assert "at" in resp.json()["detail"]


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
