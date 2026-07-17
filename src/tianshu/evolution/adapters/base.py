"""Shared contracts for domain-specific evolution candidate adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

from tianshu.evidence.models import ArtifactRefV1
from tianshu.evidence.service import ArtifactStore
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateVersionRefV1,
    EvolutionCandidateV1,
)

if TYPE_CHECKING:
    from tianshu.evolution.candidate_service import CandidateProposalV1, CandidateSourceV1


class AdapterError(ValueError):
    """Base class for fail-closed adapter errors."""


class AdapterKindMismatch(AdapterError):
    """The selected adapter does not own the proposal or candidate kind."""


class AdapterOperationUnavailable(AdapterError):
    """Activation or rollback is not safely wired for this increment."""


class StagedCandidateV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidate: EvolutionCandidateV1
    staged_artifact: ArtifactRefV1

    @property
    def candidate_id(self) -> str:
        return self.candidate.candidate_id

    @property
    def lifecycle(self) -> CandidateLifecycle:
        return self.candidate.lifecycle


class ActivationReceiptV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidate_id: str
    artifact_digest: str


class RollbackReceiptV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    candidate_id: str
    artifact_digest: str


class CandidateAdapter(Protocol):
    kind: CandidateKind

    def validate_source(self, proposal: CandidateProposalV1) -> CandidateVersionRefV1: ...

    def build_diff(self, proposal: CandidateProposalV1) -> ArtifactRefV1: ...

    def stage(self, candidate: EvolutionCandidateV1) -> StagedCandidateV1: ...

    def activate(self, candidate: EvolutionCandidateV1) -> ActivationReceiptV1: ...

    def rollback(self, candidate: EvolutionCandidateV1) -> RollbackReceiptV1: ...


class BaseCandidateAdapter:
    """Materialize immutable artifacts while subclasses validate domain sources."""

    kind: CandidateKind

    def __init__(self, artifacts: ArtifactStore) -> None:
        self._artifacts = artifacts

    def _require_kind(self, actual: CandidateKind) -> None:
        if actual is not self.kind:
            raise AdapterKindMismatch(
                f"adapter kind {self.kind.value} cannot handle {actual.value}"
            )

    def _validate_domain(self, payload: Mapping[str, object]) -> None:
        raise NotImplementedError

    def _materialize(self, payload: Mapping[str, object], *, media_type: str) -> ArtifactRefV1:
        return self._artifacts.put_bytes(
            canonical_json_bytes(payload),
            media_type=media_type,
            redaction="governed_candidate",
        )

    def _materialize_current(
        self, connection: object, payload: Mapping[str, object], *, media_type: str
    ) -> ArtifactRefV1:
        return self._artifacts.put_bytes_current(
            connection,
            canonical_json_bytes(payload),
            media_type=media_type,
            redaction="governed_candidate",
        )

    def _version_ref(self, source: CandidateSourceV1) -> CandidateVersionRefV1:
        payload = source.payload
        self._validate_domain(payload)
        artifact = self._materialize(
            payload,
            media_type=f"application/vnd.tianshu.evolution.{self.kind.value}+json",
        )
        return CandidateVersionRefV1(
            version=source.version,
            artifact_digest=artifact.digest,
            canonical_digest=canonical_sha256(payload),
        )

    def validate_source(self, proposal: CandidateProposalV1) -> CandidateVersionRefV1:
        self._require_kind(proposal.kind)
        return self._version_ref(proposal.candidate)

    def materialize_base(self, proposal: CandidateProposalV1) -> CandidateVersionRefV1:
        self._require_kind(proposal.kind)
        return self._version_ref(proposal.base)

    def build_diff(self, proposal: CandidateProposalV1) -> ArtifactRefV1:
        self._require_kind(proposal.kind)
        base = self.materialize_base(proposal)
        candidate = self.validate_source(proposal)
        payload = {
            "base_canonical_digest": base.canonical_digest,
            "candidate_canonical_digest": candidate.canonical_digest,
            "evolution_contract_hash": canonical_sha256(proposal.evolution_contract),
            "kind": self.kind.value,
            "source_digest": canonical_sha256(proposal.candidate.payload),
            "subject_key": proposal.subject_key,
        }
        return self._materialize(
            payload,
            media_type="application/vnd.tianshu.evolution.diff+json",
        )

    def stage(self, candidate: EvolutionCandidateV1) -> StagedCandidateV1:
        return self._stage(candidate, connection=None)

    def stage_current(
        self, connection: object, candidate: EvolutionCandidateV1
    ) -> StagedCandidateV1:
        return self._stage(candidate, connection=connection)

    def _stage(
        self, candidate: EvolutionCandidateV1, *, connection: object | None
    ) -> StagedCandidateV1:
        self._require_kind(candidate.kind)
        if candidate.lifecycle is not CandidateLifecycle.STAGED:
            raise AdapterError("adapter stage requires a staged candidate envelope")
        payload = {
            "candidate_artifact_digest": candidate.candidate.artifact_digest,
            "candidate_id": candidate.candidate_id,
            "diff_artifact_digest": candidate.diff_artifact_digest,
            "evolution_contract_hash": candidate.evolution_contract_hash,
            "kind": candidate.kind.value,
        }
        media_type = "application/vnd.tianshu.evolution.stage+json"
        manifest = (
            self._materialize(payload, media_type=media_type)
            if connection is None
            else self._materialize_current(connection, payload, media_type=media_type)
        )
        return StagedCandidateV1(candidate=candidate, staged_artifact=manifest)

    def activate(self, candidate: EvolutionCandidateV1) -> ActivationReceiptV1:
        self._require_kind(candidate.kind)
        raise AdapterOperationUnavailable("activation is owned by a later promotion service")

    def rollback(self, candidate: EvolutionCandidateV1) -> RollbackReceiptV1:
        self._require_kind(candidate.kind)
        raise AdapterOperationUnavailable("rollback is owned by a later promotion service")


__all__ = [
    "ActivationReceiptV1",
    "AdapterError",
    "AdapterKindMismatch",
    "AdapterOperationUnavailable",
    "BaseCandidateAdapter",
    "CandidateAdapter",
    "RollbackReceiptV1",
    "StagedCandidateV1",
]
