"""Frozen evidence contracts for the Lean Developer Preview closure."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import canonical_sha256

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitCommit = Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]
NonBlankText = Annotated[str, Field(min_length=1)]

REQUIRED_PHASE_REPORT_IDS = (
    "s1_g1_5",
    "s2_lean",
    "s3_core",
    "s4_automation",
    "s5_lean_core",
)

REQUIRED_DEFERRED_WORK_IDS = (
    "P2-A1",
    "P2-A2",
    "P2-A3",
    "P2-A4",
    "P2-A5",
    "P2-B1",
    "P2-B2",
    "P2-C1",
    "P2-C2",
    "P2-C3",
    "P3-D1",
    "P3-D2",
    "P3-D3",
    "P3-D4",
    "P4-E1",
    "P4-E2",
    "P4-E3",
    "P4-E4",
    "P4-E5",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class LeanPreviewStepResultV1(_StrictModel):
    step_id: NonBlankText
    status: Literal["passed", "failed", "blocked", "decision_required"]
    started_at: datetime
    completed_at: datetime
    evidence_hashes: tuple[Digest, ...]
    observed_state_hash: Digest

    _normalize_times = field_validator("started_at", "completed_at")(_utc)


class LeanPreviewDemoReportV1(_StrictModel):
    schema_version: Literal[1] = 1
    batch_id: NonBlankText
    source_commit: GitCommit
    wheel_sha256: Digest
    environment_fingerprint: Digest
    fixture: bool
    steps: tuple[LeanPreviewStepResultV1, ...] = Field(min_length=1)
    evidence_bundle_id: NonBlankText
    evidence_bundle_hash: Digest
    candidate_id: NonBlankText
    gate_hash: Digest
    assignment_id: NonBlankText
    rollback_receipt_hash: Digest
    external_pending: tuple[NonBlankText, ...]
    content_hash: Digest

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        step_ids = tuple(step.step_id for step in self.steps)
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("step IDs must be unique")
        if self.fixture and not self.external_pending:
            raise ValueError("fixture demo must keep external evidence pending")
        if lean_preview_content_hash(self) != self.content_hash:
            raise ValueError("Lean Preview demo content hash mismatch")
        return self


class LeanPreviewCandidateReportV1(_StrictModel):
    schema_version: Literal[1] = 1
    source_commit: GitCommit
    phase_report_hashes: dict[str, Digest] = Field(
        min_length=len(REQUIRED_PHASE_REPORT_IDS),
        max_length=len(REQUIRED_PHASE_REPORT_IDS),
    )
    demo_report_hash: Digest
    wheel_sha256: Digest
    sdist_sha256: Digest
    capability_matrix_hash: Digest
    automation_status: Literal["passed", "failed"]
    visual_status: Literal["user_approval_pending", "user_approved"]
    visual_approval_record_hash: Digest | None
    publication_status: Literal["not_authorized"]
    deferred_work_ids: tuple[NonBlankText, ...] = Field(
        min_length=len(REQUIRED_DEFERRED_WORK_IDS),
        max_length=len(REQUIRED_DEFERRED_WORK_IDS),
    )
    content_hash: Digest

    @field_validator("phase_report_hashes", mode="before")
    @classmethod
    def validate_phase_report_hashes(cls, value: object) -> object:
        if not isinstance(value, Mapping) or set(value) != set(REQUIRED_PHASE_REPORT_IDS):
            raise ValueError("phase report hashes must be complete")
        return value

    @field_validator("deferred_work_ids", mode="before")
    @classmethod
    def validate_deferred_work_ids(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)) or tuple(value) != REQUIRED_DEFERRED_WORK_IDS:
            raise ValueError("deferred work IDs must be complete and canonical")
        return tuple(value)

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.visual_status == "user_approved" and self.visual_approval_record_hash is None:
            raise ValueError("user_approved requires a separate user approval record")
        if lean_preview_content_hash(self) != self.content_hash:
            raise ValueError("Lean Preview candidate content hash mismatch")
        return self


def lean_preview_content_hash(value: BaseModel | Mapping[str, object]) -> str:
    """Hash canonical report JSON with only its own content hash omitted."""

    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    payload.pop("content_hash", None)
    return canonical_sha256(payload)


__all__ = [
    "REQUIRED_DEFERRED_WORK_IDS",
    "REQUIRED_PHASE_REPORT_IDS",
    "LeanPreviewCandidateReportV1",
    "LeanPreviewDemoReportV1",
    "LeanPreviewStepResultV1",
    "lean_preview_content_hash",
]
