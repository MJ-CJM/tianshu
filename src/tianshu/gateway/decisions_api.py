"""Authenticated REST surface for persistent governance decisions."""

from __future__ import annotations

from typing import NoReturn, Protocol

from fastapi import APIRouter, HTTPException, Query, Request

from tianshu.gateway.auth import get_auth_context
from tianshu.governance.decision_service import (
    DecisionAuthorizationError,
    DecisionConflict,
    DecisionNotFound,
    DecisionServiceError,
    DecisionValidationError,
)
from tianshu.models.decision import (
    DecisionKind,
    DecisionRecordV1,
    DecisionRequestV1,
    DecisionResolutionV1,
    ResolveDecisionCommand,
)
from tianshu.models.principal import AuthContext

decisions_router = APIRouter(prefix="/decisions", tags=["decisions"])


class _DecisionSurface(Protocol):
    def get(self, decision_request_id: str) -> DecisionRecordV1 | None: ...

    def list_pending(
        self,
        *,
        kind: DecisionKind | None = None,
    ) -> list[DecisionRequestV1]: ...

    def get_for_submitter(
        self,
        decision_request_id: str,
        *,
        submitter: str,
    ) -> DecisionRecordV1 | None: ...

    def list_pending_owned(
        self,
        *,
        submitter: str,
        kind: DecisionKind | None = None,
        limit: int = 100,
    ) -> list[DecisionRequestV1]: ...

    def resolve(
        self,
        decision_request_id: str,
        command: ResolveDecisionCommand,
        *,
        auth: AuthContext,
    ) -> DecisionResolutionV1: ...

    def deny_invalid_resolution(
        self,
        decision_request_id: str,
        *,
        auth: AuthContext,
    ) -> None: ...


_MESSAGES = {
    "decision_not_found": "decision request not found",
    "decision_identity_conflict": "decision request conflicts with durable authority",
    "decision_stale": "decision request changed before resolution",
    "decision_expired": "decision request is no longer active",
    "decision_cancelled": "decision request is no longer active",
    "decision_already_resolved": "decision request is no longer active",
    "decision_conflict": "decision request conflicts with durable authority",
    "invalid_decision_kind": "invalid decision kind",
    "invalid_decision_resolution": "invalid decision resolution",
    "workspace_apply_scope_required": "workspace:apply scope required",
    "legacy_decree_action_retired": "legacy decree action is retired; use a durable decision API",
    "legacy_decree_kind_unsupported": "legacy decree adapter does not support this decision kind",
}


def _service(request: Request) -> _DecisionSurface:
    service = request.app.state.decision_service
    return service


def _detail(context: AuthContext, code: str) -> dict[str, str]:
    return {
        "code": code,
        "message": _MESSAGES[code],
        "correlation_id": context.correlation_id,
    }


def _raise(context: AuthContext, status_code: int, code: str) -> NoReturn:
    raise HTTPException(status_code, _detail(context, code))


def require_owned_decision(
    request: Request,
    context: AuthContext,
    decision_request_id: str,
) -> DecisionRecordV1:
    record = _service(request).get_for_submitter(
        decision_request_id,
        submitter=context.principal.id,
    )
    if record is None:
        _raise(context, 404, "decision_not_found")
    return record


def _raise_service_error(context: AuthContext, error: DecisionServiceError) -> NoReturn:
    code = error.code if error.code in _MESSAGES else "decision_conflict"
    if isinstance(error, DecisionAuthorizationError):
        _raise(context, 403, "workspace_apply_scope_required")
    if isinstance(error, DecisionNotFound):
        _raise(context, 404, "decision_not_found")
    if isinstance(error, DecisionValidationError):
        _raise(context, 422, "invalid_decision_resolution")
    if isinstance(error, DecisionConflict):
        _raise(context, 409, code)
    _raise(context, 409, "decision_conflict")


def raise_decision_error(context: AuthContext, status_code: int, code: str) -> NoReturn:
    _raise(context, status_code, code)


def raise_decision_service_error(
    context: AuthContext,
    error: DecisionServiceError,
) -> NoReturn:
    _raise_service_error(context, error)


def _parse_kind(context: AuthContext, raw_kind: str | None) -> DecisionKind | None:
    if raw_kind is None:
        return None
    try:
        return DecisionKind(raw_kind)
    except ValueError:
        _raise(context, 422, "invalid_decision_kind")


async def _parse_resolution(
    request: Request,
    context: AuthContext,
    decision_request_id: str,
) -> ResolveDecisionCommand:
    try:
        payload = await request.json()
        return ResolveDecisionCommand.model_validate(payload)
    # JSONDecodeError, UnicodeDecodeError, and Pydantic ValidationError are ValueError subclasses.
    except (ValueError, TypeError, RecursionError):
        _service(request).deny_invalid_resolution(
            decision_request_id,
            auth=context,
        )
        _raise(context, 422, "invalid_decision_resolution")


@decisions_router.get("")
def list_pending_decisions(
    request: Request,
    kind: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, object]:
    context = get_auth_context(request)
    decision_kind = _parse_kind(context, kind)
    requests = _service(request).list_pending_owned(
        submitter=context.principal.id,
        kind=decision_kind,
        limit=limit,
    )
    return {
        "items": [item.model_dump(mode="json") for item in requests],
        "correlation_id": context.correlation_id,
    }


@decisions_router.get("/{decision_request_id}")
def get_decision(request: Request, decision_request_id: str) -> dict[str, object]:
    context = get_auth_context(request)
    record = require_owned_decision(request, context, decision_request_id)
    return {
        "data": record.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


@decisions_router.post("/{decision_request_id}/resolve")
async def resolve_decision(request: Request, decision_request_id: str) -> dict[str, object]:
    context = get_auth_context(request)
    require_owned_decision(request, context, decision_request_id)
    command = await _parse_resolution(request, context, decision_request_id)
    try:
        resolution = _service(request).resolve(
            decision_request_id,
            command,
            auth=context,
        )
    except DecisionServiceError as error:
        _raise_service_error(context, error)
    record = _service(request).get(decision_request_id)
    if record is None:  # pragma: no cover - the successful durable resolution owns this record
        _raise(context, 409, "decision_conflict")
    return {
        "data": resolution.model_dump(mode="json"),
        "status": record.request.status.value,
        "version": record.request.version,
        "record": record.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


__all__ = [
    "decisions_router",
    "raise_decision_error",
    "raise_decision_service_error",
    "require_owned_decision",
]
