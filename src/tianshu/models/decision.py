"""Strict immutable models for durable governance decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import JsonValue, canonical_sha256


class DecisionKind(str, Enum):
    TOOL = "tool"
    OUTER_LOOP = "outer_loop"
    PLAN_REVIEW = "plan_review"
    GOVERNED_APPLY = "governed_apply"


class DecisionStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class DecisionRequestV1(_StrictModel):
    decision_request_id: str
    schema_version: Literal[1] = 1
    kind: DecisionKind
    edict_id: str
    memorial_id: str
    request_key: str
    payload: dict[str, JsonValue]
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_by: str
    expires_at: datetime
    status: DecisionStatus
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    _validate_non_blank = field_validator(
        "decision_request_id",
        "edict_id",
        "memorial_id",
        "request_key",
        "requested_by",
    )(_non_blank)
    _normalize_times = field_validator("expires_at", "created_at", "updated_at")(_normalize_utc)

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.payload_hash != canonical_sha256(self.payload):
            raise ValueError("payload_hash does not match payload")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        return self


class DecisionResolutionV1(_StrictModel):
    decision_request_id: str
    action: str
    reason: str
    payload: dict[str, JsonValue]
    actor_principal_id: str
    actor_display_name: str
    resolved_at: datetime

    _validate_non_blank = field_validator(
        "decision_request_id",
        "action",
        "reason",
        "actor_principal_id",
        "actor_display_name",
    )(_non_blank)
    _normalize_resolved_at = field_validator("resolved_at")(_normalize_utc)


class RequestDecisionCommand(_StrictModel):
    kind: DecisionKind
    edict_id: str
    memorial_id: str
    request_key: str
    payload: dict[str, JsonValue]
    expires_at: datetime

    _validate_non_blank = field_validator("edict_id", "memorial_id", "request_key")(_non_blank)
    _normalize_expires_at = field_validator("expires_at")(_normalize_utc)


class ResolveDecisionCommand(_StrictModel):
    action: str
    reason: str
    payload: dict[str, JsonValue]
    expected_version: int = Field(ge=1)

    _validate_non_blank = field_validator("action", "reason")(_non_blank)


class DecisionRecordV1(_StrictModel):
    request: DecisionRequestV1
    resolution: DecisionResolutionV1 | None = None

    @model_validator(mode="after")
    def validate_resolution_identity(self) -> Self:
        if (
            self.resolution is not None
            and self.resolution.decision_request_id != self.request.decision_request_id
        ):
            raise ValueError("resolution decision_request_id does not match request")
        return self


class _VersionedPayload(_StrictModel):
    schema_version: Literal[1]


class _ToolApprovePayload(_VersionedPayload):
    grant_scope: Literal["once", "edict", "always"] | None = None
    grant_reason: str | None = None


class _ToolGuidePayload(_VersionedPayload):
    guidance: str

    _validate_guidance = field_validator("guidance")(_non_blank)


class _OuterContinuePayload(_VersionedPayload):
    feedback: str | None = None


class _OuterModifyAcceptancePayload(_VersionedPayload):
    acceptance: dict[str, JsonValue]


class _PlanAmendPayload(_VersionedPayload):
    amendment: str

    _validate_amendment = field_validator("amendment")(_non_blank)


_ACTION_PAYLOAD_MODELS: dict[tuple[DecisionKind, str], type[_VersionedPayload]] = {
    (DecisionKind.TOOL, "approve"): _ToolApprovePayload,
    (DecisionKind.TOOL, "reject"): _VersionedPayload,
    (DecisionKind.TOOL, "guide"): _ToolGuidePayload,
    (DecisionKind.OUTER_LOOP, "continue"): _OuterContinuePayload,
    (DecisionKind.OUTER_LOOP, "accept_as_is"): _VersionedPayload,
    (DecisionKind.OUTER_LOOP, "abort"): _VersionedPayload,
    (DecisionKind.OUTER_LOOP, "modify_acceptance"): _OuterModifyAcceptancePayload,
    (DecisionKind.PLAN_REVIEW, "approve"): _VersionedPayload,
    (DecisionKind.PLAN_REVIEW, "reject"): _VersionedPayload,
    (DecisionKind.PLAN_REVIEW, "amend"): _PlanAmendPayload,
    (DecisionKind.GOVERNED_APPLY, "approve"): _VersionedPayload,
    (DecisionKind.GOVERNED_APPLY, "reject"): _VersionedPayload,
}


def validate_resolution_payload(
    kind: DecisionKind, action: str, payload: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    """Validate the versioned action payload bound to a decision kind."""

    model = _ACTION_PAYLOAD_MODELS.get((kind, action))
    if model is None:
        raise ValueError(f"unsupported action {action!r} for decision kind {kind.value!r}")
    model.model_validate(payload)
    return payload


__all__ = [
    "DecisionKind",
    "DecisionRecordV1",
    "DecisionRequestV1",
    "DecisionResolutionV1",
    "DecisionStatus",
    "RequestDecisionCommand",
    "ResolveDecisionCommand",
    "validate_resolution_payload",
]
