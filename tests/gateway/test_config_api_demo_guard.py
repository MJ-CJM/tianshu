"""demo profile 下 provider 配置写面守卫。

demo 状态是 runtime-only 的：providers/llm_configs 表零改动、退出 demo 后
live 配置原样恢复。因此 demo 档位下 provider 配置写 API 一律 409，
且 ProviderManager 的同步路径有防御纵深守卫。
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan


@pytest.fixture
async def demo_client(tmp_path, monkeypatch):
    monkeypatch.setenv("TIANSHU_STARTUP_PROFILE", "demo")
    monkeypatch.setenv("TIANSHU_RUNTIME_PERSONAS_DIR", str(tmp_path / "runtime-personas"))
    monkeypatch.setenv("TIANSHU_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("TIANSHU_DB_PATH", str(tmp_path / "demo.sqlite3"))
    app = create_app()
    async with lifespan(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c, app


async def test_demo_put_named_config_empty_body_is_409_and_never_persists(demo_client):
    """审查复现场景：空 body PUT 曾把 ('demo','mock/tianshu-demo') 写进 providers 表。"""
    c, app = demo_client
    resp = await c.put("/api/configs/anything", json={})
    assert resp.status_code == 409
    storage = app.state.storage
    rows = storage.list_providers()
    assert all(r.get("name") != "demo" for r in rows), rows


async def test_demo_provider_config_write_surface_is_read_only(demo_client):
    c, app = demo_client
    legacy = await c.put("/api/config", json={"model": "gpt-x"})
    assert legacy.status_code == 409
    create = await c.post("/api/configs", json={"name": "n1", "model": "m1", "api_key": "k"})
    assert create.status_code == 409
    delete = await c.delete("/api/configs/whatever")
    assert delete.status_code == 409
    activate = await c.put("/api/configs/whatever/activate")
    assert activate.status_code == 409


async def test_demo_persona_llm_config_binding_still_validates_existence(demo_client):
    """get_config 掩蔽不得伪造存在性：绑定不存在的 config 名必须仍被 400 拒绝。"""
    c, app = demo_client
    resp = await c.put("/api/personas/bingbu", json={"llm_config_name": "definitely-missing"})
    assert resp.status_code == 400, resp.text


async def test_demo_manager_sync_paths_are_noop(demo_client):
    """防御纵深：即使绕过 API 直接调用 manager 同步路径，demo 下也零写入。"""
    c, app = demo_client
    pm = app.state.provider_manager
    cm = app.state.config_manager
    state = cm.state
    pm.sync_from_config(state)
    pm.unregister("anything")
    rows = app.state.storage.list_providers()
    assert all(r.get("name") != "demo" for r in rows), rows
