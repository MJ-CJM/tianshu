"""全局工作区边界与越界白名单的写入守卫。

`allowed_paths` 决定官员能否走出工作区，`workspace_dir` 决定工作区本身有多大。
两者都能在网页上改，配错任一个都等于取消隔离，故写入面必须拦住危险取值——
运行时静默失效（相对 glob）或静默放行一切（根级 glob）都是不可接受的。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(tmp_path / "t.sqlite3"))
    monkeypatch.setenv("TIANSHU_WORKSPACE_DIR", str(tmp_path))
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


async def _make_persona(c, **overrides):
    """建官员前先确保部门存在——不依赖 seed 的部门 id。"""
    await c.post("/api/departments", json={"id": "testdept", "name": "测试部"})
    body = {
        "id": "tester",
        "name": "测试官",
        "department": "testdept",
        **overrides,
    }
    return await c.post("/api/personas", json=body)


class TestAllowedPathsWriteGuard:
    @pytest.mark.parametrize(
        "bad",
        ["/**", "/*", "/", "**/*", "docs/**", "~/secrets/**"],
        ids=["root-glob", "root-star", "root", "relative-recursive", "relative", "tilde"],
    )
    async def test_dangerous_glob_rejected_on_create(self, client, bad):
        c, _ = client
        resp = await _make_persona(c, allowed_paths=[bad])
        assert resp.status_code == 400, resp.text
        assert "不合法" in resp.json()["detail"]

    async def test_credential_dir_rejected(self, client):
        c, _ = client
        resp = await _make_persona(c, allowed_paths=["/Users/someone/.ssh/**"])
        assert resp.status_code == 400
        assert "凭证目录" in resp.json()["detail"]

    async def test_legitimate_absolute_glob_accepted(self, client, tmp_path):
        c, _ = client
        resp = await _make_persona(c, allowed_paths=[f"{tmp_path}/shared/**"])
        assert resp.status_code == 201, resp.text

    async def test_guard_also_applies_to_update(self, client, tmp_path):
        c, _ = client
        assert (await _make_persona(c, allowed_paths=[f"{tmp_path}/shared/**"])).status_code == 201
        resp = await c.put("/api/personas/tester", json={"allowed_paths": ["/**"]})
        assert resp.status_code == 400, "update 面若不校验，改一次就能绕过 create 的守卫"


class TestWorkspaceDirConfig:
    async def test_get_reports_effective_root(self, client, tmp_path):
        c, _ = client
        resp = await c.get("/api/workspace")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["effective"] == str(tmp_path.resolve())
        assert data["pending_restart"] is False

    async def test_saved_value_is_persisted_and_marked_pending(self, client, tmp_path):
        c, app = client
        target = tmp_path / "elsewhere"
        target.mkdir()

        resp = await c.put("/api/workspace", json={"workspace_dir": str(target)})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["pending_restart"] is True, "换了目录必须提示重启"

        stored = app.state.storage.get_app_setting("workspace_dir")
        assert stored == str(target.resolve())

    async def test_running_settings_untouched_until_restart(self, client, tmp_path):
        """写入不得热改运行态——工具注册时已闭包捕获旧路径，热改会造成两处不一致。"""
        c, app = client
        target = tmp_path / "elsewhere"
        target.mkdir()
        before = app.state.settings.workspace_dir

        await c.put("/api/workspace", json={"workspace_dir": str(target)})
        assert app.state.settings.workspace_dir == before

    @pytest.mark.parametrize(
        "bad,reason",
        [("/", "根目录"), ("relative/path", "绝对路径"), ("", "不能为空")],
        ids=["filesystem-root", "relative", "empty"],
    )
    async def test_dangerous_workspace_rejected(self, client, bad, reason):
        c, _ = client
        resp = await c.put("/api/workspace", json={"workspace_dir": bad})
        assert resp.status_code == 400
        assert reason in resp.json()["detail"]

    async def test_nonexistent_dir_rejected(self, client, tmp_path):
        c, _ = client
        resp = await c.put("/api/workspace", json={"workspace_dir": str(tmp_path / "nope")})
        assert resp.status_code == 400
        assert "目录不存在" in resp.json()["detail"]


class TestPersonaWorkspaceDirWriteGuard:
    """官员专属工作区（#33）写入校验——与全局工作区同一套规则。"""

    async def test_valid_dir_stored_resolved(self, client, tmp_path):
        c, _ = client
        ws = tmp_path / "ws-smg"
        ws.mkdir()
        resp = await _make_persona(c, workspace_dir=str(ws))
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["workspace_dir"] == str(ws.resolve())

    async def test_empty_means_main_workspace(self, client):
        c, _ = client
        resp = await _make_persona(c, workspace_dir="")
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["workspace_dir"] == ""

    @pytest.mark.parametrize(
        "bad,reason",
        [("/", "根目录"), ("relative/ws", "绝对路径")],
        ids=["filesystem-root", "relative"],
    )
    async def test_dangerous_workspace_rejected(self, client, bad, reason):
        c, _ = client
        resp = await _make_persona(c, workspace_dir=bad)
        assert resp.status_code == 400
        assert reason in resp.json()["detail"]

    async def test_nonexistent_dir_rejected(self, client, tmp_path):
        c, _ = client
        resp = await _make_persona(c, workspace_dir=str(tmp_path / "nope"))
        assert resp.status_code == 400
        assert "目录不存在" in resp.json()["detail"]

    async def test_update_surface_also_guarded(self, client, tmp_path):
        c, _ = client
        assert (await _make_persona(c)).status_code == 201
        resp = await c.put("/api/personas/tester", json={"workspace_dir": "relative/ws"})
        assert resp.status_code == 400, "update 面不校验的话改一次就绕过"
