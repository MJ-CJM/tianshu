"""天枢 MCP server 集成测试——stateless streamable HTTP,JSON-RPC 直连。

治理边界锚点:只暴露提交+只读 5 个 tools,批红类写操作不得出现在 tools/list。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.mcp_server import build_mcp_server

_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _rpc(method: str, params: dict | None = None, id_: int = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return body


_INIT_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "0"},
}


@pytest.fixture
def mcp_client(storage):
    """独立宿主 app:只 wire storage/event_bus,lifespan 只跑 MCP session manager。"""
    mcp_holder: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_holder["mcp"].session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    settings = TianshuSettings(
        _env_file=None,
        host="127.0.0.1",
        allowed_hosts="testserver",
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.event_bus = EventBus()
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    mcp_holder["mcp"] = build_mcp_server(app)
    app.mount("/mcp", mcp_holder["mcp"].streamable_http_app())
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)

    with TestClient(app) as client:
        yield client


def _call_tool(client: TestClient, name: str, arguments: dict | None = None) -> dict:
    resp = client.post(
        "/mcp/",
        json=_rpc("tools/call", {"name": name, "arguments": arguments or {}}, id_=3),
        headers=_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "error" not in payload, payload
    import json

    return json.loads(payload["result"]["content"][0]["text"])


class TestMCPServer:
    def test_initialize(self, mcp_client):
        resp = mcp_client.post("/mcp/", json=_rpc("initialize", _INIT_PARAMS), headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["result"]["serverInfo"]["name"] == "tianshu"

    def test_tools_list_governance_boundary(self, mcp_client):
        resp = mcp_client.post("/mcp/", json=_rpc("tools/list", {}), headers=_HEADERS)
        assert resp.status_code == 200, resp.text
        names = {t["name"] for t in resp.json()["result"]["tools"]}
        assert names == {
            "submit_edict",
            "get_edict_status",
            "get_memorial",
            "list_recent_edicts",
            "list_pending_approvals",
        }
        # 治理边界:任何批红/decree 写操作不得暴露给 MCP 宿主
        assert not any("decree" in n or "approve" in n.replace("approvals", "") for n in names)

    def test_submit_and_track_edict(self, mcp_client):
        submitted = _call_tool(
            mcp_client, "submit_edict", {"goal": "写一份周报", "context": "测试上下文"}
        )
        assert submitted["status"] == "submitted"
        edict_id = submitted["edict_id"]

        status = _call_tool(mcp_client, "get_edict_status", {"edict_id": edict_id})
        assert status["edict_id"] == edict_id
        assert status["memorials"][0]["memorial_id"] == submitted["memorial_id"]

        memorial = _call_tool(mcp_client, "get_memorial", {"memorial_id": submitted["memorial_id"]})
        assert memorial["memorial_id"] == submitted["memorial_id"]

        recent = _call_tool(mcp_client, "list_recent_edicts", {"limit": 5})
        assert recent["total"] >= 1
        assert any(e["edict_id"] == edict_id for e in recent["edicts"])

    def test_unknown_ids_return_error_payload(self, mcp_client):
        missing = _call_tool(mcp_client, "get_edict_status", {"edict_id": "nope"})
        assert "not found" in missing["error"]

    def test_pending_approvals_empty(self, mcp_client):
        pending = _call_tool(mcp_client, "list_pending_approvals")
        assert pending == {"total": 0, "pending": []}
