"""MCP server REST API 端点集测。

启动整套 lifespan（包括 MCPManager），但 ``~/.tianshu/mcp_servers.yaml`` 大概
率不存在，所以默认无 server。我们通过 monkeypatch + 手喂 MCPConfig 来模拟。
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

from tianshu.app import create_app, lifespan
from tianshu.secrets.vault import reset_vault
from tianshu.tools.mcp.config import MCPConfig, MCPServerConfig


@pytest.fixture
async def client(monkeypatch):
    # 测试期把 MCP 重连参数压到最小，避免 broken stub 触发 60s 退避
    from tianshu.tools.mcp import client as mcp_client_module

    monkeypatch.setattr(mcp_client_module, "MAX_RECONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(mcp_client_module, "MAX_BACKOFF_SECONDS", 0)
    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", Fernet.generate_key().decode())
    reset_vault()

    app = create_app()
    try:
        async with lifespan(app):
            # 手动喂一份 config（不真实启动 server，仅用于 API serialization 测试）
            manager = app.state.mcp_manager
            manager._config = MCPConfig(
                mcp_servers={
                    "fixture": MCPServerConfig(
                        name="fixture",
                        transport="stdio",
                        command="echo",
                        args=["hi"],
                        enabled=True,
                        env={"TOKEN": "secret"},
                    ),
                    "remote": MCPServerConfig(
                        name="remote",
                        transport="streamable_http",
                        url="https://x.example.com/mcp",
                        enabled=False,
                        headers={"Authorization": "Bearer xxx"},
                    ),
                }
            )
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                yield c
    finally:
        reset_vault()


class TestListMCPServers:
    async def test_returns_all_servers(self, client):
        resp = await client.get("/api/mcp/servers")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        names = {s["name"] for s in body["data"]}
        assert names == {"fixture", "remote"}

    async def test_env_values_not_leaked(self, client):
        resp = await client.get("/api/mcp/servers")
        body = resp.json()
        for server in body["data"]:
            # stdio server 只回 env_keys（key 名可见），不回 value
            blob = str(server)
            assert "secret" not in blob, f"env value leaked: {blob}"
            # http server 只回 header_keys，不回 token value
            assert "xxx" not in blob, f"header value leaked: {blob}"
        # 但 key 名应该能看到（用于前端展示）
        fixture = next(s for s in body["data"] if s["name"] == "fixture")
        assert "TOKEN" in fixture["env_keys"]
        remote = next(s for s in body["data"] if s["name"] == "remote")
        assert "Authorization" in remote["header_keys"]

    async def test_disabled_server_marked(self, client):
        resp = await client.get("/api/mcp/servers")
        body = resp.json()
        remote = next(s for s in body["data"] if s["name"] == "remote")
        assert remote["enabled"] is False
        assert remote["status"] == "disabled"


class TestGetMCPServer:
    async def test_get_existing(self, client):
        resp = await client.get("/api/mcp/servers/fixture")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["name"] == "fixture"
        assert data["transport"] == "stdio"
        assert data["command"] == "echo"

    async def test_get_missing_404(self, client):
        resp = await client.get("/api/mcp/servers/nonexistent")
        assert resp.status_code == 404


class TestPatchMCPServer:
    async def test_disable_via_override(self, client):
        resp = await client.patch("/api/mcp/servers/fixture", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    async def test_patch_missing_404(self, client):
        resp = await client.patch("/api/mcp/servers/nonexistent", json={"enabled": False})
        assert resp.status_code == 404


class TestDeleteMCPOverride:
    async def test_delete(self, client):
        # 先 patch 一次写 override
        await client.patch("/api/mcp/servers/fixture", json={"enabled": False})
        # 再删除
        resp = await client.delete("/api/mcp/servers/fixture/override")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestListMCPServerTools:
    async def test_no_session_returns_empty(self, client):
        # fixture 没真实启动 → sessions 中没有，返回空
        resp = await client.get("/api/mcp/servers/fixture/tools")
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_missing_server_404(self, client):
        resp = await client.get("/api/mcp/servers/nonexistent/tools")
        assert resp.status_code == 404


class TestCreateMCPServer:
    async def test_create_stdio_validation_errors(self, client):
        # 缺 command
        resp = await client.post(
            "/api/mcp/servers",
            json={"name": "x", "transport": "stdio"},
        )
        assert resp.status_code == 400

    async def test_create_underscore_in_name_rejected(self, client):
        resp = await client.post(
            "/api/mcp/servers",
            json={
                "name": "bad_name",
                "transport": "stdio",
                "command": "echo",
            },
        )
        assert resp.status_code == 400

    async def test_create_http_missing_url(self, client):
        resp = await client.post(
            "/api/mcp/servers",
            json={"name": "y", "transport": "streamable_http"},
        )
        assert resp.status_code == 400

    async def test_create_invalid_tier(self, client):
        resp = await client.post(
            "/api/mcp/servers",
            json={
                "name": "z",
                "transport": "stdio",
                "command": "echo",
                "default_tier": 99,
            },
        )
        assert resp.status_code == 400

    async def test_create_conflict_with_yaml_server(self, client):
        # fixture 在 fixture client 里被 inject 进 manager.config
        resp = await client.post(
            "/api/mcp/servers",
            json={
                "name": "fixture",
                "transport": "stdio",
                "command": "echo",
            },
        )
        assert resp.status_code == 409

    async def test_create_persists_db_only_server(self, client):
        # 注：会触发 manager.start()，echo 命令不是合法 MCP server，会快速失败
        # 这是预期 — 我们验证 DB 落库 + reload 逻辑跑了
        resp = await client.post(
            "/api/mcp/servers",
            json={
                "name": "temp",
                "transport": "stdio",
                "command": "/bin/echo",
                "args": ["dummy"],
                "enabled": True,
                "tools_include": ["health_probe"],
                "default_tier": 0,
                "env": {"FOO": "bar"},
                "timeout": 5,
                "connect_timeout": 2,
            },
        )
        # 注意：reload 时可能因为子进程不是合法 MCP server 而 status=error，
        # 但创建本身要成功
        assert resp.status_code in (201,)
        # GET list 应能看到 temp（无论它最终连接成功与否）
        list_resp = await client.get("/api/mcp/servers")
        names = {s["name"] for s in list_resp.json()["data"]}
        assert "temp" in names
