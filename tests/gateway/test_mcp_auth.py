"""MCP parent-boundary authentication and scope contracts."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.application.edicts import EdictApplicationService
from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.mcp_server import build_mcp_server
from tianshu.models.principal import Principal

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "Host": "tianshu.example.com",
    "X-Forwarded-Proto": "https",
}

_INIT_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "pytest", "version": "0"},
}


def _rpc(method: str, params: dict | None = None, id_: int = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _settings() -> TianshuSettings:
    token_hash = hashlib.sha256(b"bootstrap-token-for-tests").hexdigest()
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url="https://tianshu.example.com",
        allowed_hosts="tianshu.example.com",
        allowed_origins="https://tianshu.example.com",
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=f"sha256:{token_hash}",
    )


@pytest.fixture
def secure_mcp(storage):
    settings = _settings()
    holder: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with holder["mcp"].session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.storage = storage
    app.state.event_bus = EventBus()
    app.state.auth_service = AuthService(storage, settings)
    app.state.edict_application_service = EdictApplicationService(storage)
    app.state.public_webhook_paths = set()
    holder["mcp"] = build_mcp_server(app)
    app.mount("/mcp", holder["mcp"].streamable_http_app())
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        yield client, app, holder["mcp"]


def test_mcp_rejects_anonymous_before_json_rpc_dispatch(secure_mcp) -> None:
    client, _, _ = secure_mcp
    response = client.post(
        "/mcp/",
        json=_rpc("initialize", _INIT_PARAMS),
        headers=_MCP_HEADERS,
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "authentication_required"


def test_mcp_enables_native_dns_rebinding_protection(secure_mcp) -> None:
    _, _, mcp = secure_mcp

    assert mcp.settings.transport_security.enable_dns_rebinding_protection is True
    assert mcp.settings.transport_security.allowed_hosts == ["tianshu.example.com"]
    assert mcp.settings.transport_security.allowed_origins == ["https://tianshu.example.com"]


def test_mcp_read_scope_cannot_submit_and_submit_scope_preserves_actor(secure_mcp) -> None:
    client, app, _ = secure_mcp
    service = app.state.auth_service
    reader = service.issue_pat(
        Principal(
            id="service:mcp-reader",
            kind="service",
            display_name="MCP Reader",
            scopes=frozenset({"mcp:read"}),
        ),
        label="MCP read only",
        scopes=frozenset({"mcp:read"}),
    )
    submitter = service.issue_pat(
        Principal(
            id="service:mcp-submit",
            kind="service",
            display_name="MCP Submitter",
            scopes=frozenset({"mcp:read", "mcp:submit"}),
        ),
        label="MCP submit",
        scopes=frozenset({"mcp:read", "mcp:submit"}),
    )

    read_headers = {**_MCP_HEADERS, "Authorization": f"Bearer {reader.raw_token}"}
    read_list = client.post(
        "/mcp/",
        json=_rpc("tools/list", {}),
        headers=read_headers,
    )
    denied = client.post(
        "/mcp/",
        json=_rpc(
            "tools/call",
            {"name": "submit_edict", "arguments": {"goal": "must be denied"}},
            id_=2,
        ),
        headers=read_headers,
    )

    submit_headers = {**_MCP_HEADERS, "Authorization": f"Bearer {submitter.raw_token}"}
    accepted = client.post(
        "/mcp/",
        json=_rpc(
            "tools/call",
            {"name": "submit_edict", "arguments": {"goal": "preserve actor"}},
            id_=3,
        ),
        headers=submit_headers,
    )

    assert read_list.status_code == 200
    assert denied.status_code == 200
    assert denied.json()["result"]["isError"] is True
    assert "mcp:submit" in denied.json()["result"]["content"][0]["text"]
    assert accepted.status_code == 200
    assert accepted.json()["result"]["isError"] is False
    accepted_payload = json.loads(accepted.json()["result"]["content"][0]["text"])
    edict_id = accepted_payload["edict_id"]
    assert app.state.storage.get_edict(edict_id).submitter == "service:mcp-submit"


def test_mcp_submit_only_scope_can_submit_but_cannot_read(secure_mcp) -> None:
    client, app, _ = secure_mcp
    issued = app.state.auth_service.issue_pat(
        Principal(
            id="service:mcp-submit-only",
            kind="service",
            display_name="MCP Submit Only",
            scopes=frozenset({"mcp:submit"}),
        ),
        label="MCP submit only",
        scopes=frozenset({"mcp:submit"}),
    )
    headers = {**_MCP_HEADERS, "Authorization": f"Bearer {issued.raw_token}"}

    submitted = client.post(
        "/mcp/",
        json=_rpc(
            "tools/call",
            {"name": "submit_edict", "arguments": {"goal": "submit only"}},
        ),
        headers=headers,
    )
    denied_read = client.post(
        "/mcp/",
        json=_rpc(
            "tools/call",
            {"name": "list_recent_edicts", "arguments": {}},
            id_=2,
        ),
        headers=headers,
    )

    assert submitted.status_code == 200
    assert submitted.json()["result"]["isError"] is False
    payload = json.loads(submitted.json()["result"]["content"][0]["text"])
    assert app.state.storage.get_edict(payload["edict_id"]).submitter == ("service:mcp-submit-only")
    assert denied_read.status_code == 200
    assert denied_read.json()["result"]["isError"] is True
    assert "mcp:read" in denied_read.json()["result"]["content"][0]["text"]


def test_mcp_request_id_is_the_submission_idempotency_key(secure_mcp) -> None:
    client, app, _ = secure_mcp
    issued = app.state.auth_service.issue_pat(
        Principal(
            id="service:mcp-idempotent",
            kind="service",
            display_name="MCP Idempotent",
            scopes=frozenset({"mcp:submit"}),
        ),
        label="MCP idempotent",
        scopes=frozenset({"mcp:submit"}),
    )
    headers = {**_MCP_HEADERS, "Authorization": f"Bearer {issued.raw_token}"}

    first = client.post(
        "/mcp/",
        json=_rpc(
            "tools/call",
            {"name": "submit_edict", "arguments": {"goal": "same MCP request"}},
            id_=77,
        ),
        headers=headers,
    )
    retry = client.post(
        "/mcp/",
        json=_rpc(
            "tools/call",
            {"name": "submit_edict", "arguments": {"goal": "same MCP request"}},
            id_=77,
        ),
        headers=headers,
    )
    conflict = client.post(
        "/mcp/",
        json=_rpc(
            "tools/call",
            {"name": "submit_edict", "arguments": {"goal": "different MCP request"}},
            id_=77,
        ),
        headers=headers,
    )

    first_payload = json.loads(first.json()["result"]["content"][0]["text"])
    retry_payload = json.loads(retry.json()["result"]["content"][0]["text"])
    assert first_payload["edict_id"] == retry_payload["edict_id"]
    assert retry_payload["deduplicated"] is True
    assert conflict.json()["result"]["isError"] is True
