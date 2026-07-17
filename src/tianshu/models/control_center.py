"""Strict read models for the principal-scoped Control Center snapshot."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from tianshu.models.decision import DecisionKind
from tianshu.models.run_state import RunPhase


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ControlRunSummaryV1(_StrictModel):
    edict_id: str
    edict_title: str
    memorial_id: str
    phase: RunPhase
    updated_at: datetime

    _normalize_time = field_validator("updated_at")(_utc)


class ControlDecisionSummaryV1(_StrictModel):
    decision_request_id: str
    edict_id: str
    edict_title: str
    memorial_id: str
    kind: DecisionKind
    expires_at: datetime
    created_at: datetime

    _normalize_times = field_validator("expires_at", "created_at")(_utc)


class ControlEvidenceSummaryV1(_StrictModel):
    bundle_id: str
    edict_id: str
    edict_title: str
    memorial_id: str
    status: Literal["open", "closed"]
    content_hash: str | None
    created_at: datetime
    closed_at: datetime | None

    _normalize_created_at = field_validator("created_at")(_utc)

    @field_validator("closed_at")
    @classmethod
    def normalize_closed_at(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def validate_closed_metadata(self) -> Self:
        if self.status == "closed" and (self.content_hash is None or self.closed_at is None):
            raise ValueError("closed evidence requires hash and closed_at")
        if self.status == "open" and (self.content_hash is not None or self.closed_at is not None):
            raise ValueError("open evidence must not expose closed metadata")
        return self


class ControlCenterSnapshotV1(_StrictModel):
    schema_version: Literal[1] = 1
    generated_at: datetime
    readiness: Literal["ready", "degraded"]
    active_runs: tuple[ControlRunSummaryV1, ...]
    pending_decisions: tuple[ControlDecisionSummaryV1, ...]
    recent_evidence: tuple[ControlEvidenceSummaryV1, ...]
    evolution_status: Literal["not_enabled", "enabled", "degraded"]

    _normalize_generated_at = field_validator("generated_at")(_utc)


__all__ = [
    "ControlCenterSnapshotV1",
    "ControlDecisionSummaryV1",
    "ControlEvidenceSummaryV1",
    "ControlRunSummaryV1",
]
