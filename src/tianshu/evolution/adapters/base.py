"""Shared contracts for domain-specific evolution candidate adapters."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict

from tianshu.evidence.models import ArtifactRefV1
from tianshu.evidence.service import ArtifactStore, ArtifactWriteReceipt
from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
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

    def __init__(self, artifacts: ArtifactStore, *, live_root: Path | None = None) -> None:
        self._artifacts = artifacts
        self._live_root = live_root

    def _require_kind(self, actual: CandidateKind) -> None:
        if actual is not self.kind:
            raise AdapterKindMismatch(
                f"adapter kind {self.kind.value} cannot handle {actual.value}"
            )

    def _normalize_domain(self, payload: Mapping[str, object]) -> dict[str, JsonValue]:
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
        payload = self._normalize_domain(source.payload)
        artifact = self._materialize(
            payload,
            media_type=f"application/vnd.tianshu.evolution.{self.kind.value}+json",
        )
        return CandidateVersionRefV1(
            version=source.version,
            artifact_digest=artifact.digest,
            canonical_digest=canonical_sha256(payload),
        )

    def _version_ref_current(
        self, connection: object, source: CandidateSourceV1
    ) -> tuple[CandidateVersionRefV1, ArtifactWriteReceipt]:
        payload = self._normalize_domain(source.payload)
        write = self._artifacts.put_bytes_tracked_current(
            connection,
            canonical_json_bytes(payload),
            media_type=f"application/vnd.tianshu.evolution.{self.kind.value}+json",
            redaction="governed_candidate",
        )
        return (
            CandidateVersionRefV1(
                version=source.version,
                artifact_digest=write.artifact.digest,
                canonical_digest=canonical_sha256(payload),
            ),
            write,
        )

    def validate_source(self, proposal: CandidateProposalV1) -> CandidateVersionRefV1:
        self._require_kind(proposal.kind)
        return self._version_ref(proposal.candidate)

    def materialize_base(self, proposal: CandidateProposalV1) -> CandidateVersionRefV1:
        self._require_kind(proposal.kind)
        return self._version_ref(proposal.base)

    def materialize_base_current(
        self, connection: object, proposal: CandidateProposalV1
    ) -> tuple[CandidateVersionRefV1, ArtifactWriteReceipt]:
        self._require_kind(proposal.kind)
        return self._version_ref_current(connection, proposal.base)

    def validate_source_current(
        self, connection: object, proposal: CandidateProposalV1
    ) -> tuple[CandidateVersionRefV1, ArtifactWriteReceipt]:
        self._require_kind(proposal.kind)
        return self._version_ref_current(connection, proposal.candidate)

    def build_diff(self, proposal: CandidateProposalV1) -> ArtifactRefV1:
        self._require_kind(proposal.kind)
        base = self.materialize_base(proposal)
        candidate = self.validate_source(proposal)
        payload = self._diff_payload(proposal, base=base, candidate=candidate)
        return self._materialize(
            payload,
            media_type="application/vnd.tianshu.evolution.diff+json",
        )

    def build_diff_current(
        self,
        connection: object,
        proposal: CandidateProposalV1,
        *,
        base: CandidateVersionRefV1,
        candidate: CandidateVersionRefV1,
    ) -> ArtifactWriteReceipt:
        self._require_kind(proposal.kind)
        payload = self._diff_payload(proposal, base=base, candidate=candidate)
        return self._artifacts.put_bytes_tracked_current(
            connection,
            canonical_json_bytes(payload),
            media_type="application/vnd.tianshu.evolution.diff+json",
            redaction="governed_candidate",
        )

    def _diff_payload(
        self,
        proposal: CandidateProposalV1,
        *,
        base: CandidateVersionRefV1,
        candidate: CandidateVersionRefV1,
    ) -> dict[str, str]:
        return {
            "base_canonical_digest": base.canonical_digest,
            "candidate_canonical_digest": candidate.canonical_digest,
            "evolution_contract_hash": canonical_sha256(proposal.evolution_contract),
            "kind": self.kind.value,
            "source_digest": candidate.canonical_digest,
            "subject_key": proposal.subject_key,
        }

    def stage(self, candidate: EvolutionCandidateV1) -> StagedCandidateV1:
        return self._stage(candidate, connection=None)

    def stage_current(
        self, connection: object, candidate: EvolutionCandidateV1
    ) -> StagedCandidateV1:
        return self._stage(candidate, connection=connection)

    def stage_tracked_current(
        self, connection: object, candidate: EvolutionCandidateV1
    ) -> tuple[StagedCandidateV1, ArtifactWriteReceipt]:
        payload = self._stage_payload(candidate)
        write = self._artifacts.put_bytes_tracked_current(
            connection,
            canonical_json_bytes(payload),
            media_type="application/vnd.tianshu.evolution.stage+json",
            redaction="governed_candidate",
        )
        return StagedCandidateV1(candidate=candidate, staged_artifact=write.artifact), write

    def _stage(
        self, candidate: EvolutionCandidateV1, *, connection: object | None
    ) -> StagedCandidateV1:
        self._require_kind(candidate.kind)
        if candidate.lifecycle is not CandidateLifecycle.STAGED:
            raise AdapterError("adapter stage requires a staged candidate envelope")
        payload = self._stage_payload(candidate)
        media_type = "application/vnd.tianshu.evolution.stage+json"
        manifest = (
            self._materialize(payload, media_type=media_type)
            if connection is None
            else self._materialize_current(connection, payload, media_type=media_type)
        )
        return StagedCandidateV1(candidate=candidate, staged_artifact=manifest)

    def _stage_payload(self, candidate: EvolutionCandidateV1) -> dict[str, str]:
        self._require_kind(candidate.kind)
        if candidate.lifecycle is not CandidateLifecycle.STAGED:
            raise AdapterError("adapter stage requires a staged candidate envelope")
        return {
            "candidate_artifact_digest": candidate.candidate.artifact_digest,
            "candidate_id": candidate.candidate_id,
            "diff_artifact_digest": candidate.diff_artifact_digest,
            "evolution_contract_hash": candidate.evolution_contract_hash,
            "kind": candidate.kind.value,
        }

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
