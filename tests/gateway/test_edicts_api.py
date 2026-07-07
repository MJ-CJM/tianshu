"""POST /edicts/latest-memorials 批量端点：空列表 / 正常 / 超限三情形。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway.edicts_api import edicts_router
from tianshu.models import Edict, Memorial, TaskStatus


@pytest.fixture
def app(storage):
    app = FastAPI()
    app.include_router(edicts_router)
    app.state.storage = storage
    return app


def test_batch_latest_memorials_empty_list(app):
    with TestClient(app) as client:
        resp = client.post("/edicts/latest-memorials", json={"edict_ids": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == {}


def test_batch_latest_memorials_normal(app, storage):
    edict_with_memorial = Edict(goal="测试目标一")
    storage.save_edict(edict_with_memorial)
    memorial = Memorial(
        edict_id=edict_with_memorial.id,
        instruction="办理",
        status=TaskStatus.RUNNING,
    )
    storage.save_memorial(memorial)

    edict_without_memorial = Edict(goal="测试目标二")
    storage.save_edict(edict_without_memorial)

    with TestClient(app) as client:
        resp = client.post(
            "/edicts/latest-memorials",
            json={"edict_ids": [edict_with_memorial.id, edict_without_memorial.id]},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data[edict_with_memorial.id]["id"] == memorial.id
        assert data[edict_with_memorial.id]["status"] == "running"
        assert data[edict_without_memorial.id] is None


def test_batch_latest_memorials_exceeds_limit(app):
    edict_ids = [f"edict-{i}" for i in range(201)]
    with TestClient(app) as client:
        resp = client.post("/edicts/latest-memorials", json={"edict_ids": edict_ids})
        assert resp.status_code == 400
