"""MCP configuration writes are admin-only, audited, and fail closed."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.config import TianshuSettings
from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.mcp_api import mcp_router
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.tools.mcp.manager import MCPManager
from tianshu.tools.registry import ToolRegistry

_ADMIN_TOKEN = "bootstrap-token-for-mcp-admin-tests"
_BASE_URL = "https://tianshu.example.com"


def _settings() -> TianshuSettings:
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=_BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=_BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=("sha256:" + hashlib.sha256(_ADMIN_TOKEN.encode()).hexdigest()),
    )


def _app(storage, tmp_path: Path, *, allowlist: str = "approved") -> FastAPI:
    settings = _settings()
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    app.state.tool_registry = ToolRegistry()
    app.state.mcp_manager = MCPManager(
        app.state.tool_registry,
        ExecutionGateway(),
        security_mode="secure-remote",
        config_path=tmp_path / "missing-mcp.yaml",
        storage=storage,
        allowlist=allowlist,
    )
    app.state.mcp_manager.load_config()
    app.state.mcp_manager.start = AsyncMock()
    app.state.mcp_manager.shutdown = AsyncMock()
    app.include_router(mcp_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(
        app,
        base_url=_BASE_URL,
        client=("127.0.0.1", 41000),
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _api_token(app: FastAPI) -> str:
    issued = app.state.auth_service.issue_pat(
        Principal(
            id="service:mcp-api-reader",
            kind=PrincipalKind.SERVICE,
            display_name="MCP API Reader",
            scopes=frozenset({"api"}),
        ),
        label="mcp-api-only",
        scopes=frozenset({"api"}),
    )
    return issued.raw_token


def _admin_token(app: FastAPI) -> str:
    issued = app.state.auth_service.issue_pat(
        Principal(
            id="service:mcp-admin",
            kind=PrincipalKind.SERVICE,
            display_name="MCP Admin",
            scopes=frozenset({"admin"}),
        ),
        label="mcp-admin-only",
        scopes=frozenset({"admin"}),
    )
    return issued.raw_token


def _seed_disabled_stdio(storage, name: str = "approved") -> None:
    storage.upsert_mcp_override(
        name,
        enabled=False,
        transport="stdio",
        command="python",
        tools_include=["approved_tool"],
    )


def _audit_request(action: str, *, outcome: str = "succeeded") -> AppendSystemAuditRequest:
    return AppendSystemAuditRequest(
        correlation_id="mcp-config-atomicity-test",
        actor_digest="a" * 64,
        action=action,
        outcome=outcome,  # type: ignore[arg-type]
        reason_code="policy_allowed",
        subject_kind="mcp_server",
        subject_digest="b" * 64,
        metadata={},
    )


def test_mcp_safe_get_is_api_but_all_writes_are_admin(
    storage,
    tmp_path: Path,
) -> None:
    _seed_disabled_stdio(storage)
    app = _app(storage, tmp_path)
    app.state.mcp_manager.load_config()
    api_token = _api_token(app)

    write_requests = (
        ("POST", "/api/mcp/servers", {"name": "new", "transport": "stdio", "command": "python"}),
        ("PATCH", "/api/mcp/servers/approved", {"default_tier": 1}),
        ("DELETE", "/api/mcp/servers/approved/override", None),
        ("POST", "/api/mcp/reload", None),
    )
    with _client(app) as client:
        anonymous = client.get("/api/mcp/servers")
        anonymous_writes = [
            client.request(method, path, json=body) for method, path, body in write_requests
        ]
        api_get = client.get("/api/mcp/servers", headers=_headers(api_token))
        denied_writes = [
            client.request(method, path, json=body, headers=_headers(api_token))
            for method, path, body in write_requests
        ]
        admin_get = client.get("/api/mcp/servers", headers=_headers(_ADMIN_TOKEN))

    assert anonymous.status_code == 401
    assert [response.status_code for response in anonymous_writes] == [401, 401, 401, 401]
    assert api_get.status_code == 200
    assert admin_get.status_code == 200
    assert [response.status_code for response in denied_writes] == [403, 403, 403, 403]
    assert all(
        response.json()["error"]["code"] == "insufficient_scope" for response in denied_writes
    )
    assert {row["name"] for row in storage.list_mcp_overrides()} == {"approved"}


def test_admin_only_scope_keeps_read_and_write_access(storage, tmp_path: Path) -> None:
    app = _app(storage, tmp_path)
    admin_token = _admin_token(app)

    with _client(app) as client:
        read = client.get("/api/mcp/servers", headers=_headers(admin_token))
        write = client.post("/api/mcp/reload", headers=_headers(admin_token))

    assert (read.status_code, write.status_code) == (200, 200)


@pytest.mark.parametrize(
    ("payload", "secret_sentinel"),
    [
        (
            {
                "name": "approved",
                "transport": "stdio",
                "command": "SECRET_COMMAND_MUST_NOT_REACH_AUDIT",
                "tools_include": ["approved_tool"],
            },
            "SECRET_COMMAND_MUST_NOT_REACH_AUDIT",
        ),
        (
            {
                "name": "approved",
                "transport": "streamable_http",
                "url": "https://SECRET_REMOTE_MUST_NOT_REACH_AUDIT.invalid/mcp",
            },
            "https://SECRET_REMOTE_MUST_NOT_REACH_AUDIT.invalid/mcp",
        ),
    ],
    ids=["stdio", "streamable-http"],
)
def test_admin_create_defaults_disabled_and_writes_hash_only_atomic_audit(
    storage,
    tmp_path: Path,
    payload: dict[str, object],
    secret_sentinel: str,
) -> None:
    app = _app(storage, tmp_path)

    with _client(app) as client:
        response = client.post(
            "/api/mcp/servers",
            headers=_headers(_ADMIN_TOKEN),
            json=payload,
        )

    assert response.status_code == 201
    [row] = storage.list_mcp_overrides()
    assert row["enabled"] is False
    [event] = [
        event for event in storage.list_system_audit() if event.action == "mcp.config.created"
    ]
    assert event.outcome == "succeeded"
    assert event.reason_code == "policy_allowed"
    assert event.correlation_id == response.headers["x-correlation-id"]
    assert event.actor_digest == hashlib.sha256(b"user:owner").hexdigest()
    assert event.subject_digest == hashlib.sha256(str(payload["name"]).encode()).hexdigest()
    assert event.metadata == {}
    serialized = repr(event.model_dump(mode="json"))
    assert str(payload["name"]) not in serialized
    assert secret_sentinel not in serialized


@pytest.mark.parametrize(
    ("payload", "allowlist", "reason_code"),
    [
        (
            {
                "name": "remote-sentinel",
                "transport": "streamable_http",
                "url": "https://SECRET_REMOTE.invalid/mcp",
                "enabled": True,
            },
            "remote-sentinel",
            "trusted_egress_unavailable",
        ),
        (
            {
                "name": "stdio-sentinel",
                "transport": "stdio",
                "command": "SECRET_COMMAND",
                "enabled": True,
            },
            "stdio-sentinel",
            "approved_tools_required",
        ),
        (
            {
                "name": "not-listed",
                "transport": "stdio",
                "command": "SECRET_COMMAND",
                "tools_include": ["approved_tool"],
                "enabled": True,
            },
            "different-server",
            "server_not_allowlisted",
        ),
    ],
)
def test_admin_create_admission_denial_is_stable_redacted_and_audited(
    storage,
    tmp_path: Path,
    payload: dict[str, object],
    allowlist: str,
    reason_code: str,
) -> None:
    app = _app(storage, tmp_path, allowlist=allowlist)

    with _client(app) as client:
        response = client.post(
            "/api/mcp/servers",
            headers=_headers(_ADMIN_TOKEN),
            json=payload,
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {"code": "mcp_admission_denied", "reason_code": reason_code}
    }
    assert storage.list_mcp_overrides() == []
    [event] = [
        event for event in storage.list_system_audit() if event.action == "mcp.admission.denied"
    ]
    assert event.outcome == "denied"
    assert event.reason_code == reason_code
    assert event.correlation_id == response.headers["x-correlation-id"]
    assert event.actor_digest == hashlib.sha256(b"user:owner").hexdigest()
    assert event.subject_digest == hashlib.sha256(str(payload["name"]).encode()).hexdigest()
    assert event.metadata == {}
    serialized = repr(event.model_dump(mode="json"))
    for value in payload.values():
        if isinstance(value, str):
            assert value not in serialized


def test_admin_patch_and_delete_emit_action_specific_audits(storage, tmp_path: Path) -> None:
    _seed_disabled_stdio(storage)
    app = _app(storage, tmp_path)
    app.state.mcp_manager.load_config()

    with _client(app) as client:
        patched = client.patch(
            "/api/mcp/servers/approved",
            headers=_headers(_ADMIN_TOKEN),
            json={"default_tier": 1},
        )
        deleted = client.delete(
            "/api/mcp/servers/approved/override",
            headers=_headers(_ADMIN_TOKEN),
        )

    assert (patched.status_code, deleted.status_code) == (200, 200)
    events = [
        event for event in storage.list_system_audit() if event.action.startswith("mcp.config.")
    ]
    assert [event.action for event in events] == ["mcp.config.updated", "mcp.config.deleted"]
    assert [event.correlation_id for event in events] == [
        patched.headers["x-correlation-id"],
        deleted.headers["x-correlation-id"],
    ]
    assert all(event.metadata == {} for event in events)


@pytest.mark.parametrize("mutation", ["create", "update", "delete"])
def test_mcp_config_mutation_rolls_back_when_audit_append_fails(
    storage,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    import tianshu.storage.config_repo as config_repo

    if mutation in {"update", "delete"}:
        _seed_disabled_stdio(storage)
    before = storage.list_mcp_overrides()

    def _raising_audit(*_: object, **__: object) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(config_repo, "_append_system_audit_unlocked", _raising_audit)
    with pytest.raises(RuntimeError, match="injected audit failure"):
        if mutation == "create":
            storage.upsert_mcp_override_with_audit(
                "created",
                _audit_request("mcp.config.created"),
                enabled=False,
                transport="stdio",
                command="python",
                tools_include=["approved_tool"],
            )
        elif mutation == "update":
            storage.upsert_mcp_override_with_audit(
                "approved",
                _audit_request("mcp.config.updated"),
                default_tier=1,
            )
        else:
            storage.delete_mcp_override_with_audit(
                "approved",
                _audit_request("mcp.config.deleted"),
            )

    assert storage.list_mcp_overrides() == before


def test_reload_propagates_secret_failure_as_stable_redacted_error(
    storage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app(storage, tmp_path)
    sentinel = "SECRET_VAULT_FAILURE_MUST_NOT_LEAK"

    def _fail_closed() -> None:
        raise ValueError(sentinel)

    monkeypatch.setattr(app.state.mcp_manager, "load_config", _fail_closed)
    with _client(app) as client:
        response = client.post(
            "/api/mcp/reload",
            headers=_headers(_ADMIN_TOKEN),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "mcp_config_unavailable"}}
    assert sentinel not in response.text
    assert sentinel not in caplog.text
