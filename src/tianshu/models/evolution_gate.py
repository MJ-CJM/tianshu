"""Immutable gate-result contracts shared by evolution and install services."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import GateName

REQUIRED_GATES: tuple[GateName, ...] = tuple(GateName)


class GateStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"
    ERROR = "error"
    MISSING = "missing"


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class EvolutionGateResultV1(_StrictModel):
    schema_version: Literal[1] = 1
    gate: GateName
    status: GateStatus
    reason_code: str
    evidence_hashes: tuple[str, ...] = ()

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gate reason code must not be blank")
        return value


class EvolutionGateReportV1(_StrictModel):
    schema_version: Literal[1] = 1
    candidate_id: str
    candidate_version: int = Field(ge=1)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_snapshot_version: int = Field(ge=1)
    results: tuple[EvolutionGateResultV1, ...]
    promotion_allowed: bool
    blocking_gates: tuple[GateName, ...]
    evidence_bundle_ids: tuple[str, ...]
    evaluated_at: datetime

    @property
    def report_hash(self) -> str:
        return canonical_sha256(self)

    @field_validator("evaluated_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if tuple(result.gate for result in self.results) != REQUIRED_GATES:
            raise ValueError("gate results must be complete and canonical")
        derived = tuple(
            result.gate for result in self.results if result.status is not GateStatus.PASSED
        )
        if self.blocking_gates != derived or self.promotion_allowed != (not derived):
            raise ValueError("gate decision must be derived from current results")
        return self

    @classmethod
    def from_results(
        cls,
        *,
        candidate_id: str,
        candidate_version: int,
        candidate_digest: str,
        gate_snapshot_version: int,
        results: tuple[EvolutionGateResultV1, ...],
        evidence_bundle_ids: tuple[str, ...],
        evaluated_at: datetime | str,
    ) -> EvolutionGateReportV1:
        by_gate = {result.gate: result for result in results}
        complete = tuple(
            by_gate.get(
                gate,
                EvolutionGateResultV1(
                    gate=gate,
                    status=GateStatus.MISSING,
                    reason_code="evidence_missing",
                ),
            )
            for gate in REQUIRED_GATES
        )
        blocking = tuple(
            result.gate for result in complete if result.status is not GateStatus.PASSED
        )
        return cls.model_validate(
            {
                "candidate_id": candidate_id,
                "candidate_version": candidate_version,
                "candidate_digest": candidate_digest,
                "gate_snapshot_version": gate_snapshot_version,
                "results": complete,
                "promotion_allowed": not blocking,
                "blocking_gates": blocking,
                "evidence_bundle_ids": evidence_bundle_ids,
                "evaluated_at": evaluated_at,
            }
        )


__all__ = [
    "EvolutionGateReportV1",
    "EvolutionGateResultV1",
    "GateStatus",
    "REQUIRED_GATES",
]
