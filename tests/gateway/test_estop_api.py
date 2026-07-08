"""分级急停 API(迭代 3「深防御」)——engage/resume/status + 事件留痕。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.estop_api import estop_router
from tianshu.security.estop import EstopManager


@pytest.fixture
def client(storage):
    app = FastAPI()
    app.state.storage = storage
    app.state.event_bus = EventBus()
    app.state.estop_manager = EstopManager(storage)
    app.include_router(estop_router, prefix="/api")
    return TestClient(app)


class TestEstopApi:
    def test_status_default(self, client):
        data = client.get("/api/estop").json()["data"]
        assert data["engaged"] is False
        assert data["available"] is True

    def test_engage_kill_all(self, client):
        resp = client.post("/api/estop/engage", json={"kill_all": True, "reason": "drill"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["kill_all"] is True and data["engaged"] is True
        # status 反映
        assert client.get("/api/estop").json()["data"]["kill_all"] is True

    def test_engage_freeze_and_resume(self, client):
        client.post("/api/estop/engage", json={"freeze_tools": ["shell_exec"]})
        assert client.get("/api/estop").json()["data"]["frozen_tools"] == ["shell_exec"]
        client.post("/api/estop/resume", json={"all_clear": True})
        assert client.get("/api/estop").json()["data"]["engaged"] is False

    def test_resume_selective(self, client):
        client.post("/api/estop/engage", json={"kill_all": True, "network_kill": True})
        client.post("/api/estop/resume", json={"kill_all": True})
        data = client.get("/api/estop").json()["data"]
        assert data["kill_all"] is False and data["network_kill"] is True

    def test_unavailable_when_no_manager(self, storage):
        app = FastAPI()
        app.state.storage = storage
        app.include_router(estop_router, prefix="/api")
        c = TestClient(app)
        assert c.get("/api/estop").json()["data"]["available"] is False
