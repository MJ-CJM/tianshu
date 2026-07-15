"""Authenticated REST surface for persistent governance decisions."""

from __future__ import annotations

import json
from typing import NoReturn, Protocol

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from tianshu.gateway.auth import get_auth_context
from tianshu.governance.decision_service import (
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

    def resolve(
        self,
        decision_request_id: str,
        command: ResolveDecisionCommand,
        *,
        auth: AuthContext,
    ) -> DecisionResolutionV1: ...


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


def _raise_service_error(context: AuthContext, error: DecisionServiceError) -> NoReturn:
    code = error.code if error.code in _MESSAGES else "decision_conflict"
    if isinstance(error, DecisionNotFound):
        _raise(context, 404, "decision_not_found")
    if isinstance(error, DecisionValidationError):
        _raise(context, 422, "invalid_decision_resolution")
    if isinstance(error, DecisionConflict):
        _raise(context, 409, code)
    _raise(context, 409, "decision_conflict")


def _parse_kind(context: AuthContext, raw_kind: str | None) -> DecisionKind | None:
    if raw_kind is None:
        return None
    try:
        return DecisionKind(raw_kind)
    except ValueError:
        _raise(context, 422, "invalid_decision_kind")


async def _parse_resolution(request: Request, context: AuthContext) -> ResolveDecisionCommand:
    try:
        payload = await request.json()
        return ResolveDecisionCommand.model_validate(payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError):
        _raise(context, 422, "invalid_decision_resolution")


@decisions_router.get("")
def list_pending_decisions(
    request: Request,
    kind: str | None = Query(default=None),
) -> dict[str, object]:
    context = get_auth_context(request)
    decision_kind = _parse_kind(context, kind)
    requests = _service(request).list_pending(kind=decision_kind)
    return {
        "items": [item.model_dump(mode="json") for item in requests],
        "correlation_id": context.correlation_id,
    }


@decisions_router.get("/{decision_request_id}")
def get_decision(request: Request, decision_request_id: str) -> dict[str, object]:
    context = get_auth_context(request)
    record = _service(request).get(decision_request_id)
    if record is None:
        _raise(context, 404, "decision_not_found")
    return {
        "data": record.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


@decisions_router.post("/{decision_request_id}/resolve")
async def resolve_decision(request: Request, decision_request_id: str) -> dict[str, object]:
    context = get_auth_context(request)
    command = await _parse_resolution(request, context)
    try:
        resolution = _service(request).resolve(
            decision_request_id,
            command,
            auth=context,
        )
    except DecisionServiceError as error:
        _raise_service_error(context, error)
    return {
        "data": resolution.model_dump(mode="json"),
        "correlation_id": context.correlation_id,
    }


__all__ = ["decisions_router"]
