"""Authenticated actors propagated across Tianshu gateway surfaces."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class PrincipalKind(StrEnum):
    LOCAL = "local"
    HUMAN = "human"
    SERVICE = "service"
    WEBHOOK = "webhook"


class AuthenticationSource(StrEnum):
    TRUSTED_LOCAL = "trusted-local"
    BEARER = "bearer"
    SESSION_COOKIE = "session-cookie"
    WEBHOOK = "webhook"


class ClientKind(StrEnum):
    WEB = "web"
    CLI = "cli"
    MCP = "mcp"
    API = "api"
    WEBHOOK = "webhook"
    SYSTEM = "system"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: PrincipalKind
    display_name: str
    scopes: frozenset[str] = frozenset()


class AuthContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: Principal
    source: AuthenticationSource
    credential_id: str | None = None
    client_kind: ClientKind
    correlation_id: str
    remote_addr: str | None = None


__all__ = [
    "AuthContext",
    "AuthenticationSource",
    "ClientKind",
    "Principal",
    "PrincipalKind",
]
