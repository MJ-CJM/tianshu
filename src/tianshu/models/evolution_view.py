"""Strict read contract for the pre-S5 Evolution Center."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_EVIDENCE_BUNDLE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EvolutionGateSummaryV1(_StrictModel):
    code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+$")
    status: Literal["pending", "passed", "failed", "error", "missing"]
    blocking: bool
    current: float | None = None
    required: float | None = None
    evidence_bundle_id: str | None = Field(
        default=None,
        pattern=_EVIDENCE_BUNDLE_ID_PATTERN,
    )
    evidence_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_gate_truth(self) -> Self:
        if (self.current is None) != (self.required is None):
            raise ValueError("current and required must be present together")
        if (self.evidence_bundle_id is None) != (self.evidence_hash is None):
            raise ValueError("evidence_bundle_id and evidence_hash must be present together")
        if self.status == "passed" and self.blocking:
            raise ValueError("a passed gate cannot be blocking")
        return self


class EvolutionCandidateSummaryV1(_StrictModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    kind: Literal["memory", "skill", "policy", "persona", "code", "executor"]
    version: int = Field(ge=1)
    lifecycle: Literal[
        "proposed",
        "staged",
        "evaluating",
        "blocked",
        "ready",
        "canary",
        "promoted",
        "rejected",
        "rollback_pending",
        "rolled_back",
        "archived",
    ]
    artifact_hash: str = Field(pattern=_SHA256_PATTERN)
    promotion_allowed: bool
    rollback_state: Literal["not_required", "ready", "pending", "completed", "failed"]
    gates: tuple[EvolutionGateSummaryV1, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def validate_promotion_truth(self) -> Self:
        gate_codes = [gate.code for gate in self.gates]
        if len(set(gate_codes)) != len(gate_codes):
            raise ValueError("gate codes must be unique")
        if self.promotion_allowed and any(gate.blocking for gate in self.gates):
            raise ValueError("promotion cannot be allowed while a gate is blocking")
        return self


class EvolutionRoutingSummaryV1(_StrictModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    subject_key: str = Field(min_length=1, max_length=512, pattern=r".*\S.*")
    routing_version: int = Field(ge=1)
    allocation_percent: float = Field(ge=0, le=100)
    champion_assignment_count: int = Field(ge=0)
    challenger_assignment_count: int = Field(ge=0)


class EvolutionCenterSnapshotV1(_StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["not_enabled", "enabled", "degraded"]
    reason_code: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+$")
    routing_enabled: bool = True
    candidates: tuple[EvolutionCandidateSummaryV1, ...] = Field(default=(), max_length=100)
    routing: tuple[EvolutionRoutingSummaryV1, ...] = Field(default=(), max_length=100)
    last_gate_hash: str | None = Field(default=None, pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_snapshot_truth(self) -> Self:
        if self.status == "not_enabled" and (
            self.candidates or self.routing or self.last_gate_hash is not None
        ):
            raise ValueError("not_enabled snapshot cannot contain evolution data")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError("candidate ids must be unique")
        routing_candidate_ids = [item.candidate_id for item in self.routing]
        if len(set(routing_candidate_ids)) != len(routing_candidate_ids):
            raise ValueError("routing candidate ids must be unique")
        if any(candidate_id not in candidate_ids for candidate_id in routing_candidate_ids):
            raise ValueError("routing must reference a snapshot candidate")
        return self


__all__ = [
    "EvolutionCandidateSummaryV1",
    "EvolutionCenterSnapshotV1",
    "EvolutionGateSummaryV1",
    "EvolutionRoutingSummaryV1",
]
