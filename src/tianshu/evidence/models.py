"""Strict immutable contracts for content-addressed run evidence."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.governance_contract import (
    CAPABILITY_IDS,
    CapabilityId,
    EffectiveGovernanceContractV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.plan_revision import PlanRevisionV1
from tianshu.security.redact import redact_text
from tianshu.security.sensitive_payload import contains_raw_sensitive_payload

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_STABLE_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_COMMAND_BYTES = 16384


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("value must be an opaque identifier")
    return value


def _stable_code(value: str) -> str:
    if not _STABLE_CODE.fullmatch(value):
        raise ValueError("value must be a stable lowercase code")
    return value


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _optional_non_blank(value: str | None) -> str | None:
    return _non_blank(value) if value is not None else None


def _redacted(value: str) -> str:
    value = _non_blank(value.strip())
    if redact_text(value) != value or contains_raw_sensitive_payload(value):
        raise ValueError("value must already be redacted")
    return value


def _redacted_identifier(value: str) -> str:
    return _identifier(_redacted(value))


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ArtifactRefV1(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=255)
    redaction: str = Field(min_length=1, max_length=128)
    uri: str
    root_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_uri(self) -> Self:
        if self.uri != f"artifact://sha256/{self.digest}":
            raise ValueError("artifact URI must be derived from its digest")
        return self


class CheckEvidenceV1(_StrictModel):
    check_id: str
    name: str
    status: Literal["passed", "failed", "unavailable", "skipped"]
    command_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    exit_code: int | None
    output_artifact_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    started_at: datetime
    completed_at: datetime

    _validate_identifiers = field_validator("check_id")(_identifier)
    _validate_name = field_validator("name")(_non_blank)
    _normalize_times = field_validator("started_at", "completed_at")(_utc)

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("check completed_at must not precede started_at")
        return self


class DecisionEvidenceV1(_StrictModel):
    decision_request_id: str
    kind: Literal["tool", "outer_loop", "plan_review", "governed_apply"]
    action: str
    actor_principal_id: str
    reason: str = Field(max_length=4096)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_at: datetime

    _validate_ids = field_validator("decision_request_id", "actor_principal_id")(_identifier)
    _validate_action = field_validator("action")(_stable_code)
    _validate_reason = field_validator("reason")(_redacted)
    _normalize_resolved_at = field_validator("resolved_at")(_utc)


class EffectEvidenceV1(_StrictModel):
    intent_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_id: str
    status: Literal["intended", "receipted", "uncertain"]
    semantics: Literal[
        "provider_idempotent",
        "receipt_lookup",
        "workspace_only",
        "untracked_external",
        "opaque_cli",
    ]
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason_code: str | None

    _validate_effect_id = field_validator("effect_id")(_identifier)
    _validate_reason = field_validator("reason_code")(_optional_non_blank)


class CostEvidenceV1(_StrictModel):
    currency: Literal["CNY"] = "CNY"
    requested_budget: Decimal | None = Field(default=None, ge=0)
    effective_budget: Decimal | None = Field(default=None, ge=0)
    actual_cost: Decimal = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)


class EnvironmentEvidenceV1(_StrictModel):
    tianshu_version: str
    python_version: str
    platform: str
    architecture: str
    dependency_lock_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_base_revision: str | None
    environment_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    _validate_text = field_validator(
        "tianshu_version", "python_version", "platform", "architecture"
    )(_non_blank)


class AuditorConclusionV1(_StrictModel):
    auditor_id: str
    verdict: Literal["pass", "fail"]
    reason: str = Field(max_length=4096)
    required_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    evaluated_at: datetime

    _validate_auditor = field_validator("auditor_id")(_identifier)
    _validate_reason = field_validator("reason")(_redacted)
    _normalize_time = field_validator("evaluated_at")(_utc)


class ReproductionCommandV1(_StrictModel):
    label: str = Field(max_length=256)
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    cwd_ref: str = Field(max_length=256)
    environment_keys: tuple[str, ...] = Field(max_length=64)
    expected_result_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    _validate_label = field_validator("label")(_redacted)
    _validate_cwd = field_validator("cwd_ref")(_redacted_identifier)

    @model_validator(mode="after")
    def validate_command(self) -> Self:
        if any(not value or len(value) > 4096 or "\x00" in value for value in self.argv):
            raise ValueError("reproduction argv is invalid or too large")
        if len(set(self.environment_keys)) != len(self.environment_keys):
            raise ValueError("reproduction environment keys must be unique")
        if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) for key in self.environment_keys):
            raise ValueError("reproduction environment keys must be names, not values")
        payload = {"argv": list(self.argv), "environment_keys": list(self.environment_keys)}
        if contains_raw_sensitive_payload(payload):
            raise ValueError("reproduction command must not contain a raw secret")
        if len(canonical_json_bytes(payload)) > _MAX_COMMAND_BYTES:
            raise ValueError("reproduction command is too large")
        return self


class EvidenceRequirementsV1(_StrictModel):
    check_names: tuple[str, ...]
    decision_request_ids: tuple[str, ...]
    effect_intent_ids: tuple[str, ...]
    artifact_digests: tuple[str, ...]

    @field_validator("artifact_digests")
    @classmethod
    def validate_digests(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(_DIGEST.fullmatch(value) is None for value in values):
            raise ValueError("required artifact digest is invalid")
        return tuple(sorted(set(values)))

    @field_validator("check_names", "decision_request_ids", "effect_intent_ids")
    @classmethod
    def normalize_values(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(values)))


class ExecutorCapabilityEvidenceV1(_StrictModel):
    schema_version: Literal["1"] = "1"
    capability: CapabilityId
    state: Literal["enforced", "best_effort", "observed", "unsupported"]
    evidence: tuple[str, ...]

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if not self.evidence or tuple(sorted(set(self.evidence))) != self.evidence:
            raise ValueError("executor capability evidence must be non-empty and canonical")
        return self


class ExecutorManifestEvidenceV1(_StrictModel):
    schema_version: Literal["1"] = "1"
    manifest_id: str
    manifest_version: str
    adapter_id: str
    display_name: str
    level: Literal["managed", "contained", "observe-only"]
    experimental: bool
    execution_modes: tuple[Literal["single", "dag", "outer_loop"], ...]
    capabilities: tuple[ExecutorCapabilityEvidenceV1, ...]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        capability_ids = tuple(item.capability for item in self.capabilities)
        if capability_ids != tuple(sorted(CAPABILITY_IDS)):
            raise ValueError("executor capabilities must be complete and canonical")
        if len(set(self.execution_modes)) != len(self.execution_modes):
            raise ValueError("executor execution modes must be unique")
        return self

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(canonical_json_bytes(self)).hexdigest()


class EvidenceSnapshotV1(_StrictModel):
    run_state_version: int = Field(ge=1)
    requested_contract: RequestedGovernanceContractV1
    requested_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_contract: EffectiveGovernanceContractV1
    effective_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    executor_manifest: ExecutorManifestEvidenceV1
    executor_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_revision: PlanRevisionV1
    artifacts: tuple[ArtifactRefV1, ...]
    checks: tuple[CheckEvidenceV1, ...]
    decisions: tuple[DecisionEvidenceV1, ...]
    effects: tuple[EffectEvidenceV1, ...]
    cost: CostEvidenceV1
    environment: EnvironmentEvidenceV1
    auditor: AuditorConclusionV1
    requirements: EvidenceRequirementsV1
    reproduction_command: ReproductionCommandV1

    @model_validator(mode="after")
    def validate_hashes(self) -> Self:
        if self.requested_contract_hash != self.requested_contract.content_hash:
            raise ValueError("requested contract hash mismatch")
        if self.effective_contract_hash != self.effective_contract.content_hash:
            raise ValueError("effective contract hash mismatch")
        if self.executor_manifest_hash != self.executor_manifest.content_hash:
            raise ValueError("executor manifest hash mismatch")
        if self.executor_manifest_hash != self.effective_contract.executor_manifest_hash:
            raise ValueError("effective contract executor manifest hash mismatch")
        return self


class EvidenceBundleV1(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: str
    edict_id: str
    memorial_id: str
    status: Literal["open"] = "open"
    snapshot: EvidenceSnapshotV1
    version: int = Field(ge=1)
    created_at: datetime

    _validate_ids = field_validator("bundle_id", "edict_id", "memorial_id")(_identifier)
    _normalize_time = field_validator("created_at")(_utc)


class ClosedEvidenceBundleV1(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: str
    edict_id: str
    memorial_id: str
    status: Literal["closed"] = "closed"
    snapshot: EvidenceSnapshotV1
    version: int = Field(ge=2)
    created_at: datetime
    closed_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    _validate_ids = field_validator("bundle_id", "edict_id", "memorial_id")(_identifier)
    _normalize_times = field_validator("created_at", "closed_at")(_utc)

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if self.closed_at < self.created_at:
            raise ValueError("closed_at must not precede created_at")
        if closed_bundle_content_hash(self) != self.content_hash:
            raise ValueError("closed evidence content hash mismatch")
        return self


class EvidenceVerificationV1(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    bundle_id: str
    verified: bool
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_count: int = Field(ge=0)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=64)

    _validate_id = field_validator("bundle_id")(_identifier)


def closed_bundle_content_hash(
    value: BaseModel | Mapping[str, object],
) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    payload.pop("content_hash", None)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


__all__ = [
    "ArtifactRefV1",
    "AuditorConclusionV1",
    "CheckEvidenceV1",
    "ClosedEvidenceBundleV1",
    "CostEvidenceV1",
    "DecisionEvidenceV1",
    "EffectEvidenceV1",
    "EnvironmentEvidenceV1",
    "EvidenceBundleV1",
    "EvidenceRequirementsV1",
    "EvidenceSnapshotV1",
    "EvidenceVerificationV1",
    "ExecutorCapabilityEvidenceV1",
    "ExecutorManifestEvidenceV1",
    "ReproductionCommandV1",
    "closed_bundle_content_hash",
]
