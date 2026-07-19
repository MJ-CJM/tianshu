"""Strict immutable Lean evidence for canonical plan revisions."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tianshu.models.canonical import canonical_json_bytes
from tianshu.security.redact import redact_text

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


def _identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError("value must be an opaque identifier")
    return value


def _optional_identifier(value: str | None) -> str | None:
    return _identifier(value) if value is not None else None


def _reason_code(value: str) -> str:
    if not _REASON_CODE_RE.fullmatch(value):
        raise ValueError("reason_code must be a stable lowercase code")
    return value


def _redacted_non_blank(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("reason_summary must not be blank")
    if redact_text(normalized) != normalized:
        raise ValueError("reason_summary must already be redacted")
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    return value.astimezone(UTC)


class PlanRevisionV1(BaseModel):
    """One immutable plan hash and its minimal Evidence-facing lineage facts."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    revision_id: str
    parent_revision_id: str | None
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_code: str
    reason_summary: str = Field(max_length=4096)
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    _validate_revision_id = field_validator("revision_id")(_identifier)
    _validate_parent_revision_id = field_validator("parent_revision_id")(_optional_identifier)
    _validate_reason_code = field_validator("reason_code")(_reason_code)
    _validate_reason_summary = field_validator("reason_summary")(_redacted_non_blank)
    _normalize_created_at = field_validator("created_at")(_utc)

    @model_validator(mode="after")
    def validate_identity(self) -> PlanRevisionV1:
        if self.parent_revision_id == self.revision_id:
            raise ValueError("plan revision must not parent itself")
        if self.artifact_digest != self.plan_hash:
            raise ValueError("artifact_digest must address the canonical plan bytes")
        return self


def build_plan_revision(
    plan: BaseModel | Mapping[str, object],
    *,
    revision_id: str,
    parent_revision_id: str | None,
    reason_code: str,
    reason_summary: str,
    created_at: datetime,
) -> PlanRevisionV1:
    """Build one revision from the exact bytes a later ArtifactStore will accept."""

    plan_bytes = canonical_json_bytes(plan)
    digest = hashlib.sha256(plan_bytes).hexdigest()
    safe_reason = redact_text(reason_summary.strip())
    return PlanRevisionV1(
        revision_id=revision_id,
        parent_revision_id=parent_revision_id,
        plan_hash=digest,
        reason_code=reason_code,
        reason_summary=safe_reason,
        artifact_digest=digest,
        created_at=created_at,
    )


def validate_plan_revision_lineage(
    lineage: tuple[PlanRevisionV1, ...],
) -> tuple[PlanRevisionV1, ...]:
    """Reject any lineage that is disconnected, duplicated, or out of order."""

    seen: set[str] = set()
    previous: PlanRevisionV1 | None = None
    for untrusted in lineage:
        try:
            revision = PlanRevisionV1.model_validate(untrusted.model_dump(mode="python"))
        except ValidationError as exc:
            raise ValueError("plan revision lineage contains an invalid revision") from exc
        if revision.revision_id in seen:
            raise ValueError("plan revision lineage contains a duplicate revision")
        expected_parent = previous.revision_id if previous is not None else None
        if revision.parent_revision_id != expected_parent:
            raise ValueError("plan revision lineage is disconnected")
        if previous is not None and revision.created_at < previous.created_at:
            raise ValueError("plan revision lineage timestamps move backwards")
        seen.add(revision.revision_id)
        previous = revision
    return lineage


__all__ = [
    "PlanRevisionV1",
    "build_plan_revision",
    "validate_plan_revision_lineage",
]
