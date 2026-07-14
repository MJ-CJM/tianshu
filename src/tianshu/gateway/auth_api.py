"""Authentication/session lifecycle API."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from tianshu.gateway.auth import (
    IssuedToken,
    SecurityAuditContext,
    SessionPair,
    TokenMetadata,
    get_auth_context,
    hash_system_audit_identity,
)
from tianshu.models.principal import AuthContext

auth_router = APIRouter(prefix="/auth", tags=["auth"])


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1)


class TokenIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    scopes: frozenset[str] = Field(min_length=1)
    expires_at: datetime | None = None


def _settings(request: Request):
    return request.app.state.settings


def _service(request: Request):
    return request.app.state.auth_service


def _audit_context(request: Request) -> SecurityAuditContext:
    correlation_id = getattr(request.state, "correlation_id", None)
    if not isinstance(correlation_id, str) or not correlation_id:
        raise RuntimeError("request correlation is unavailable")
    context = getattr(request.state, "auth_context", None)
    return SecurityAuditContext(
        correlation_id=correlation_id,
        actor_digest=(
            hash_system_audit_identity(context.principal.id)
            if isinstance(context, AuthContext)
            else None
        ),
    )


def _metadata_payload(metadata: TokenMetadata) -> dict:
    payload = asdict(metadata)
    payload["scopes"] = sorted(metadata.scopes)
    return payload


def _issued_payload(issued: IssuedToken) -> dict:
    return {
        **_metadata_payload(issued.metadata),
        "token": issued.raw_token,
    }


def _set_session_cookies(response: Response, pair: SessionPair, request: Request) -> None:
    settings = _settings(request)
    secure = settings.security_mode == "secure-remote"
    response.set_cookie(
        "tianshu_access",
        pair.access_token,
        max_age=settings.auth_access_token_ttl_seconds,
        path="/api",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        "tianshu_refresh",
        pair.refresh_token,
        max_age=settings.auth_refresh_token_ttl_seconds,
        path="/api/auth/refresh",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookies(response: Response, request: Request) -> None:
    secure = _settings(request).security_mode == "secure-remote"
    response.delete_cookie(
        "tianshu_access",
        path="/api",
        secure=secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        "tianshu_refresh",
        path="/api/auth/refresh",
        secure=secure,
        httponly=True,
        samesite="strict",
    )


@auth_router.get("/mode")
def auth_mode(request: Request) -> dict:
    mode = _settings(request).security_mode
    return {"mode": mode, "login_required": mode == "secure-remote"}


@auth_router.post("/session")
def create_session(body: SessionRequest, request: Request, response: Response) -> dict:
    pair = _service(request).create_session(
        body.token,
        audit_context=_audit_context(request),
    )
    if pair is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    _set_session_cookies(response, pair, request)
    return {
        "principal": pair.principal.model_dump(mode="json"),
        "access_expires_at": pair.access_expires_at,
    }


@auth_router.post("/refresh")
def refresh_session(request: Request, response: Response) -> dict:
    raw_refresh = request.cookies.get("tianshu_refresh", "")
    pair = _service(request).refresh_session(
        raw_refresh,
        audit_context=_audit_context(request),
    )
    if pair is None:
        _clear_session_cookies(response, request)
        raise HTTPException(status_code=401, detail="invalid credentials")
    _set_session_cookies(response, pair, request)
    return {
        "principal": pair.principal.model_dump(mode="json"),
        "access_expires_at": pair.access_expires_at,
    }


@auth_router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(request: Request, response: Response) -> Response:
    context = get_auth_context(request)
    if context.credential_id:
        _service(request).revoke_session(
            context.credential_id,
            audit_context=_audit_context(request),
        )
    _clear_session_cookies(response, request)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@auth_router.get("/me")
def auth_me(request: Request) -> dict:
    context = get_auth_context(request)
    return {
        "principal": context.principal.model_dump(mode="json"),
        "source": context.source.value,
        "client_kind": context.client_kind.value,
    }


@auth_router.get("/tokens")
def list_tokens(request: Request) -> dict:
    get_auth_context(request)
    return {"items": [_metadata_payload(item) for item in _service(request).list_pats()]}


@auth_router.post("/tokens", status_code=status.HTTP_201_CREATED)
def issue_token(body: TokenIssueRequest, request: Request) -> dict:
    context = get_auth_context(request)
    try:
        issued = _service(request).issue_pat(
            context.principal,
            label=body.label,
            scopes=body.scopes,
            expires_at=body.expires_at,
            audit_context=_audit_context(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _issued_payload(issued)


@auth_router.post("/tokens/{token_id}/rotate")
def rotate_token(token_id: str, request: Request) -> dict:
    get_auth_context(request)
    try:
        issued = _service(request).rotate_pat(
            token_id,
            audit_context=_audit_context(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="active token not found") from exc
    return _issued_payload(issued)


@auth_router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_token(token_id: str, request: Request) -> Response:
    get_auth_context(request)
    if not _service(request).revoke_token(
        token_id,
        audit_context=_audit_context(request),
    ):
        raise HTTPException(status_code=404, detail="active token not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["auth_router"]
