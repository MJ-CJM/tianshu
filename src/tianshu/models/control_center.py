"""Strict read models for the principal-scoped Control Center snapshot."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.decision import DecisionKind
from tianshu.models.run_state import RunPhase

EvolutionStatus = Literal["not_enabled", "enabled", "degraded"]


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
    active_run_total: int = Field(ge=0, description="Total active runs before summary limit")
    unarchived_edict_total: int = Field(
        default=0,
        ge=0,
        description="Total visible Edicts without archived_at",
    )
    awaiting_follow_up_total: int = Field(
        default=0,
        ge=0,
        description="Unarchived conversation Edicts whose latest run is complete",
    )
    cancelled_edict_total: int = Field(
        default=0,
        ge=0,
        description="Unarchived Edicts in the cancelled state",
    )
    pending_decision_total: int = Field(
        ge=0,
        description="Total pending decisions before summary limit",
    )
    evidence_total: int = Field(
        ge=0,
        description="Total evidence bundles before recent summary limit",
    )
    active_runs: tuple[ControlRunSummaryV1, ...] = Field(max_length=20)
    pending_decisions: tuple[ControlDecisionSummaryV1, ...] = Field(max_length=20)
    recent_evidence: tuple[ControlEvidenceSummaryV1, ...] = Field(max_length=20)
    evolution_status: EvolutionStatus

    _normalize_generated_at = field_validator("generated_at")(_utc)

    @model_validator(mode="after")
    def validate_summary_totals(self) -> Self:
        if self.active_run_total < len(self.active_runs):
            raise ValueError("active_run_total must cover active_runs")
        if self.pending_decision_total < len(self.pending_decisions):
            raise ValueError("pending_decision_total must cover pending_decisions")
        if self.evidence_total < len(self.recent_evidence):
            raise ValueError("evidence_total must cover recent_evidence")
        if self.awaiting_follow_up_total + self.cancelled_edict_total > self.unarchived_edict_total:
            raise ValueError("workspace breakdown must not exceed unarchived_edict_total")
        return self


__all__ = [
    "ControlCenterSnapshotV1",
    "ControlDecisionSummaryV1",
    "ControlEvidenceSummaryV1",
    "ControlRunSummaryV1",
    "EvolutionStatus",
]
