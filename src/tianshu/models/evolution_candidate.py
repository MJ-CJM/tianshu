"""Strict immutable contracts for governed evolution candidates."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import JsonValue, canonical_sha256

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"


class CandidateKind(StrEnum):
    MEMORY = "memory"
    SKILL = "skill"
    POLICY = "policy"
    PERSONA = "persona"
    CODE = "code"


class CandidateSourceChannel(StrEnum):
    API = "api"
    CLI = "cli"
    AGENT = "agent"
    REVIEWER = "reviewer"
    CURATOR = "curator"
    ZIP = "zip"
    SYSTEM = "system"


class CandidateLifecycle(StrEnum):
    PROPOSED = "proposed"
    STAGED = "staged"
    EVALUATING = "evaluating"
    BLOCKED = "blocked"
    READY = "ready"
    CANARY = "canary"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"
    ARCHIVED = "archived"


class GateName(StrEnum):
    SCHEMA = "schema"
    SECURITY = "security"
    REGRESSION = "regression"
    SAMPLE = "sample"
    EVIDENCE = "evidence"
    BUDGET = "budget"
    ROLLBACK = "rollback"
    HUMAN_VETO = "human_veto"


LEGAL_LIFECYCLE_TRANSITIONS: dict[CandidateLifecycle, frozenset[CandidateLifecycle]] = {
    CandidateLifecycle.PROPOSED: frozenset({CandidateLifecycle.STAGED}),
    CandidateLifecycle.STAGED: frozenset({CandidateLifecycle.EVALUATING}),
    CandidateLifecycle.EVALUATING: frozenset(
        {CandidateLifecycle.BLOCKED, CandidateLifecycle.READY}
    ),
    CandidateLifecycle.BLOCKED: frozenset(
        {CandidateLifecycle.EVALUATING, CandidateLifecycle.REJECTED}
    ),
    CandidateLifecycle.READY: frozenset({CandidateLifecycle.CANARY, CandidateLifecycle.REJECTED}),
    CandidateLifecycle.CANARY: frozenset(
        {
            CandidateLifecycle.READY,
            CandidateLifecycle.PROMOTED,
            CandidateLifecycle.REJECTED,
            CandidateLifecycle.ROLLBACK_PENDING,
        }
    ),
    CandidateLifecycle.PROMOTED: frozenset(
        {CandidateLifecycle.ROLLBACK_PENDING, CandidateLifecycle.ARCHIVED}
    ),
    CandidateLifecycle.ROLLBACK_PENDING: frozenset({CandidateLifecycle.ROLLED_BACK}),
    CandidateLifecycle.ROLLED_BACK: frozenset({CandidateLifecycle.ARCHIVED}),
    CandidateLifecycle.REJECTED: frozenset({CandidateLifecycle.ARCHIVED}),
    CandidateLifecycle.ARCHIVED: frozenset(),
}


def validate_lifecycle_transition(source: CandidateLifecycle, target: CandidateLifecycle) -> None:
    """Reject lifecycle edges outside the frozen governed graph."""

    if target not in LEGAL_LIFECYCLE_TRANSITIONS[source]:
        raise ValueError(f"illegal lifecycle transition: {source.value} -> {target.value}")


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _optional_non_blank(value: str | None) -> str | None:
    return _non_blank(value) if value is not None else None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class EvolutionProvenanceV1(_StrictModel):
    schema_version: Literal[1] = 1
    source_channel: CandidateSourceChannel
    source_uri_redacted: str | None
    source_digest: str = Field(pattern=_DIGEST_PATTERN)
    actor_principal_id: str
    actor_display_name: str
    originating_edict_id: str | None
    originating_memorial_id: str | None
    producer_name: str
    producer_version: str
    received_at: datetime

    _validate_required_text = field_validator(
        "actor_principal_id", "actor_display_name", "producer_name", "producer_version"
    )(_non_blank)
    _validate_optional_text = field_validator(
        "source_uri_redacted", "originating_edict_id", "originating_memorial_id"
    )(_optional_non_blank)
    _normalize_received_at = field_validator("received_at")(_utc)


class CandidateVersionRefV1(_StrictModel):
    version: str
    artifact_digest: str = Field(pattern=_DIGEST_PATTERN)
    canonical_digest: str = Field(pattern=_DIGEST_PATTERN)

    _validate_version = field_validator("version")(_non_blank)


class RoutingPolicyV1(_StrictModel):
    allocation_basis_points: int = Field(ge=0, le=10_000)
    allocation_seed_id: str
    routing_version: int = Field(ge=1)

    _validate_seed = field_validator("allocation_seed_id")(_non_blank)


class RollbackSpecV1(_StrictModel):
    champion_ref: CandidateVersionRefV1
    restore_point_ref: str
    adapter_name: str
    max_seconds: int = Field(gt=0, le=60)

    _validate_text = field_validator("restore_point_ref", "adapter_name")(_non_blank)


class EvolutionContractV1(_StrictModel):
    schema_version: Literal[1] = 1
    kind: CandidateKind
    subject_key: str
    governance_contract_hash: str = Field(pattern=_DIGEST_PATTERN)
    required_gates: tuple[GateName, ...]
    regression_policy_artifact_digest: str = Field(pattern=_DIGEST_PATTERN)
    sample_policy_artifact_digest: str = Field(pattern=_DIGEST_PATTERN)
    budget_policy_artifact_digest: str = Field(pattern=_DIGEST_PATTERN)
    minimum_canary_samples: int = Field(gt=0)
    max_canary_allocation_basis_points: int = Field(gt=0, le=1_000)
    rollback_slo_seconds: int = Field(gt=0, le=60)
    automatic_promotion_allowed: Literal[False] = False

    _validate_subject_key = field_validator("subject_key")(_non_blank)

    @field_validator("required_gates")
    @classmethod
    def validate_required_gates(cls, values: tuple[GateName, ...]) -> tuple[GateName, ...]:
        if not values:
            raise ValueError("required_gates must not be empty")
        if len(set(values)) != len(values):
            raise ValueError("required_gates must be unique")
        return values


class CandidateSourceV1(_StrictModel):
    schema_version: Literal[1] = 1
    version: str
    payload: dict[str, JsonValue]

    _validate_version = field_validator("version")(_non_blank)


class ProvenanceInputV1(_StrictModel):
    schema_version: Literal[1] = 1
    source_channel: CandidateSourceChannel
    source_uri_redacted: str | None
    actor_principal_id: str
    actor_display_name: str
    originating_edict_id: str | None
    originating_memorial_id: str | None
    producer_name: str
    producer_version: str


class CandidateProposalV1(_StrictModel):
    schema_version: Literal[1] = 1
    command_id: str
    kind: CandidateKind
    subject_key: str
    base: CandidateSourceV1
    candidate: CandidateSourceV1
    evolution_contract: EvolutionContractV1
    provenance: ProvenanceInputV1
    evidence_bundle_ids: tuple[str, ...]
    restore_point_ref: str

    _validate_identity = field_validator("command_id", "subject_key", "restore_point_ref")(
        _non_blank
    )

    @field_validator("evidence_bundle_ids")
    @classmethod
    def validate_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError("evidence bundle IDs must be unique and non-blank")
        return values

    @model_validator(mode="after")
    def validate_contract_binding(self) -> Self:
        if (
            self.kind is not self.evolution_contract.kind
            or self.subject_key != self.evolution_contract.subject_key
        ):
            raise ValueError("proposal kind and subject_key must match evolution contract")
        return self


class EvolutionCandidateV1(_StrictModel):
    schema_version: Literal[1] = 1
    candidate_id: str
    kind: CandidateKind
    subject_key: str
    provenance: EvolutionProvenanceV1
    base: CandidateVersionRefV1
    candidate: CandidateVersionRefV1
    diff_artifact_digest: str = Field(pattern=_DIGEST_PATTERN)
    evolution_contract: EvolutionContractV1
    evolution_contract_hash: str = Field(pattern=_DIGEST_PATTERN)
    gate_snapshot_version: int = Field(ge=0)
    evidence_bundle_ids: tuple[str, ...]
    routing: RoutingPolicyV1 | None
    rollback: RollbackSpecV1
    lifecycle: CandidateLifecycle
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    _validate_identity = field_validator("candidate_id", "subject_key")(_non_blank)
    _normalize_times = field_validator("created_at", "updated_at")(_utc)

    @field_validator("evidence_bundle_ids")
    @classmethod
    def validate_evidence_bundle_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("evidence_bundle_ids must not contain blank values")
        if len(set(values)) != len(values):
            raise ValueError("evidence_bundle_ids must be unique")
        return values

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if (
            self.kind is not self.evolution_contract.kind
            or self.subject_key != self.evolution_contract.subject_key
        ):
            raise ValueError("candidate kind and subject_key must match evolution contract")
        if self.evolution_contract_hash != canonical_sha256(self.evolution_contract):
            raise ValueError("evolution_contract_hash does not match evolution_contract")
        if self.rollback.champion_ref != self.base:
            raise ValueError("rollback champion_ref must match base")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.routing is not None and self.routing.allocation_basis_points > (
            self.evolution_contract.max_canary_allocation_basis_points
        ):
            raise ValueError("routing allocation exceeds evolution contract canary ceiling")
        return self


__all__ = [
    "CandidateKind",
    "CandidateLifecycle",
    "CandidateProposalV1",
    "CandidateSourceV1",
    "CandidateSourceChannel",
    "CandidateVersionRefV1",
    "EvolutionCandidateV1",
    "EvolutionContractV1",
    "EvolutionProvenanceV1",
    "GateName",
    "LEGAL_LIFECYCLE_TRANSITIONS",
    "ProvenanceInputV1",
    "RollbackSpecV1",
    "RoutingPolicyV1",
    "validate_lifecycle_transition",
]
