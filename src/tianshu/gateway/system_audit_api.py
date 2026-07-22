"""Admin-only reads for the tamper-evident SystemAudit chain."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from tianshu.gateway.auth import get_auth_context
from tianshu.storage.system_audit_repo import SystemAuditIntegrityError

system_audit_router = APIRouter(prefix="/audit/system", tags=["system-audit"])


def _storage(request: Request):
    return request.app.state.storage


def _require_admin(request: Request) -> None:
    context = get_auth_context(request)
    if "admin" not in context.principal.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient scope",
        )


def _integrity_error(exc: SystemAuditIntegrityError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "system_audit_integrity_failed",
            "reason_code": exc.reason_code,
            "failure_sequence": exc.sequence,
        },
    )


@system_audit_router.get("")
def list_system_audit(
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    _require_admin(request)
    try:
        events = _storage(request).list_system_audit(after=after, limit=limit)
    except SystemAuditIntegrityError as exc:
        raise _integrity_error(exc) from None
    return {"items": [event.model_dump(mode="json") for event in events]}


@system_audit_router.get("/export")
def export_system_audit(request: Request) -> dict:
    _require_admin(request)
    try:
        exported = _storage(request).export_system_audit()
    except SystemAuditIntegrityError as exc:
        raise _integrity_error(exc) from None
    return exported.model_dump(mode="json")


__all__ = ["system_audit_router"]
