"""Immutable models and canonical hashing for the system audit chain."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

type SystemAuditMetadataValue = str | int | bool | None
type SystemAuditOutcome = Literal["allowed", "denied", "succeeded", "failed"]

GENESIS_SYSTEM_AUDIT_HASH = "0" * 64
MAX_SYSTEM_AUDIT_PAGE_SIZE = 1000

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_STABLE_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")

SYSTEM_AUDIT_METADATA_KEYS: dict[str, frozenset[str]] = {
    "auth.token.issued": frozenset({"scope_count", "token_type"}),
    "auth.token.rotated": frozenset({"scope_count", "token_type"}),
    "auth.token.revoked": frozenset({"family_size", "token_type"}),
    "auth.session.denied": frozenset({"token_type"}),
    "auth.session.rotated": frozenset({"family_size"}),
    "auth.session.revoked": frozenset({"family_size"}),
    "estop.engaged": frozenset({"frozen_tool_count", "kill_all", "network_kill"}),
    "estop.resumed": frozenset({"frozen_tool_count", "kill_all", "network_kill"}),
    "mcp.admission.denied": frozenset(),
    "mcp.config.created": frozenset(),
    "mcp.config.updated": frozenset(),
    "mcp.config.deleted": frozenset(),
    "secrets.master_key.rotated": frozenset(),
    "decision.request.denied": frozenset({"kind"}),
    "decision.resolve.denied": frozenset({"actual_version", "expected_version", "kind", "status"}),
    "notification.delivery.retry_scheduled": frozenset(
        {"attempt_count", "deadline_expired", "max_attempts"}
    ),
    "notification.delivery.dead_lettered": frozenset(
        {"attempt_count", "deadline_expired", "max_attempts"}
    ),
    "notification.delivery.delivered": frozenset(
        {"attempt_count", "deadline_expired", "max_attempts"}
    ),
}


def canonical_system_audit_json(payload: dict[str, Any]) -> str:
    """Return the canonical JSON representation used by the audit hash chain."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def hash_system_audit_payload(payload: dict[str, Any]) -> str:
    """Hash a persisted system-audit payload as lowercase SHA-256."""

    canonical = canonical_system_audit_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_digest(value: str) -> str:
    if not _DIGEST_RE.fullmatch(value):
        raise ValueError("digest must be 64 lowercase hexadecimal characters")
    return value


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _validate_stable_code(value: str) -> str:
    if not _STABLE_CODE_RE.fullmatch(value):
        raise ValueError("value must be a stable lowercase code")
    return value


def _validate_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("value must be an opaque identifier")
    return value


def _validate_metadata_value(key: str, value: SystemAuditMetadataValue) -> None:
    if key == "token_type":
        if value not in {"pat", "access", "refresh"}:
            raise ValueError("token_type must be a stable token category")
        return
    if key in {"family_size", "frozen_tool_count", "scope_count"}:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        return
    if key in {"attempt_count", "max_attempts"}:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        return
    if key == "deadline_expired" and not isinstance(value, bool):
        raise ValueError("deadline_expired must be a boolean")
    if key == "deadline_expired":
        return
    if key in {"kill_all", "network_kill"} and not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    if key in {"actual_version", "expected_version"}:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        return
    if key == "kind" and value not in {
        "tool",
        "outer_loop",
        "plan_review",
        "governed_apply",
    }:
        raise ValueError("kind must be a stable decision kind")
    if key == "status" and value not in {
        "pending",
        "resolved",
        "expired",
        "cancelled",
        "missing",
    }:
        raise ValueError("status must be a stable decision status")


class AppendSystemAuditRequest(BaseModel):
    """Safe caller-supplied fields for one system audit append."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str = Field(min_length=1, max_length=256)
    actor_digest: str
    action: str
    outcome: SystemAuditOutcome
    reason_code: str
    subject_kind: str
    subject_digest: str
    metadata: dict[str, SystemAuditMetadataValue] = Field(default_factory=dict)

    _validate_digests = field_validator("actor_digest", "subject_digest")(_validate_digest)
    _validate_correlation = field_validator("correlation_id")(_validate_identifier)
    _validate_reason_and_subject = field_validator("reason_code", "subject_kind")(
        _validate_stable_code
    )

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata_value_types(cls, metadata: Any) -> Any:
        if isinstance(metadata, Mapping):
            for value in metadata.values():
                if value is not None and type(value) not in (str, int, bool):
                    raise ValueError("metadata values must use exact primitive types")
        return metadata

    @model_validator(mode="after")
    def validate_action_metadata(self) -> Self:
        allowed_keys = SYSTEM_AUDIT_METADATA_KEYS.get(self.action)
        if allowed_keys is None:
            raise ValueError("unsupported system audit action")
        unknown_keys = set(self.metadata) - allowed_keys
        if unknown_keys:
            raise ValueError(
                "metadata keys are not allowed for action: " + ", ".join(sorted(unknown_keys))
            )
        for key, value in self.metadata.items():
            _validate_metadata_value(key, value)
        return self


class SystemAuditEventV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    id: str
    sequence: int = Field(ge=1)
    correlation_id: str
    actor_digest: str
    action: str
    outcome: SystemAuditOutcome
    reason_code: str
    subject_kind: str
    subject_digest: str
    metadata: dict[str, SystemAuditMetadataValue]
    previous_hash: str
    event_hash: str
    created_at: datetime

    _validate_digests = field_validator(
        "actor_digest", "subject_digest", "previous_hash", "event_hash"
    )(_validate_digest)
    _normalize_created_at = field_validator("created_at")(_normalize_utc)


class SystemAuditExportV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    start_sequence: int | None
    end_sequence: int | None
    terminal_hash: str
    events: tuple[SystemAuditEventV1, ...]

    _validate_terminal_hash = field_validator("terminal_hash")(_validate_digest)


class SystemAuditVerificationV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    verified: bool
    event_count: int = Field(ge=0)
    start_sequence: int | None
    end_sequence: int | None
    terminal_hash: str
    failure_sequence: int | None = None
    reason_code: str

    _validate_terminal_hash = field_validator("terminal_hash")(_validate_digest)
