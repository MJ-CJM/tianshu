"""Authenticated REST surface for governed workspace inspection and apply."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, NoReturn, Protocol

from fastapi import APIRouter, HTTPException, Request

from tianshu.executor.workspace_service import WorkspaceApplyError
from tianshu.gateway.auth import get_auth_context
from tianshu.models import ApiResponse
from tianshu.models.principal import Principal

workspace_router = APIRouter(prefix="/workspace-runs", tags=["workspace"])


class WorkspaceApplySurface(Protocol):
    async def get_run_status(self, run_id: str) -> object: ...

    async def get_run_changes(self, run_id: str) -> object: ...

    async def issue_apply_decision(
        self,
        run_id: str,
        principal: Principal,
        reason: str,
        ttl: timedelta,
    ) -> tuple[object, str]: ...

    async def apply(
        self,
        run_id: str,
        decision_id: str,
        token: str,
        principal: Principal,
    ) -> object: ...


_ERRORS: dict[str, tuple[int, str]] = {
    "run_not_found": (404, "workspace run not found"),
    "decision_not_found": (404, "apply decision not found"),
    "token_invalid": (403, "apply authorization rejected"),
    "principal_mismatch": (403, "apply authorization rejected"),
    "scope_denied": (403, "workspace apply scope required"),
    "decision_expired": (409, "apply decision is not usable"),
    "decision_not_pending": (409, "apply decision is not usable"),
    "lease_not_active": (409, "workspace run is not active"),
    "changes_unavailable": (409, "workspace changes are unavailable"),
    "apply_not_governed": (409, "workspace apply is not governed"),
    "capability_not_enforced": (409, "governed apply capability is not enforced"),
    "binding_mismatch": (409, "workspace apply state changed"),
    "source_drift": (409, "workspace apply state changed"),
    "staging_drift": (409, "workspace apply state changed"),
    "change_set_stale": (409, "workspace apply state changed"),
    "apply_claim_lost": (409, "workspace apply state changed"),
    "materialization_failed": (500, "workspace apply failed safely"),
    "rollback_failed": (500, "workspace apply failed safely"),
    "cleanup_failed": (500, "workspace apply failed safely"),
}


def _service(request: Request) -> WorkspaceApplySurface:
    return request.app.state.workspace_service


def _principal(request: Request, *, require_apply_scope: bool = False) -> Principal:
    principal = get_auth_context(request).principal
    if require_apply_scope and "workspace:apply" not in principal.scopes:
        raise HTTPException(
            403,
            {
                "code": "insufficient_scope",
                "message": "workspace:apply scope required",
            },
        )
    return principal


def _raise_safe_workspace_error(error: Exception) -> NoReturn:
    code = getattr(error, "code", None)
    if (
        not isinstance(error, WorkspaceApplyError)
        or not isinstance(code, str)
        or code not in _ERRORS
    ):
        raise error
    status, message = _ERRORS[code]
    raise HTTPException(status, {"code": code, "message": message}) from None


def _invalid_apply_request() -> NoReturn:
    raise HTTPException(
        422,
        {
            "code": "invalid_apply_request",
            "message": "invalid apply request",
        },
    )


def _invalid_apply_decision_request() -> NoReturn:
    raise HTTPException(
        422,
        {
            "code": "invalid_apply_decision_request",
            "message": "invalid apply decision request",
        },
    )


def _validated_decision_reason(reason: str) -> str:
    normalized = reason.strip()
    if (
        not normalized
        or len(normalized) > 2_000
        or any(character in normalized for character in "\x00\r\n")
    ):
        _invalid_apply_decision_request()
    return normalized


async def _parse_apply_decision_request(request: Request) -> tuple[str, int]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        _invalid_apply_decision_request()
    if not isinstance(payload, dict) or set(payload) not in (
        {"reason"},
        {"reason", "ttl_seconds"},
    ):
        _invalid_apply_decision_request()
    reason = payload["reason"]
    ttl_seconds = payload.get("ttl_seconds", 300)
    if (
        not isinstance(reason, str)
        or isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or not 1 <= ttl_seconds <= 3_600
    ):
        _invalid_apply_decision_request()
    return _validated_decision_reason(reason), ttl_seconds


async def _parse_apply_request(request: Request) -> tuple[str, str]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        _invalid_apply_request()
    if not isinstance(payload, dict) or set(payload) != {"decision_id", "token"}:
        _invalid_apply_request()
    decision_id = payload["decision_id"]
    token = payload["token"]
    if (
        not isinstance(decision_id, str)
        or not 1 <= len(decision_id) <= 128
        or not isinstance(token, str)
        or not 1 <= len(token) <= 4_096
    ):
        _invalid_apply_request()
    return decision_id, token


def _dump(record: object) -> dict[str, Any]:
    model_dump = getattr(record, "model_dump", None)
    if not callable(model_dump):
        raise TypeError("workspace service returned a non-record value")
    payload = model_dump(mode="json")
    if not isinstance(payload, dict):
        raise TypeError("workspace service returned an invalid record")
    return payload


def _pick(payload: dict[str, Any], *fields: str) -> dict[str, Any]:
    return {field: payload[field] for field in fields if field in payload}


def _change_view(payload: dict[str, Any]) -> dict[str, Any]:
    return _pick(
        payload,
        "schema_version",
        "kind",
        "old_path",
        "new_path",
        "old_oid",
        "new_oid",
        "old_mode",
        "new_mode",
        "old_size",
        "new_size",
        "binary",
    )


def _change_set_view(record: object) -> dict[str, Any]:
    payload = _dump(record)
    view = _pick(
        payload,
        "schema_version",
        "id",
        "lease_id",
        "restore_point_id",
        "base_revision",
        "sequence",
        "created_at",
    )
    content_hash = getattr(record, "content_hash", payload.get("content_hash"))
    if isinstance(content_hash, str):
        view["content_hash"] = content_hash
    view["changes"] = [
        _change_view(change) for change in payload.get("changes", ()) if isinstance(change, dict)
    ]
    return view


def _change_set_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    view = _pick(payload, "id", "sequence", "created_at")
    view["change_count"] = len(payload.get("changes", ()))
    return view


def _decision_view(record_or_payload: object) -> dict[str, Any]:
    payload = record_or_payload if isinstance(record_or_payload, dict) else _dump(record_or_payload)
    return _pick(
        payload,
        "schema_version",
        "id",
        "lease_id",
        "change_set_id",
        "change_set_hash",
        "base_revision",
        "apply_scope",
        "reason",
        "state",
        "state_version",
        "expires_at",
        "created_at",
    )


def _receipt_view(record_or_payload: object) -> dict[str, Any]:
    payload = record_or_payload if isinstance(record_or_payload, dict) else _dump(record_or_payload)
    return _pick(
        payload,
        "schema_version",
        "id",
        "decision_id",
        "lease_id",
        "change_set_id",
        "change_set_hash",
        "outcome",
        "pre_source_head",
        "post_source_head",
        "rollback_status",
        "failure_code",
        "created_at",
    )


def _status_view(record: object) -> dict[str, Any]:
    payload = _dump(record)
    view = _pick(
        payload,
        "schema_version",
        "run_id",
        "memorial_status",
        "effective_contract_hash",
        "host_crash_gap",
    )
    lease = payload.get("lease")
    view["lease"] = (
        _pick(lease, "id", "state", "state_version", "apply_mode", "base_revision")
        if isinstance(lease, dict)
        else None
    )
    restore_point = payload.get("restore_point")
    view["restore_point"] = (
        _pick(restore_point, "id", "base_revision", "created_at")
        if isinstance(restore_point, dict)
        else None
    )
    change_set = payload.get("change_set")
    view["change_set"] = _change_set_summary(change_set if isinstance(change_set, dict) else None)
    decision = payload.get("latest_decision")
    view["latest_decision"] = _decision_view(decision) if isinstance(decision, dict) else None
    receipt = payload.get("latest_receipt")
    view["latest_receipt"] = _receipt_view(receipt) if isinstance(receipt, dict) else None
    return view


@workspace_router.get("/{run_id}/status", response_model=ApiResponse)
async def get_workspace_status(run_id: str, request: Request) -> ApiResponse:
    _principal(request)
    try:
        status = await _service(request).get_run_status(run_id)
    except Exception as error:
        _raise_safe_workspace_error(error)
    return ApiResponse(success=True, data=_status_view(status))


@workspace_router.get("/{run_id}/changes", response_model=ApiResponse)
async def get_workspace_changes(run_id: str, request: Request) -> ApiResponse:
    _principal(request)
    try:
        changes = await _service(request).get_run_changes(run_id)
    except Exception as error:
        _raise_safe_workspace_error(error)
    return ApiResponse(success=True, data=_change_set_view(changes))


@workspace_router.post("/{run_id}/apply-decisions", response_model=ApiResponse, status_code=201)
async def issue_workspace_apply_decision(
    run_id: str,
    request: Request,
) -> ApiResponse:
    principal = _principal(request, require_apply_scope=True)
    reason, ttl_seconds = await _parse_apply_decision_request(request)
    try:
        decision, token = await _service(request).issue_apply_decision(
            run_id,
            principal,
            reason,
            timedelta(seconds=ttl_seconds),
        )
    except Exception as error:
        _raise_safe_workspace_error(error)
    return ApiResponse(
        success=True,
        data={"decision": _decision_view(decision), "token": token},
    )


@workspace_router.post("/{run_id}/apply", response_model=ApiResponse)
async def apply_workspace_changes(
    run_id: str,
    request: Request,
) -> ApiResponse:
    principal = _principal(request, require_apply_scope=True)
    decision_id, token = await _parse_apply_request(request)
    try:
        receipt = await _service(request).apply(
            run_id,
            decision_id,
            token,
            principal,
        )
    except Exception as error:
        _raise_safe_workspace_error(error)
    return ApiResponse(success=True, data={"receipt": _receipt_view(receipt)})


__all__ = [
    "WorkspaceApplySurface",
    "workspace_router",
]
