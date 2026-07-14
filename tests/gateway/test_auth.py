"""Runtime mode, identity, and parent ASGI security boundary contracts."""

from __future__ import annotations

import hashlib
import importlib.util
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tianshu.config import TianshuSettings
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage


def _bootstrap_hash(token: str = "bootstrap-token-for-tests") -> str:
    return f"sha256:{hashlib.sha256(token.encode()).hexdigest()}"


def _secure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIANSHU_SECURITY_MODE", "secure-remote")
    monkeypatch.setenv("TIANSHU_PUBLIC_BASE_URL", "https://tianshu.example.com")
    monkeypatch.setenv("TIANSHU_ALLOWED_HOSTS", "tianshu.example.com")
    monkeypatch.setenv("TIANSHU_ALLOWED_ORIGINS", "https://tianshu.example.com")
    monkeypatch.setenv("TIANSHU_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("TIANSHU_AUTH_BOOTSTRAP_TOKEN_HASH", _bootstrap_hash())


def test_runtime_defaults_are_trusted_local_and_loopback() -> None:
    settings = TianshuSettings(_env_file=None)

    assert settings.host == "127.0.0.1"
    assert settings.security_mode == "trusted-local"


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20"])
def test_trusted_local_rejects_non_loopback_host_without_container_boundary(
    host: str,
) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        TianshuSettings(_env_file=None, host=host)


def test_principal_module_exists() -> None:
    assert importlib.util.find_spec("tianshu.models.principal") is not None


def test_auth_gateway_module_exists() -> None:
    assert importlib.util.find_spec("tianshu.gateway.auth") is not None


def test_principal_and_auth_context_are_immutable_and_forbid_extra_fields() -> None:
    principal = Principal(
        id="user:owner",
        kind=PrincipalKind.HUMAN,
        display_name="Owner",
        scopes=frozenset({"admin", "api", "mcp:read"}),
    )
    context = AuthContext(
        principal=principal,
        source=AuthenticationSource.BEARER,
        credential_id="token-1",
        client_kind=ClientKind.CLI,
        correlation_id="corr-1",
        remote_addr="127.0.0.1",
    )

    assert context.principal.id == "user:owner"
    assert context.model_dump(mode="json")["source"] == "bearer"
    with pytest.raises(ValidationError):
        Principal(
            id="user:owner",
            kind="human",
            display_name="Owner",
            scopes=frozenset(),
            forged=True,
        )
    with pytest.raises(ValidationError):
        context.correlation_id = "forged"  # type: ignore[misc]


def test_auth_context_binding_is_nested_and_resets() -> None:
    from tianshu.gateway.auth import bind_auth_context, get_current_auth_context

    outer = AuthContext(
        principal=Principal(
            id="user:outer",
            kind="human",
            display_name="Outer",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="outer-correlation",
    )
    inner = outer.model_copy(
        update={
            "principal": Principal(
                id="user:inner",
                kind="human",
                display_name="Inner",
                scopes=frozenset({"api"}),
            )
        }
    )

    assert get_current_auth_context() is None
    with bind_auth_context(outer):
        assert get_current_auth_context() == outer
        with bind_auth_context(inner):
            assert get_current_auth_context() == inner
        assert get_current_auth_context() == outer
    assert get_current_auth_context() is None


def test_issued_pat_is_returned_once_and_only_hash_is_persisted(storage: Storage) -> None:
    from tianshu.gateway.auth import AuthService

    now = datetime(2026, 7, 11, tzinfo=UTC)
    service = AuthService(storage, TianshuSettings(_env_file=None), clock=lambda: now)
    principal = Principal(
        id="user:owner",
        kind="human",
        display_name="Owner",
        scopes=frozenset({"admin", "api", "mcp:read", "mcp:submit"}),
    )

    issued = service.issue_pat(
        principal,
        label="CLI",
        scopes=frozenset({"api", "mcp:read"}),
        expires_at=now + timedelta(days=30),
    )

    persisted = storage.get_auth_token_by_prefix(issued.prefix)
    assert persisted is not None
    assert persisted["token_hash"] == hashlib.sha256(issued.raw_token.encode()).hexdigest()
    assert issued.raw_token not in repr(issued)
    assert all(not hasattr(item, "raw_token") for item in service.list_pats())


def test_pat_rotate_and_revoke_take_effect_immediately(storage: Storage) -> None:
    from tianshu.gateway.auth import AuthService

    now = datetime(2026, 7, 11, tzinfo=UTC)
    service = AuthService(storage, TianshuSettings(_env_file=None), clock=lambda: now)
    principal = Principal(
        id="user:owner",
        kind="human",
        display_name="Owner",
        scopes=frozenset({"admin", "api"}),
    )
    issued = service.issue_pat(principal, label="CLI", scopes=frozenset({"api"}))

    authenticated = service.authenticate_token(
        issued.raw_token,
        client_kind=ClientKind.CLI,
        correlation_id="corr-1",
        remote_addr="127.0.0.1",
    )
    assert authenticated is not None
    assert authenticated.principal.id == "user:owner"

    rotated = service.rotate_pat(issued.id)
    assert (
        service.authenticate_token(
            issued.raw_token,
            client_kind=ClientKind.CLI,
            correlation_id="corr-old",
        )
        is None
    )
    assert (
        service.authenticate_token(
            rotated.raw_token,
            client_kind=ClientKind.CLI,
            correlation_id="corr-new",
        )
        is not None
    )

    assert service.revoke_token(rotated.id) is True
    assert (
        service.authenticate_token(
            rotated.raw_token,
            client_kind=ClientKind.CLI,
            correlation_id="corr-revoked",
        )
        is None
    )


def test_bootstrap_hash_authenticates_without_persisting_plaintext(storage: Storage) -> None:
    from tianshu.gateway.auth import AuthService

    raw_token = "bootstrap-token-for-tests"
    settings = TianshuSettings(
        _env_file=None,
        auth_bootstrap_token_hash=_bootstrap_hash(raw_token),
    )
    service = AuthService(storage, settings)

    context = service.authenticate_token(
        raw_token,
        client_kind=ClientKind.CLI,
        correlation_id="bootstrap-correlation",
    )

    assert context is not None
    assert context.principal.id == "user:owner"
    assert context.principal.scopes == frozenset(
        {"admin", "api", "mcp:read", "mcp:submit", "workspace:apply"}
    )
    assert storage.list_auth_tokens() == []


def test_refresh_rotates_the_session_family_and_rejects_replay(storage: Storage) -> None:
    from tianshu.gateway.auth import AuthService

    now = datetime(2026, 7, 11, tzinfo=UTC)
    service = AuthService(storage, TianshuSettings(_env_file=None), clock=lambda: now)
    principal = Principal(
        id="user:owner",
        kind="human",
        display_name="Owner",
        scopes=frozenset({"admin", "api"}),
    )
    pat = service.issue_pat(principal, label="Web login", scopes=principal.scopes)
    first = service.create_session(pat.raw_token)
    assert first is not None

    refreshed = service.refresh_session(first.refresh_token)
    assert refreshed is not None
    assert refreshed.family_id == first.family_id
    assert (
        service.authenticate_token(
            first.access_token,
            client_kind=ClientKind.WEB,
            correlation_id="old-access",
        )
        is None
    )
    assert service.refresh_session(first.refresh_token) is None
    assert (
        service.authenticate_token(
            refreshed.access_token,
            client_kind=ClientKind.WEB,
            correlation_id="new-access",
        )
        is None
    )


@pytest.mark.parametrize(
    "missing_name",
    [
        "TIANSHU_PUBLIC_BASE_URL",
        "TIANSHU_ALLOWED_HOSTS",
        "TIANSHU_ALLOWED_ORIGINS",
        "TIANSHU_TRUSTED_PROXY_CIDRS",
        "TIANSHU_AUTH_BOOTSTRAP_TOKEN_HASH",
    ],
)
def test_secure_remote_requires_complete_identity_and_tls_proxy_configuration(
    monkeypatch: pytest.MonkeyPatch,
    missing_name: str,
) -> None:
    _secure_env(monkeypatch)
    monkeypatch.delenv(missing_name)

    with pytest.raises(ValidationError):
        TianshuSettings(_env_file=None)


def test_secure_remote_accepts_complete_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _secure_env(monkeypatch)

    settings = TianshuSettings(_env_file=None)

    assert settings.security_mode == "secure-remote"
    assert settings.allowed_hosts_list == ("tianshu.example.com",)
    assert settings.allowed_origins_list == ("https://tianshu.example.com",)
    assert settings.trusted_proxy_cidrs_list == ("127.0.0.1/32",)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TIANSHU_PUBLIC_BASE_URL", "http://tianshu.example.com"),
        ("TIANSHU_ALLOWED_HOSTS", "*"),
        ("TIANSHU_ALLOWED_ORIGINS", "*"),
        ("TIANSHU_ALLOWED_ORIGINS", "http://tianshu.example.com"),
        ("TIANSHU_TRUSTED_PROXY_CIDRS", "0.0.0.0/0"),
        ("TIANSHU_AUTH_BOOTSTRAP_TOKEN_HASH", "plaintext-token"),
    ],
)
def test_secure_remote_rejects_unsafe_configuration(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _secure_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        TianshuSettings(_env_file=None)


def _secure_settings() -> TianshuSettings:
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url="https://tianshu.example.com",
        allowed_hosts="tianshu.example.com",
        allowed_origins="https://tianshu.example.com",
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=_bootstrap_hash(),
    )


def _boundary_app(storage: Storage, settings: TianshuSettings) -> FastAPI:
    from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware

    app = FastAPI()
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = {"/channels/feishu/inbound"}
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
    async def echo(request: Request, path: str) -> dict:
        context = getattr(request.state, "auth_context", None)
        return {
            "path": f"/{path}",
            "principal": context.principal.id if context else None,
            "correlation_id": context.correlation_id if context else None,
            "client_kind": context.client_kind.value if context else None,
        }

    return app


def _auth_api_app(storage: Storage, settings: TianshuSettings) -> FastAPI:
    from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
    from tianshu.gateway.auth_api import auth_router

    app = FastAPI()
    app.state.settings = settings
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    app.include_router(auth_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/health"),
        ("GET", "/health/live"),
        ("GET", "/health/ready"),
        ("GET", "/"),
        ("GET", "/assets/app.js"),
        ("GET", "/api/auth/mode"),
        ("POST", "/api/auth/session"),
        ("POST", "/api/auth/refresh"),
        ("POST", "/channels/feishu/inbound"),
    ],
)
def test_secure_remote_public_route_matrix(
    storage: Storage,
    method: str,
    path: str,
) -> None:
    with TestClient(
        _boundary_app(storage, _secure_settings()),
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        response = client.request(method, path)

    assert response.status_code == 200
    assert response.json()["principal"] is None


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/edicts"),
        ("GET", "/api/auth/me"),
        ("GET", "/mcp"),
        ("POST", "/mcp"),
        ("POST", "/channels/feishu/inbound/near-match"),
    ],
)
def test_secure_remote_protected_routes_reject_anonymous_requests(
    storage: Storage,
    method: str,
    path: str,
) -> None:
    with TestClient(
        _boundary_app(storage, _secure_settings()),
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        response = client.request(method, path)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "authentication_required"
    assert response.json()["error"]["correlation_id"]


def test_secure_remote_bootstrap_bearer_reaches_rest_and_mcp(storage: Storage) -> None:
    headers = {
        "Authorization": "Bearer bootstrap-token-for-tests",
        "X-Tianshu-Client": "cli",
    }
    with TestClient(
        _boundary_app(storage, _secure_settings()),
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        rest = client.get("/api/edicts", headers=headers)
        mcp = client.post("/mcp", headers=headers)

    assert rest.status_code == 200
    assert rest.json()["principal"] == "user:owner"
    assert rest.json()["client_kind"] == "cli"
    assert mcp.status_code == 200
    assert mcp.json()["principal"] == "user:owner"
    assert mcp.json()["client_kind"] == "mcp"
    assert rest.headers["x-correlation-id"] == rest.json()["correlation_id"]


def test_invalid_host_origin_and_tls_are_rejected_before_route_execution(
    storage: Storage,
) -> None:
    app = _boundary_app(storage, _secure_settings())
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        bad_host = client.get("/health/live", headers={"Host": "evil.example"})
        wrong_port = client.get(
            "/health/live",
            headers={"Host": "tianshu.example.com:444"},
        )
        bad_origin = client.get(
            "/health/live",
            headers={"Origin": "https://evil.example"},
        )
    with TestClient(
        app,
        base_url="http://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        plaintext = client.get("/health/live")

    assert bad_host.status_code == 421
    assert wrong_port.status_code == 421
    assert bad_origin.status_code == 403
    assert plaintext.status_code == 426


def test_secure_remote_trusts_forwarded_https_only_from_configured_proxy(
    storage: Storage,
) -> None:
    app = _boundary_app(storage, _secure_settings())
    with TestClient(
        app,
        base_url="http://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        trusted = client.get("/health/live", headers={"X-Forwarded-Proto": "https"})
    with TestClient(
        app,
        base_url="http://tianshu.example.com",
        client=("203.0.113.9", 41000),
    ) as client:
        untrusted = client.get("/health/live", headers={"X-Forwarded-Proto": "https"})

    assert trusted.status_code == 200
    assert untrusted.status_code == 426


def test_secure_remote_allows_only_valid_origin_cors_preflight(storage: Storage) -> None:
    app = _boundary_app(storage, _secure_settings())
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        allowed = client.options(
            "/api/edicts",
            headers={
                "Origin": "https://tianshu.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        rejected = client.options(
            "/api/edicts",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert allowed.status_code == 200
    assert rejected.status_code == 403


def test_trusted_local_synthesizes_owner_only_without_forwarded_public_client(
    storage: Storage,
) -> None:
    settings = TianshuSettings(_env_file=None)
    app = _boundary_app(storage, settings)
    with TestClient(
        app,
        base_url="http://localhost",
        client=("127.0.0.1", 41000),
    ) as client:
        local = client.get("/api/edicts")
        forwarded = client.get(
            "/api/edicts",
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        real_ip = client.get(
            "/api/edicts",
            headers={"X-Real-IP": "203.0.113.9"},
        )
    with TestClient(
        app,
        base_url="http://localhost",
        client=("203.0.113.9", 41000),
    ) as client:
        remote = client.get("/api/edicts")

    assert local.status_code == 200
    assert local.json()["principal"] == "local:owner"
    assert forwarded.status_code == 401
    assert real_ip.status_code == 401
    assert remote.status_code == 401


def test_explicit_container_boundary_allows_docker_bridge_client(storage: Storage) -> None:
    settings = TianshuSettings(
        _env_file=None,
        host="0.0.0.0",
        trusted_local_container_boundary=True,
        trusted_local_container_gateway="172.18.0.1",
    )
    app = _boundary_app(storage, settings)
    with TestClient(
        app,
        base_url="http://localhost",
        client=("172.18.0.1", 41000),
    ) as client:
        response = client.get("/api/edicts")

    assert response.status_code == 200
    assert response.json()["principal"] == "local:owner"


def test_container_boundary_never_trusts_arbitrary_public_client(storage: Storage) -> None:
    settings = TianshuSettings(
        _env_file=None,
        host="0.0.0.0",
        trusted_local_container_boundary=True,
        trusted_local_container_gateway="172.18.0.1",
    )
    app = _boundary_app(storage, settings)
    with TestClient(
        app,
        base_url="http://localhost",
        client=("203.0.113.9", 41000),
    ) as client:
        response = client.get("/api/edicts")

    assert response.status_code == 401


def test_container_boundary_rejects_sibling_private_client(storage: Storage) -> None:
    settings = TianshuSettings(
        _env_file=None,
        host="0.0.0.0",
        trusted_local_container_boundary=True,
        trusted_local_container_gateway="172.18.0.1",
    )
    app = _boundary_app(storage, settings)
    with TestClient(
        app,
        base_url="http://localhost",
        client=("172.18.0.2", 41000),
    ) as client:
        response = client.get("/api/edicts")

    assert response.status_code == 401


def test_container_boundary_requires_exact_private_gateway() -> None:
    with pytest.raises(ValidationError, match="container gateway"):
        TianshuSettings(
            _env_file=None,
            host="0.0.0.0",
            trusted_local_container_boundary=True,
        )
    with pytest.raises(ValidationError, match="container gateway"):
        TianshuSettings(
            _env_file=None,
            host="0.0.0.0",
            trusted_local_container_boundary=True,
            trusted_local_container_gateway="0.0.0.0",
        )


def test_secure_remote_protected_route_requires_auth_and_accepts_valid_token(
    storage: Storage,
) -> None:
    app = _boundary_app(storage, _secure_settings())
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        anonymous = client.get("/api/edicts")
        authenticated = client.get(
            "/api/edicts",
            headers={"Authorization": "Bearer bootstrap-token-for-tests"},
        )

    assert anonymous.status_code == 401
    assert authenticated.status_code == 200
    assert authenticated.json()["principal"] == "user:owner"


def test_secure_remote_rejects_container_boundary_override() -> None:
    values = _secure_settings().model_dump()
    values["trusted_local_container_boundary"] = True
    with pytest.raises(ValidationError, match="container boundary"):
        TianshuSettings(_env_file=None, **values)


def test_unknown_unsafe_root_route_is_denied_even_with_credentials(storage: Storage) -> None:
    with TestClient(
        _boundary_app(storage, _secure_settings()),
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        response = client.post(
            "/future-root-admin",
            headers={"Authorization": "Bearer bootstrap-token-for-tests"},
        )

    assert response.status_code == 404


def test_auth_mode_discovery_exposes_no_secrets(storage: Storage) -> None:
    app = _auth_api_app(storage, _secure_settings())
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        response = client.get("/api/auth/mode")

    assert response.status_code == 200
    assert response.json() == {"mode": "secure-remote", "login_required": True}
    assert "token" not in response.text.lower()


def test_web_session_uses_http_only_cookies_and_supports_refresh_logout(
    storage: Storage,
) -> None:
    app = _auth_api_app(storage, _secure_settings())
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        login = client.post(
            "/api/auth/session",
            json={"token": "bootstrap-token-for-tests"},
        )
        me = client.get("/api/auth/me")
        refresh = client.post("/api/auth/refresh")
        me_after_refresh = client.get("/api/auth/me")
        logout = client.delete("/api/auth/session")
        after_logout = client.get("/api/auth/me")

    cookies = login.headers.get_list("set-cookie")
    assert login.status_code == 200
    assert me.json()["principal"]["id"] == "user:owner"
    assert any(
        value.startswith("tianshu_access=")
        and "HttpOnly" in value
        and "Secure" in value
        and "SameSite=strict" in value
        and "Path=/api" in value
        for value in cookies
    )
    assert any(
        value.startswith("tianshu_refresh=")
        and "HttpOnly" in value
        and "Path=/api/auth/refresh" in value
        for value in cookies
    )
    assert "bootstrap-token-for-tests" not in login.text
    assert refresh.status_code == 200
    assert me_after_refresh.status_code == 200
    assert logout.status_code == 204
    assert after_logout.status_code == 401


def test_pat_management_returns_secret_once_and_enforces_admin_scope(storage: Storage) -> None:
    settings = _secure_settings()
    app = _auth_api_app(storage, settings)
    service = app.state.auth_service
    limited = service.issue_pat(
        Principal(
            id="service:reader",
            kind="service",
            display_name="Reader",
            scopes=frozenset({"api"}),
        ),
        label="Reader",
        scopes=frozenset({"api"}),
    )
    admin_headers = {"Authorization": "Bearer bootstrap-token-for-tests"}
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        issued_response = client.post(
            "/api/auth/tokens",
            headers=admin_headers,
            json={"label": "CLI", "scopes": ["api", "mcp:read"]},
        )
        raw_token = issued_response.json()["token"]
        token_id = issued_response.json()["id"]
        listed = client.get("/api/auth/tokens", headers=admin_headers)
        forbidden = client.get(
            "/api/auth/tokens",
            headers={"Authorization": f"Bearer {limited.raw_token}"},
        )
        rotated = client.post(
            f"/api/auth/tokens/{token_id}/rotate",
            headers=admin_headers,
        )
        revoked = client.delete(
            f"/api/auth/tokens/{rotated.json()['id']}",
            headers=admin_headers,
        )

    assert issued_response.status_code == 201
    assert raw_token.startswith("tsu_")
    assert raw_token not in listed.text
    assert all("token" not in row for row in listed.json()["items"])
    assert forbidden.status_code == 403
    assert rotated.status_code == 200
    assert rotated.json()["token"] != raw_token
    assert revoked.status_code == 204


def test_create_app_uses_one_injected_settings_snapshot(tmp_path) -> None:
    from tianshu.app import create_app

    settings = TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "app.db"),
        host="127.0.0.1",
    )
    app = create_app(settings)

    assert app.state.settings is settings
    assert settings.host == "127.0.0.1"


def test_real_app_has_public_liveness_and_parent_boundary(tmp_path) -> None:
    from tianshu.app import create_app

    settings = _secure_settings().model_copy(update={"db_path": str(tmp_path / "app.db")})
    app = create_app(settings)
    client = TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    )
    try:
        live = client.get("/health/live")
        anonymous = client.get("/api/edicts")
    finally:
        client.close()

    assert live.status_code == 200
    assert live.json() == {"schema_version": "1", "status": "live"}
    assert anonymous.status_code == 401


def test_authenticated_edict_submitter_and_idempotency_ignore_forged_body(
    storage: Storage,
    config_manager,
) -> None:
    from tianshu.bus.event_bus import EventBus
    from tianshu.gateway.edicts_api import edicts_router

    settings = _secure_settings()
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.event_bus = EventBus()
    app.state.config_manager = config_manager
    app.state.auth_service = __import__(
        "tianshu.gateway.auth", fromlist=["AuthService"]
    ).AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    app.include_router(edicts_router, prefix="/api")
    from tianshu.gateway.auth import SecurityBoundaryMiddleware

    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    headers = {"Authorization": "Bearer bootstrap-token-for-tests"}
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        first = client.post(
            "/api/edicts",
            headers=headers,
            json={
                "goal": "canonical actor",
                "submitter": "forged:first",
                "idempotency_key": "same-key",
            },
        )
        second = client.post(
            "/api/edicts",
            headers=headers,
            json={
                "goal": "canonical actor",
                "submitter": "forged:second",
                "idempotency_key": "same-key",
            },
        )
        different_goal = client.post(
            "/api/edicts",
            headers=headers,
            json={"goal": "different request", "idempotency_key": "same-key"},
        )
        changed_contract = first.json()["data"]["governance_contract"]
        changed_contract["budget"]["token_limit"] = 123
        different_contract = client.post(
            "/api/edicts",
            headers=headers,
            json={
                "goal": "canonical actor",
                "idempotency_key": "same-key",
                "governance_contract": changed_contract,
            },
        )

    assert first.status_code == 202
    assert first.json()["data"]["submitter"] == "user:owner"
    assert second.json()["metadata"]["deduplicated"] is True
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    assert different_goal.status_code == 409
    assert different_contract.status_code == 409


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_authenticated_approval_actor_ignores_forged_body(
    storage: Storage,
    action: Literal["approve", "reject"],
) -> None:
    from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
    from tianshu.gateway.execution_api import execution_router
    from tianshu.models import Decree

    settings = _secure_settings()
    manager = SimpleNamespace(
        submit_decree=AsyncMock(),
        submit_tool_decision=AsyncMock(
            return_value=Decree(memorial_id="m-1", action=action, actor="user:owner")
        ),
    )
    app = FastAPI()
    app.state.settings = settings
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    app.state.approval_manager = manager
    app.include_router(execution_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    headers = {"Authorization": "Bearer bootstrap-token-for-tests"}
    with TestClient(
        app,
        base_url="https://tianshu.example.com",
        client=("127.0.0.1", 41000),
    ) as client:
        decree = client.post(
            "/api/decrees",
            headers=headers,
            json={"memorial_id": "m-1", "action": action, "actor": "forged"},
        )
        tool = client.post(
            "/api/approvals/tool_decision",
            headers=headers,
            json={"memorial_id": "m-1", "action": action, "actor": "forged"},
        )

    submitted_decree = manager.submit_decree.await_args.args[0]
    assert decree.status_code == 201
    assert decree.json()["data"]["actor"] == "user:owner"
    assert submitted_decree.actor == "user:owner"
    assert tool.status_code == 201
    assert manager.submit_tool_decision.await_args.kwargs["actor"] == "user:owner"
