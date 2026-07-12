"""Unified authentication service and parent ASGI security boundary."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from ulid import ULID

from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)

if TYPE_CHECKING:
    from tianshu.config import TianshuSettings
    from tianshu.storage import Storage

ALL_AUTH_SCOPES = frozenset({"admin", "api", "mcp:read", "mcp:submit", "workspace:apply"})
_LOCAL_ORIGIN = re.compile(r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$")
_current_auth_context: ContextVar[AuthContext | None] = ContextVar(
    "current_auth_context",
    default=None,
)


def get_current_auth_context() -> AuthContext | None:
    return _current_auth_context.get()


@contextmanager
def bind_auth_context(context: AuthContext) -> Iterator[None]:
    token = _current_auth_context.set(context)
    try:
        yield
    finally:
        _current_auth_context.reset(token)


@dataclass(frozen=True)
class TokenMetadata:
    id: str
    prefix: str
    principal_id: str
    principal_kind: str
    display_name: str
    label: str
    scopes: frozenset[str]
    token_type: str
    family_id: str | None
    created_at: str
    expires_at: str | None
    revoked_at: str | None
    replaced_by: str | None
    last_used_at: str | None


@dataclass(frozen=True)
class IssuedToken:
    id: str
    prefix: str
    raw_token: str = field(repr=False)
    metadata: TokenMetadata


@dataclass(frozen=True)
class SessionPair:
    family_id: str
    principal: Principal
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    access_expires_at: str
    refresh_expires_at: str


def hash_auth_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _metadata(row: dict) -> TokenMetadata:
    return TokenMetadata(
        id=row["id"],
        prefix=row["prefix"],
        principal_id=row["principal_id"],
        principal_kind=row["principal_kind"],
        display_name=row["display_name"],
        label=row["label"],
        scopes=frozenset(row["scopes"]),
        token_type=row["token_type"],
        family_id=row["family_id"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        replaced_by=row["replaced_by"],
        last_used_at=row["last_used_at"],
    )


class AuthService:
    def __init__(
        self,
        storage: Storage,
        settings: TianshuSettings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self._clock()
        return now if now.tzinfo is not None else now.replace(tzinfo=UTC)

    @staticmethod
    def _token_prefix(raw_token: str) -> str | None:
        parts = raw_token.split("_", 2)
        if len(parts) != 3 or parts[0] != "tsu" or not parts[1] or not parts[2]:
            return None
        return parts[1]

    def _new_token_record(
        self,
        principal: Principal,
        *,
        label: str,
        scopes: frozenset[str],
        token_type: str,
        family_id: str | None,
        expires_at: datetime | None,
    ) -> tuple[dict[str, object], str]:
        token_id = str(ULID())
        raw_token = f"tsu_{token_id}_{secrets.token_urlsafe(32)}"
        now = self._now()
        record: dict[str, object] = {
            "id": token_id,
            "prefix": token_id,
            "token_hash": hash_auth_token(raw_token),
            "principal_id": principal.id,
            "principal_kind": principal.kind.value,
            "display_name": principal.display_name,
            "label": label,
            "scopes": sorted(scopes),
            "token_type": token_type,
            "family_id": family_id,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
        }
        return record, raw_token

    @staticmethod
    def _principal_from_row(row: dict) -> Principal:
        return Principal(
            id=row["principal_id"],
            kind=PrincipalKind(row["principal_kind"]),
            display_name=row["display_name"],
            scopes=frozenset(row["scopes"]),
        )

    def _active_row(self, raw_token: str, allowed_types: frozenset[str]) -> dict | None:
        prefix = self._token_prefix(raw_token)
        if prefix is None:
            return None
        row = self._storage.get_auth_token_by_prefix(prefix)
        if row is None or row["token_type"] not in allowed_types or row["revoked_at"]:
            return None
        expires_at = _parse_timestamp(row["expires_at"])
        if expires_at is not None and expires_at <= self._now():
            return None
        if not hmac.compare_digest(row["token_hash"], hash_auth_token(raw_token)):
            return None
        return row

    def issue_pat(
        self,
        principal: Principal,
        *,
        label: str,
        scopes: frozenset[str],
        expires_at: datetime | None = None,
    ) -> IssuedToken:
        if not scopes or not scopes <= principal.scopes or not scopes <= ALL_AUTH_SCOPES:
            raise ValueError("issued token scopes must be a non-empty subset of principal scopes")
        record, raw_token = self._new_token_record(
            principal,
            label=label,
            scopes=scopes,
            token_type="pat",
            family_id=None,
            expires_at=expires_at,
        )
        self._storage.save_auth_token(record)
        row = self._storage.get_auth_token(record["id"])
        assert row is not None
        return IssuedToken(
            id=row["id"],
            prefix=row["prefix"],
            raw_token=raw_token,
            metadata=_metadata(row),
        )

    def list_pats(self) -> list[TokenMetadata]:
        return [_metadata(row) for row in self._storage.list_auth_tokens("pat")]

    def rotate_pat(self, token_id: str) -> IssuedToken:
        old = self._storage.get_auth_token(token_id)
        if old is None or old["token_type"] != "pat" or old["revoked_at"] is not None:
            raise ValueError("active personal token not found")
        expires_at = _parse_timestamp(old["expires_at"])
        if expires_at is not None and expires_at <= self._now():
            raise ValueError("active personal token not found")
        record, raw_token = self._new_token_record(
            self._principal_from_row(old),
            label=old["label"],
            scopes=frozenset(old["scopes"]),
            token_type="pat",
            family_id=None,
            expires_at=expires_at,
        )
        self._storage.replace_auth_token(token_id, record, self._now().isoformat())
        row = self._storage.get_auth_token(record["id"])
        assert row is not None
        return IssuedToken(
            id=row["id"],
            prefix=row["prefix"],
            raw_token=raw_token,
            metadata=_metadata(row),
        )

    def revoke_token(self, token_id: str) -> bool:
        return self._storage.revoke_auth_token(token_id, self._now().isoformat())

    def _bootstrap_context(
        self,
        raw_token: str,
        *,
        client_kind: ClientKind,
        correlation_id: str,
        remote_addr: str | None,
    ) -> AuthContext | None:
        configured_hash = self._settings.auth_bootstrap_token_hash
        if not configured_hash:
            return None
        actual = f"sha256:{hash_auth_token(raw_token)}"
        if not hmac.compare_digest(configured_hash, actual):
            return None
        principal = Principal(
            id="user:owner",
            kind=PrincipalKind.HUMAN,
            display_name="Owner",
            scopes=ALL_AUTH_SCOPES,
        )
        return AuthContext(
            principal=principal,
            source=AuthenticationSource.BEARER,
            credential_id="bootstrap",
            client_kind=client_kind,
            correlation_id=correlation_id,
            remote_addr=remote_addr,
        )

    def authenticate_token(
        self,
        raw_token: str,
        *,
        client_kind: ClientKind,
        correlation_id: str,
        remote_addr: str | None = None,
        allowed_types: frozenset[str] = frozenset({"pat", "access"}),
        source: AuthenticationSource = AuthenticationSource.BEARER,
    ) -> AuthContext | None:
        bootstrap = self._bootstrap_context(
            raw_token,
            client_kind=client_kind,
            correlation_id=correlation_id,
            remote_addr=remote_addr,
        )
        if bootstrap is not None and "pat" in allowed_types:
            return bootstrap

        row = self._active_row(raw_token, allowed_types)
        if row is None:
            return None
        return AuthContext(
            principal=self._principal_from_row(row),
            source=source,
            credential_id=row["id"],
            client_kind=client_kind,
            correlation_id=correlation_id,
            remote_addr=remote_addr,
        )

    def _session_records(
        self,
        principal: Principal,
        family_id: str,
    ) -> tuple[list[dict[str, object]], SessionPair]:
        now = self._now()
        access_expires = now + timedelta(seconds=self._settings.auth_access_token_ttl_seconds)
        refresh_expires = now + timedelta(seconds=self._settings.auth_refresh_token_ttl_seconds)
        access_record, access_token = self._new_token_record(
            principal,
            label="Web access session",
            scopes=principal.scopes,
            token_type="access",
            expires_at=access_expires,
            family_id=family_id,
        )
        refresh_record, refresh_token = self._new_token_record(
            principal,
            label="Web refresh session",
            scopes=principal.scopes,
            token_type="refresh",
            expires_at=refresh_expires,
            family_id=family_id,
        )
        return [access_record, refresh_record], SessionPair(
            family_id=family_id,
            principal=principal,
            access_token=access_token,
            refresh_token=refresh_token,
            access_expires_at=access_expires.isoformat(),
            refresh_expires_at=refresh_expires.isoformat(),
        )

    def create_session(self, raw_pat: str) -> SessionPair | None:
        context = self.authenticate_token(
            raw_pat,
            client_kind=ClientKind.WEB,
            correlation_id=str(ULID()),
            allowed_types=frozenset({"pat"}),
        )
        if context is None:
            return None
        family_id = str(ULID())
        records, pair = self._session_records(context.principal, family_id)
        self._storage.save_auth_tokens(records)
        return pair

    def refresh_session(self, raw_refresh: str) -> SessionPair | None:
        context = self.authenticate_token(
            raw_refresh,
            client_kind=ClientKind.WEB,
            correlation_id=str(ULID()),
            allowed_types=frozenset({"refresh"}),
            source=AuthenticationSource.SESSION_COOKIE,
        )
        if context is None:
            self._revoke_replayed_refresh(raw_refresh)
            return None
        if context.credential_id is None:
            return None
        old = self._storage.get_auth_token(context.credential_id)
        if old is None or old["family_id"] is None:
            return None
        records, pair = self._session_records(context.principal, old["family_id"])
        try:
            self._storage.replace_auth_session_family(
                old["family_id"],
                old["id"],
                records,
                self._now().isoformat(),
            )
        except ValueError:
            return None
        return pair

    def _revoke_replayed_refresh(self, raw_refresh: str) -> None:
        prefix = self._token_prefix(raw_refresh)
        if prefix is None:
            return
        row = self._storage.get_auth_token_by_prefix(prefix)
        if (
            row is None
            or row["token_type"] != "refresh"
            or row["revoked_at"] is None
            or row["replaced_by"] is None
            or row["family_id"] is None
            or not hmac.compare_digest(row["token_hash"], hash_auth_token(raw_refresh))
        ):
            return
        self._storage.revoke_auth_family(row["family_id"], self._now().isoformat())

    def revoke_session(self, credential_id: str) -> bool:
        row = self._storage.get_auth_token(credential_id)
        if row is None or row["family_id"] is None:
            return False
        return self._storage.revoke_auth_family(row["family_id"], self._now().isoformat()) > 0

    def is_context_active(self, context: AuthContext) -> bool:
        """Revalidate an established transport without retaining its raw credential."""

        if context.source == AuthenticationSource.TRUSTED_LOCAL:
            return self._settings.security_mode == "trusted-local"
        if context.credential_id == "bootstrap":
            return bool(self._settings.auth_bootstrap_token_hash)
        if context.credential_id is None:
            return False

        row = self._storage.get_auth_token(context.credential_id)
        if row is None or row["revoked_at"] is not None:
            return False
        expires_at = _parse_timestamp(row["expires_at"])
        if expires_at is not None and expires_at <= self._now():
            return False
        return (
            row["principal_id"] == context.principal.id
            and frozenset(row["scopes"]) == context.principal.scopes
        )


def get_auth_context(scope_or_request: object) -> AuthContext:
    """Return the middleware-produced identity or fail closed.

    Accepting either a Starlette Request/WebSocket or a raw ASGI scope keeps
    gateway handlers and mounted transports on the same identity contract.
    """

    scope = getattr(scope_or_request, "scope", scope_or_request)
    if not isinstance(scope, dict):
        raise RuntimeError("authentication context is unavailable")
    context = scope.get("state", {}).get("auth_context")
    if not isinstance(context, AuthContext):
        raise RuntimeError("authentication context is unavailable")
    return context


class SecurityBoundaryMiddleware:
    """Pure ASGI identity boundary for HTTP, WebSocket, and mounted MCP apps."""

    def __init__(self, app: ASGIApp, *, settings: TianshuSettings) -> None:
        self.app = app
        self.settings = settings
        self._trusted_proxies = tuple(
            ipaddress.ip_network(value, strict=False) for value in settings.trusted_proxy_cidrs_list
        )
        self._container_gateway = (
            ipaddress.ip_address(settings.trusted_local_container_gateway)
            if settings.trusted_local_container_boundary
            else None
        )

    @staticmethod
    def _header_host(value: str) -> str:
        value = value.strip().lower()
        if value.startswith("["):
            closing = value.find("]")
            return value[: closing + 1] if closing >= 0 else value
        return value.rsplit(":", 1)[0] if value.count(":") == 1 else value

    def _host_allowed(self, host: str) -> bool:
        configured = {value.lower() for value in self.settings.allowed_hosts_list}
        if self.settings.security_mode == "secure-remote":
            return host.strip().lower() in configured
        return self._header_host(host) in {"localhost", "127.0.0.1", "[::1]"} | configured

    def _origin_allowed(self, origin: str | None) -> bool:
        if not origin:
            return True
        if self.settings.security_mode == "secure-remote":
            return origin in self.settings.allowed_origins_list
        return _LOCAL_ORIGIN.fullmatch(origin) is not None

    @staticmethod
    def _client_address(scope: Scope) -> str | None:
        client = scope.get("client")
        return str(client[0]) if client else None

    def _is_loopback(self, address: str | None) -> bool:
        if not address:
            return False
        try:
            return ipaddress.ip_address(address).is_loopback
        except ValueError:
            # In-process ASGI test clients use a non-IP sentinel. It is only
            # recognized when the test-only Host was explicitly configured.
            return address == "testclient" and "testserver" in self.settings.allowed_hosts_list

    def _is_container_gateway(self, address: str | None) -> bool:
        if not address or self._container_gateway is None:
            return False
        try:
            client_ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return client_ip == self._container_gateway

    def _trusted_forwarded_https(self, scope: Scope, headers: Headers) -> bool:
        if headers.get("x-forwarded-proto", "").split(",", 1)[0].strip().lower() != "https":
            return False
        address = self._client_address(scope)
        if not address:
            return False
        try:
            client_ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(client_ip in network for network in self._trusted_proxies)

    @staticmethod
    def _cookie(headers: Headers, name: str) -> str | None:
        raw_cookie = headers.get("cookie")
        if not raw_cookie:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie)
        except Exception:
            return None
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    @staticmethod
    def _client_kind(
        path: str,
        source: AuthenticationSource,
        declared_client: str | None = None,
    ) -> ClientKind:
        if path.startswith("/mcp"):
            return ClientKind.MCP
        if source == AuthenticationSource.SESSION_COOKIE:
            return ClientKind.WEB
        if declared_client == ClientKind.CLI.value:
            return ClientKind.CLI
        return ClientKind.API

    @staticmethod
    def _public_route(method: str, path: str, webhook_paths: set[str]) -> bool:
        if method == "POST" and path in webhook_paths:
            return True
        if (method, path) in {
            ("GET", "/health"),
            ("HEAD", "/health"),
            ("GET", "/health/live"),
            ("HEAD", "/health/live"),
            ("GET", "/health/ready"),
            ("HEAD", "/health/ready"),
            ("GET", "/api/auth/mode"),
            ("POST", "/api/auth/session"),
            ("POST", "/api/auth/refresh"),
        }:
            return True
        return method in {"GET", "HEAD"} and not path.startswith(
            ("/api", "/mcp", "/docs", "/redoc", "/openapi.json")
        )

    @staticmethod
    def _required_scopes(path: str) -> frozenset[str]:
        if path.startswith("/mcp"):
            return frozenset({"mcp:read", "mcp:submit"})
        if path.startswith("/api/auth/tokens"):
            return frozenset({"admin"})
        return frozenset({"api"})

    @staticmethod
    def _requires_workspace_apply(method: str, path: str) -> bool:
        return (
            method == "POST"
            and path.startswith("/api/workspace-runs/")
            and path.rsplit("/", 1)[-1] in {"apply-decisions", "apply"}
        )

    @staticmethod
    def _unsafe_unknown(method: str, path: str, webhook_paths: set[str]) -> bool:
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return False
        known_prefix = path.startswith(("/api", "/mcp", "/channels/"))
        return not known_prefix and path not in webhook_paths

    def _local_context(
        self,
        scope: Scope,
        headers: Headers,
        correlation_id: str,
    ) -> AuthContext | None:
        if any(
            headers.get(name)
            for name in (
                "forwarded",
                "x-forwarded-for",
                "x-forwarded-host",
                "x-forwarded-proto",
                "x-real-ip",
            )
        ):
            return None
        address = self._client_address(scope)
        if not self._is_loopback(address):
            if not self.settings.trusted_local_container_boundary:
                return None
            if not self._is_container_gateway(address):
                return None
        return AuthContext(
            principal=Principal(
                id="local:owner",
                kind=PrincipalKind.LOCAL,
                display_name="Local Owner",
                scopes=ALL_AUTH_SCOPES,
            ),
            source=AuthenticationSource.TRUSTED_LOCAL,
            client_kind=self._client_kind(
                scope.get("path", ""),
                AuthenticationSource.TRUSTED_LOCAL,
                headers.get("x-tianshu-client"),
            ),
            correlation_id=correlation_id,
            remote_addr=address,
        )

    def _authenticate(
        self,
        scope: Scope,
        headers: Headers,
        correlation_id: str,
    ) -> AuthContext | None:
        authorization = headers.get("authorization")
        raw_token: str | None = None
        source = AuthenticationSource.BEARER
        if authorization:
            scheme, separator, value = authorization.partition(" ")
            if scheme.lower() != "bearer" or not separator or not value.strip():
                return None
            raw_token = value.strip()
        else:
            raw_token = self._cookie(headers, "tianshu_access")
            source = AuthenticationSource.SESSION_COOKIE

        if raw_token:
            starlette_app = scope.get("app")
            auth_service = getattr(getattr(starlette_app, "state", None), "auth_service", None)
            if auth_service is None:
                return None
            return auth_service.authenticate_token(
                raw_token,
                client_kind=self._client_kind(
                    scope.get("path", ""),
                    source,
                    headers.get("x-tianshu-client"),
                ),
                correlation_id=correlation_id,
                remote_addr=self._client_address(scope),
                allowed_types=(
                    frozenset({"access"})
                    if source == AuthenticationSource.SESSION_COOKIE
                    else frozenset({"pat", "access"})
                ),
                source=source,
            )
        if self.settings.security_mode == "trusted-local":
            return self._local_context(scope, headers, correlation_id)
        return None

    @staticmethod
    async def _reject_http(
        send: Send,
        status: int,
        code: str,
        correlation_id: str,
    ) -> None:
        payload = json.dumps(
            {
                "error": {
                    "code": code,
                    "message": "request rejected by the security boundary",
                    "correlation_id": correlation_id,
                }
            },
            separators=(",", ":"),
        ).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
            (b"x-correlation-id", correlation_id.encode()),
        ]
        if status == 401:
            headers.append((b"www-authenticate", b"Bearer"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})

    @staticmethod
    async def _reject_websocket(send: Send, code: int, reason: str) -> None:
        await send({"type": "websocket.close", "code": code, "reason": reason})

    @staticmethod
    def _send_with_correlation(send: Send, correlation_id: str) -> Send:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-correlation-id", correlation_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        return wrapped

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        correlation_id = str(ULID())
        host = headers.get("host", "")
        if not self._host_allowed(host):
            if scope["type"] == "websocket":
                await self._reject_websocket(send, 4403, "host_not_allowed")
            else:
                await self._reject_http(send, 421, "host_not_allowed", correlation_id)
            return
        if not self._origin_allowed(headers.get("origin")):
            if scope["type"] == "websocket":
                await self._reject_websocket(send, 4403, "origin_not_allowed")
            else:
                await self._reject_http(send, 403, "origin_not_allowed", correlation_id)
            return
        if (
            self.settings.security_mode == "secure-remote"
            and scope.get("scheme") not in {"https", "wss"}
            and not self._trusted_forwarded_https(scope, headers)
        ):
            if scope["type"] == "websocket":
                await self._reject_websocket(send, 4403, "tls_required")
            else:
                await self._reject_http(send, 426, "tls_required", correlation_id)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        starlette_app = scope.get("app")
        webhook_paths = set(
            getattr(getattr(starlette_app, "state", None), "public_webhook_paths", set())
        )
        if scope["type"] == "http" and self._unsafe_unknown(method, path, webhook_paths):
            await self._reject_http(send, 404, "route_not_allowed", correlation_id)
            return
        is_cors_preflight = (
            method == "OPTIONS"
            and bool(headers.get("origin"))
            and bool(headers.get("access-control-request-method"))
        )
        if scope["type"] == "http" and (
            is_cors_preflight or self._public_route(method, path, webhook_paths)
        ):
            await self.app(
                scope,
                receive,
                self._send_with_correlation(send, correlation_id),
            )
            return

        context = self._authenticate(scope, headers, correlation_id)
        if context is None:
            if scope["type"] == "websocket":
                await self._reject_websocket(send, 4401, "authentication_required")
            else:
                await self._reject_http(send, 401, "authentication_required", correlation_id)
            return
        required_scopes = self._required_scopes(path)
        if required_scopes.isdisjoint(context.principal.scopes):
            if scope["type"] == "websocket":
                await self._reject_websocket(send, 4403, "insufficient_scope")
            else:
                await self._reject_http(send, 403, "insufficient_scope", correlation_id)
            return
        if self._requires_workspace_apply(method, path) and (
            "workspace:apply" not in context.principal.scopes
        ):
            if scope["type"] == "websocket":
                await self._reject_websocket(send, 4403, "insufficient_scope")
            else:
                await self._reject_http(send, 403, "insufficient_scope", correlation_id)
            return

        scope.setdefault("state", {})["auth_context"] = context
        with bind_auth_context(context):
            await self.app(
                scope,
                receive,
                self._send_with_correlation(send, correlation_id),
            )


__all__ = [
    "ALL_AUTH_SCOPES",
    "AuthService",
    "SecurityBoundaryMiddleware",
    "get_auth_context",
    "hash_auth_token",
    "IssuedToken",
    "SessionPair",
    "TokenMetadata",
    "bind_auth_context",
    "get_current_auth_context",
]
