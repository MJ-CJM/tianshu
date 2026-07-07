"""Backward compatibility tests — Phase 0 behavior preserved."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan
from tianshu.models import Edict, EdictStatus, Memorial, TaskStatus


class TestBackwardCompatImports:
    """Ensure old import paths still work."""

    def test_import_from_models(self):
        from tianshu.models import (
            Edict,
        )

        assert Edict is not None

    def test_import_from_agent_shim(self):
        from tianshu.agent import Agent, AgentResult

        assert Agent is not None
        assert AgentResult is not None

    def test_import_from_gateway_shim(self):
        from tianshu.gateway import gateway_router

        assert gateway_router is not None


class TestBackwardCompatModels:
    """Phase 0 model behavior preserved."""

    def test_edict_defaults_unchanged(self):
        e = Edict(goal="test")
        assert e.status == EdictStatus.OPEN
        assert e.title == ""
        assert e.context is None

    def test_memorial_defaults_unchanged(self):
        m = Memorial(edict_id="e1")
        assert m.status == TaskStatus.SUBMITTED
        assert m.usage.total_tokens == 0
        assert m.error is None
        assert m.result is None


class TestBackwardCompatAPI:
    """Phase 0 API endpoints still work."""

    @pytest.fixture
    async def client(self):
        app = create_app()
        async with lifespan(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                yield c

    async def test_health(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200

    async def test_create_edict(self, client):
        with patch("tianshu.executor.agent.LLMClient"):
            resp = await client.post("/api/edicts", json={"goal": "compat test"})
        assert resp.status_code == 202
        assert resp.json()["success"] is True

    async def test_list_edicts(self, client):
        resp = await client.get("/api/edicts")
        assert resp.status_code == 200

    async def test_config_endpoints(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200

        resp = await client.get("/api/configs")
        assert resp.status_code == 200
