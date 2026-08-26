"""Immutable per-run governed evolution attribution contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind, CandidateVersionRefV1

_DIGEST = r"^[0-9a-f]{64}$"


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


class RunAssignmentV1(_StrictModel):
    """One durable routing decision whose Memorial identity is never re-bucketed."""

    assignment_id: str
    memorial_id: str
    candidate_id: str
    champion_ref: CandidateVersionRefV1
    selected_ref: CandidateVersionRefV1
    routing_version: int = Field(ge=1)
    bucket: int = Field(ge=0, le=9_999)
    created_at: datetime

    _validate_ids = field_validator("assignment_id", "memorial_id")(_non_blank)
    _validate_candidate_id = field_validator("candidate_id")(_non_blank)
    _normalize_created_at = field_validator("created_at")(_utc)


class LegacyRunAssignmentV1(_StrictModel):
    """Durable proof that a run began outside governed candidate routing."""

    mode: Literal["legacy_unmanaged"] = "legacy_unmanaged"
    assignment_id: str
    memorial_id: str
    created_at: datetime

    _validate_ids = field_validator("assignment_id", "memorial_id")(_non_blank)
    _normalize_created_at = field_validator("created_at")(_utc)


class EffectiveEvolutionOverlayV1(_StrictModel):
    """Verified logical resource selection consumed by one run."""

    assignment_id: str
    kind: CandidateKind | None
    subject_key: str | None
    artifact_digest: str = Field(pattern=_DIGEST)
    canonical_digest: str = Field(pattern=_DIGEST)

    _validate_assignment_id = field_validator("assignment_id")(_non_blank)
    _validate_subject_key = field_validator("subject_key")(_optional_non_blank)

    @model_validator(mode="after")
    def validate_domain_binding(self) -> Self:
        if (self.kind is None) != (self.subject_key is None):
            raise ValueError("overlay kind and subject_key must be present together")
        return self


class EvolutionRunEvidenceV1(_StrictModel):
    """Canonical assignment and effective overlay attribution for Evidence."""

    assignment: RunAssignmentV1
    overlay: EffectiveEvolutionOverlayV1
    candidate_id: str
    routing_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_attribution(self) -> Self:
        if (
            self.overlay.assignment_id != self.assignment.assignment_id
            or self.overlay.artifact_digest != self.assignment.selected_ref.artifact_digest
            or self.overlay.canonical_digest != self.assignment.selected_ref.canonical_digest
            or self.candidate_id != self.assignment.candidate_id
            or self.routing_version != self.assignment.routing_version
        ):
            raise ValueError("evolution evidence does not match the durable assignment")
        return self


class SubjectRunAssignmentV1(_StrictModel):
    """One immutable routing decision for a governed evolution subject."""

    assignment_id: str
    memorial_id: str
    kind: CandidateKind
    subject_key: str = Field(min_length=1, max_length=512)
    candidate_id: str | None
    champion_ref: CandidateVersionRefV1
    selected_ref: CandidateVersionRefV1
    routing_version: int = Field(ge=1)
    bucket: int = Field(ge=0, le=9_999)
    created_at: datetime

    _validate_ids = field_validator("assignment_id", "memorial_id", "subject_key")(_non_blank)
    _validate_candidate_id = field_validator("candidate_id")(_optional_non_blank)
    _normalize_created_at = field_validator("created_at")(_utc)


class RunAssignmentSetV1(_StrictModel):
    """Canonical per-subject assignments pinned to one Memorial."""

    memorial_id: str
    assignments: tuple[SubjectRunAssignmentV1, ...] = Field(min_length=1, max_length=64)
    set_hash: str = Field(pattern=_DIGEST)

    _validate_memorial_id = field_validator("memorial_id")(_non_blank)

    @model_validator(mode="after")
    def validate_assignment_set(self) -> Self:
        if any(assignment.memorial_id != self.memorial_id for assignment in self.assignments):
            raise ValueError("assignment set entries must belong to the same Memorial")
        keys = tuple(
            (assignment.kind.value, assignment.subject_key) for assignment in self.assignments
        )
        if len(set(keys)) != len(keys):
            raise ValueError("assignment set kind and subject_key pairs must be unique")
        if keys != tuple(sorted(keys)):
            raise ValueError("assignment set entries must be canonically sorted")
        material = self.model_dump(mode="json", exclude={"set_hash"})
        if self.set_hash != canonical_sha256(material):
            raise ValueError("assignment set hash does not match assignments")
        return self


__all__ = [
    "EffectiveEvolutionOverlayV1",
    "EvolutionRunEvidenceV1",
    "LegacyRunAssignmentV1",
    "RunAssignmentSetV1",
    "RunAssignmentV1",
    "SubjectRunAssignmentV1",
]
