"""Frozen evidence contracts for the Lean Developer Preview closure."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from tianshu.models.canonical import canonical_sha256

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_GIT_COMMIT_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_AWARE_RFC3339_PATTERN = (
    r"^(?:"
    r"(?:(?!0000)[0-9]{4})-(?:"
    r"(?:01|03|05|07|08|10|12)-(?:0[1-9]|[12][0-9]|3[01])|"
    r"(?:04|06|09|11)-(?:0[1-9]|[12][0-9]|30)|"
    r"02-(?:0[1-9]|1[0-9]|2[0-8])"
    r")|"
    r"(?:[0-9]{2}(?:0[48]|[2468][048]|[13579][26])|"
    r"(?:0[48]|[2468][048]|[13579][26])00)-02-29"
    r")T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))"
    r"(?![\s\S])"
)
_AWARE_RFC3339 = re.compile(_AWARE_RFC3339_PATTERN)

Digest = Annotated[str, Field(pattern=_DIGEST_PATTERN)]
GitCommit = Annotated[str, Field(pattern=_GIT_COMMIT_PATTERN)]
NonBlankText = Annotated[str, Field(min_length=1)]

REQUIRED_DEMO_STEP_IDS = (
    "doctor_ready",
    "submit_governed_edict",
    "observe_decision_required",
    "resolve_decision_with_reason",
    "observe_completed_run",
    "verify_evidence_bundle",
    "propose_skill_candidate",
    "evaluate_candidate_gate",
    "start_skill_canary",
    "submit_canary_eligible_run",
    "verify_real_candidate_overlay",
    "rollback_candidate",
    "verify_new_run_uses_champion",
)

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


def _parse_aware_rfc3339(value: object) -> object:
    if isinstance(value, str):
        if _AWARE_RFC3339.fullmatch(value) is None:
            raise ValueError("timestamp must use canonical aware RFC3339 syntax")
        return datetime.fromisoformat(value)
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class LeanPreviewStepResultV1(_StrictModel):
    step_id: NonBlankText
    status: Literal["passed", "failed", "blocked", "decision_required"]
    started_at: datetime
    completed_at: datetime
    evidence_hashes: tuple[Digest, ...]
    observed_state_hash: Digest

    _validate_lexical_times = field_validator("started_at", "completed_at", mode="before")(
        _parse_aware_rfc3339
    )
    _normalize_times = field_validator("started_at", "completed_at")(_utc)

    @model_validator(mode="after")
    def validate_time_order(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        return self


class LeanPreviewDemoReportV1(_StrictModel):
    schema_version: Literal[1] = 1
    batch_id: NonBlankText
    source_commit: GitCommit
    wheel_sha256: Digest
    environment_fingerprint: Digest
    fixture: bool
    steps: tuple[LeanPreviewStepResultV1, ...] = Field(
        min_length=len(REQUIRED_DEMO_STEP_IDS),
        max_length=len(REQUIRED_DEMO_STEP_IDS),
    )
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
        if tuple(step.step_id for step in self.steps) != REQUIRED_DEMO_STEP_IDS:
            raise ValueError("Lean Preview demo steps must be complete, unique, and canonical")
        if self.fixture and not self.external_pending:
            raise ValueError("fixture demo must keep external evidence pending")
        if lean_preview_content_hash(self) != self.content_hash:
            raise ValueError("Lean Preview demo content hash mismatch")
        return self


class LeanPreviewVisualApprovalRecordV1(_StrictModel):
    schema_version: Literal[1] = 1
    approval_id: NonBlankText
    approval_kind: Literal["explicit_user_review"]
    decision: Literal["approved"]
    approved_by: NonBlankText
    approved_at: datetime
    source_commit: GitCommit
    demo_report_hash: Digest
    content_hash: Digest

    _validate_approved_at_syntax = field_validator("approved_at", mode="before")(
        _parse_aware_rfc3339
    )
    _normalize_approved_at = field_validator("approved_at")(_utc)

    @model_validator(mode="after")
    def validate_content_hash(self) -> Self:
        if lean_preview_content_hash(self) != self.content_hash:
            raise ValueError("Lean Preview approval record content hash mismatch")
        return self


class LeanPreviewCandidateReportV1(_StrictModel):
    schema_version: Literal[1] = 1
    source_commit: GitCommit
    phase_report_hashes: dict[str, Digest] = Field(
        min_length=len(REQUIRED_PHASE_REPORT_IDS),
        max_length=len(REQUIRED_PHASE_REPORT_IDS),
    )
    demo_report_ref: NonBlankText
    demo_report_hash: Digest
    wheel_sha256: Digest
    sdist_sha256: Digest
    capability_matrix_hash: Digest
    automation_status: Literal["passed", "failed"]
    visual_status: Literal["user_approval_pending", "user_approved"]
    visual_approval_record_ref: NonBlankText | None
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
        approval_values = (
            self.visual_approval_record_ref,
            self.visual_approval_record_hash,
        )
        if self.visual_status == "user_approval_pending" and approval_values != (None, None):
            raise ValueError("pending visual approval must not carry an approval record")
        if self.visual_status == "user_approved" and None in approval_values:
            raise ValueError("user_approved requires a resolvable user approval record")
        if lean_preview_content_hash(self) != self.content_hash:
            raise ValueError("Lean Preview candidate content hash mismatch")
        return self


class ResolvedLeanPreviewCandidateArtifactsV1(_StrictModel):
    demo_report: LeanPreviewDemoReportV1
    visual_approval_record: LeanPreviewVisualApprovalRecordV1 | None


def lean_preview_content_hash(value: BaseModel | Mapping[str, object]) -> str:
    """Hash canonical report JSON with only its own content hash omitted."""

    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    payload.pop("content_hash", None)
    return canonical_sha256(payload)


def _schema_for(model: type[BaseModel], filename: str) -> dict[str, object]:
    schema = model.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"https://tianshu.dev/schemas/{filename}"
    return schema


def lean_preview_demo_report_schema() -> dict[str, object]:
    """Return the deterministic behavioral JSON Schema for demo reports."""

    schema = _schema_for(
        LeanPreviewDemoReportV1,
        "lean-preview-demo-report-v1.schema.json",
    )
    definitions = cast(dict[str, object], schema["$defs"])
    step_schema = cast(dict[str, object], definitions["LeanPreviewStepResultV1"])
    step_properties = cast(dict[str, object], step_schema["properties"])
    for field_name in ("started_at", "completed_at"):
        timestamp_schema = cast(dict[str, object], step_properties[field_name])
        timestamp_schema["pattern"] = _AWARE_RFC3339_PATTERN
    properties = cast(dict[str, object], schema["properties"])
    properties["steps"] = {
        "allOf": [
            {
                "items": {"$ref": "#/$defs/LeanPreviewStepResultV1"},
            },
            {
                "items": False,
                "maxItems": len(REQUIRED_DEMO_STEP_IDS),
                "minItems": len(REQUIRED_DEMO_STEP_IDS),
                "prefixItems": [
                    {
                        "properties": {"step_id": {"const": step_id}},
                        "required": ["step_id"],
                    }
                    for step_id in REQUIRED_DEMO_STEP_IDS
                ],
            },
        ],
        "title": "Steps",
        "type": "array",
    }
    schema["allOf"] = [
        {
            "if": {
                "properties": {"fixture": {"const": True}},
                "required": ["fixture"],
            },
            "then": {
                "properties": {"external_pending": {"minItems": 1}},
                "required": ["external_pending"],
            },
        }
    ]
    return schema


def lean_preview_candidate_report_schema() -> dict[str, object]:
    """Return the deterministic behavioral JSON Schema for candidate reports."""

    schema = _schema_for(
        LeanPreviewCandidateReportV1,
        "lean-preview-candidate-report-v1.schema.json",
    )
    properties = cast(dict[str, object], schema["properties"])
    properties["phase_report_hashes"] = {
        "additionalProperties": False,
        "maxProperties": len(REQUIRED_PHASE_REPORT_IDS),
        "minProperties": len(REQUIRED_PHASE_REPORT_IDS),
        "properties": {
            phase_id: {"pattern": _DIGEST_PATTERN, "type": "string"}
            for phase_id in REQUIRED_PHASE_REPORT_IDS
        },
        "required": list(REQUIRED_PHASE_REPORT_IDS),
        "title": "Phase Report Hashes",
        "type": "object",
    }
    properties["deferred_work_ids"] = {
        "items": False,
        "maxItems": len(REQUIRED_DEFERRED_WORK_IDS),
        "minItems": len(REQUIRED_DEFERRED_WORK_IDS),
        "prefixItems": [{"const": work_id} for work_id in REQUIRED_DEFERRED_WORK_IDS],
        "title": "Deferred Work Ids",
        "type": "array",
    }
    schema["allOf"] = [
        {
            "if": {
                "properties": {"visual_status": {"const": "user_approved"}},
                "required": ["visual_status"],
            },
            "then": {
                "properties": {
                    "visual_approval_record_ref": {"minLength": 1, "type": "string"},
                    "visual_approval_record_hash": {
                        "pattern": _DIGEST_PATTERN,
                        "type": "string",
                    },
                },
                "required": [
                    "visual_approval_record_ref",
                    "visual_approval_record_hash",
                ],
            },
            "else": {
                "properties": {
                    "visual_approval_record_ref": {"type": "null"},
                    "visual_approval_record_hash": {"type": "null"},
                },
                "required": [
                    "visual_approval_record_ref",
                    "visual_approval_record_hash",
                ],
            },
        }
    ]
    return schema


def _resolve_json_artifact(
    artifact_root: Path,
    reference: str,
    label: str,
    model: type[BaseModel],
) -> BaseModel:
    root = artifact_root
    relative = PurePosixPath(reference)
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{label} artifact root must be a real directory")
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"{label} reference must stay inside the artifact root")
    path = root.joinpath(*relative.parts)
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} reference must not contain symlinks")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} artifact does not exist") from exc
    if not resolved_path.is_relative_to(resolved_root) or not resolved_path.is_file():
        raise ValueError(f"{label} reference must resolve to a regular file")
    try:
        return model.model_validate_json(resolved_path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ValueError(f"{label} artifact is invalid") from exc


def resolve_lean_preview_candidate_artifacts(
    candidate: LeanPreviewCandidateReportV1,
    artifact_root: Path,
) -> ResolvedLeanPreviewCandidateArtifactsV1:
    """Resolve and verify the demo and optional explicit user-approval artifacts."""

    demo = _resolve_json_artifact(
        artifact_root,
        candidate.demo_report_ref,
        "demo report",
        LeanPreviewDemoReportV1,
    )
    if not isinstance(demo, LeanPreviewDemoReportV1):  # pragma: no cover - type narrowing
        raise TypeError("resolved demo report has the wrong model type")
    if demo.fixture:
        raise ValueError("demo report fixture cannot qualify a Lean Preview candidate")
    if demo.content_hash != candidate.demo_report_hash:
        raise ValueError("demo report hash does not match candidate")
    if demo.source_commit != candidate.source_commit:
        raise ValueError("demo report source commit does not match candidate")
    if demo.wheel_sha256 != candidate.wheel_sha256:
        raise ValueError("demo report Wheel hash does not match candidate")

    approval: LeanPreviewVisualApprovalRecordV1 | None = None
    if candidate.visual_status == "user_approved":
        reference = candidate.visual_approval_record_ref
        expected_hash = candidate.visual_approval_record_hash
        if reference is None or expected_hash is None:  # guarded by model validation
            raise ValueError("approval record reference and hash are required")
        resolved_approval = _resolve_json_artifact(
            artifact_root,
            reference,
            "approval record",
            LeanPreviewVisualApprovalRecordV1,
        )
        if not isinstance(resolved_approval, LeanPreviewVisualApprovalRecordV1):
            raise TypeError("resolved approval record has the wrong model type")
        approval = resolved_approval
        if approval.content_hash != expected_hash:
            raise ValueError("approval record hash does not match candidate")
        if approval.source_commit != candidate.source_commit:
            raise ValueError("approval record source commit does not match candidate")
        if approval.demo_report_hash != candidate.demo_report_hash:
            raise ValueError("approval record demo hash does not match candidate")

    return ResolvedLeanPreviewCandidateArtifactsV1(
        demo_report=demo,
        visual_approval_record=approval,
    )


__all__ = [
    "REQUIRED_DEFERRED_WORK_IDS",
    "REQUIRED_DEMO_STEP_IDS",
    "REQUIRED_PHASE_REPORT_IDS",
    "LeanPreviewCandidateReportV1",
    "LeanPreviewDemoReportV1",
    "LeanPreviewStepResultV1",
    "LeanPreviewVisualApprovalRecordV1",
    "ResolvedLeanPreviewCandidateArtifactsV1",
    "lean_preview_candidate_report_schema",
    "lean_preview_content_hash",
    "lean_preview_demo_report_schema",
    "resolve_lean_preview_candidate_artifacts",
]
