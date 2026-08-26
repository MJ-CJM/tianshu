"""Strict immutable contract for per-subject evolution governance."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.evolution_candidate import CandidateKind

type EvolutionPolicyMode = Literal["frozen", "manual", "canary"]


def _subject_key(value: str) -> str:
    if not value.strip():
        raise ValueError("subject_key must not be blank")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("updated_at must be timezone-aware")
    return value.astimezone(UTC)


class EvolutionPolicyV1(BaseModel):
    """One mutable policy row represented as an immutable CAS value."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    subject_key: str = Field(min_length=1, max_length=512)
    kind: CandidateKind
    mode: EvolutionPolicyMode
    max_canary_basis_points: int = Field(ge=0, le=1_000)
    version: int = Field(ge=1)
    updated_at: datetime

    _validate_subject_key = field_validator("subject_key")(_subject_key)
    _normalize_updated_at = field_validator("updated_at")(_utc)

    @model_validator(mode="after")
    def validate_canary_allocation(self) -> Self:
        if self.mode == "canary" and self.max_canary_basis_points == 0:
            raise ValueError("canary mode requires a positive max_canary_basis_points")
        return self


__all__ = ["EvolutionPolicyMode", "EvolutionPolicyV1"]
