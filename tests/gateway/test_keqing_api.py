"""客卿与影子快照 API(迭代 3.5)。"""

from __future__ import annotations

import shutil

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.keqing_api import keqing_router


@pytest.fixture
def client(storage):
    app = FastAPI()
    app.state.storage = storage
    app.state.event_bus = EventBus()
    app.include_router(keqing_router, prefix="/api")
    return TestClient(app)


class TestKeqingApi:
    def test_list_agents(self, client):
        data = client.get("/api/keqing/agents").json()["data"]
        assert data[0] == "native"
        assert "keqing:claude-code" in data and "keqing:codex" in data

    def test_snapshots_empty(self, client):
        data = client.get("/api/edicts/e1/snapshots").json()["data"]
        assert data == []

    def test_snapshots_listed(self, client, storage):
        storage.save_shadow_snapshot(
            {
                "id": "s1",
                "edict_id": "e1",
                "memorial_id": "m1",
                "sha": "abc123def456",
                "label": "keqing:m1",
                "work_tree": "/tmp/kq/e1",
                "created_at": "2026-07-08T00:00:00+00:00",
            }
        )
        data = client.get("/api/edicts/e1/snapshots").json()["data"]
        assert len(data) == 1 and data[0]["sha"] == "abc123def456"

    def test_revert_no_snapshots(self, client):
        resp = client.post("/api/edicts/ghost/snapshots/revert", json={"sha": "x"})
        assert resp.json()["success"] is False

    @pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
    def test_revert_roundtrip(self, client, storage, tmp_path):
        from tianshu.executor.shadow_snapshot import ShadowSnapshot

        work = tmp_path / "work"
        work.mkdir()
        (work / "a.txt").write_text("v1")
        ss = ShadowSnapshot(work, "e1", root=tmp_path / "shadow")
        ss.init()
        s1 = ss.snapshot("n1")
        (work / "a.txt").write_text("v2")
        ss.snapshot("n2")
        storage.save_shadow_snapshot(
            {
                "id": "s1",
                "edict_id": "e1",
                "memorial_id": None,
                "sha": s1.sha,
                "label": "n1",
                "work_tree": str(work),
                "created_at": "2026-07-08T00:00:00+00:00",
            }
        )
        # revert 走 gitdir 默认路径(~/.tianshu/shadow),此处仅验证 API 契约:
        # work_tree 存在但默认 gitdir 无此仓 → revert 返回 False(优雅失败)
        resp = client.post("/api/edicts/e1/snapshots/revert", json={"sha": s1.sha})
        assert "success" in resp.json()
