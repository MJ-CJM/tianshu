"""Common proposal identity, provenance, lifecycle, and persistence authority."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from tianshu.evidence.service import ArtifactStore, ArtifactWriteReceipt
from tianshu.evolution.adapters.base import BaseCandidateAdapter, StagedCandidateV1
from tianshu.evolution.adapters.code import CodeCandidateAdapter
from tianshu.evolution.adapters.memory import MemoryCandidateAdapter
from tianshu.evolution.adapters.persona import PersonaCandidateAdapter
from tianshu.evolution.adapters.policy import PolicyCandidateAdapter
from tianshu.evolution.adapters.skill import SkillCandidateAdapter
from tianshu.models.canonical import JsonValue, canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateSourceChannel,
    EvolutionCandidateV1,
    EvolutionContractV1,
    EvolutionProvenanceV1,
    RollbackSpecV1,
)
from tianshu.storage.evolution_repo import EvolutionRepository, EvolutionRepositoryConflict
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class CandidateServiceError(RuntimeError):
    """Base application-service error."""


class CandidateNotFound(CandidateServiceError):
    """No governed candidate exists for the requested identity."""


class CandidateIdentityConflict(CandidateServiceError):
    """A deterministic command identity is already bound differently."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CandidateSourceV1(_StrictModel):
    schema_version: Literal[1] = 1
    version: str
    payload: dict[str, JsonValue]

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source version must not be blank")
        return value


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

    @field_validator("command_id", "subject_key", "restore_point_ref")
    @classmethod
    def validate_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("proposal identity values must not be blank")
        return value

    @field_validator("evidence_bundle_ids")
    @classmethod
    def validate_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError("evidence bundle IDs must be unique and non-blank")
        return values

    @model_validator(mode="after")
    def validate_contract_binding(self) -> CandidateProposalV1:
        if (
            self.kind is not self.evolution_contract.kind
            or self.subject_key != self.evolution_contract.subject_key
        ):
            raise ValueError("proposal kind and subject_key must match evolution contract")
        return self


class _Storage(Protocol):
    def unit_of_work(self) -> SqliteUnitOfWork: ...


@dataclass(frozen=True)
class CandidateLiveAuthorities:
    """Read-only roots that adapters must never mutate during propose or stage."""

    memory_root: Path
    skill_target: Path
    policy_root: Path
    persona_root: Path
    code_worktree: Path

    def for_kind(self, kind: CandidateKind) -> Path:
        return {
            CandidateKind.MEMORY: self.memory_root,
            CandidateKind.SKILL: self.skill_target,
            CandidateKind.POLICY: self.policy_root,
            CandidateKind.PERSONA: self.persona_root,
            CandidateKind.CODE: self.code_worktree,
        }[kind]


_ADAPTER_TYPES: dict[CandidateKind, type[BaseCandidateAdapter]] = {
    CandidateKind.MEMORY: MemoryCandidateAdapter,
    CandidateKind.SKILL: SkillCandidateAdapter,
    CandidateKind.POLICY: PolicyCandidateAdapter,
    CandidateKind.PERSONA: PersonaCandidateAdapter,
    CandidateKind.CODE: CodeCandidateAdapter,
}


class CandidateService:
    """Own the common envelope and durable transitions for all five domains."""

    def __init__(
        self,
        storage: _Storage,
        artifacts: ArtifactStore,
        *,
        live_authorities: CandidateLiveAuthorities,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._artifacts = artifacts
        self._repository = EvolutionRepository()
        self._live_authorities = live_authorities
        self._clock = clock or (lambda: datetime.now(UTC))

    def _adapter(self, kind: CandidateKind) -> BaseCandidateAdapter:
        return _ADAPTER_TYPES[kind](
            self._artifacts,
            live_root=self._live_authorities.for_kind(kind),
        )

    @staticmethod
    def _candidate_id(
        proposal: CandidateProposalV1,
        *,
        base_digest: str,
        candidate_digest: str,
    ) -> str:
        identity = {
            "schema_version": 1,
            "command_id": proposal.command_id,
            "kind": proposal.kind.value,
            "subject_key": proposal.subject_key,
            "base_version": proposal.base.version,
            "base_digest": base_digest,
            "candidate_version": proposal.candidate.version,
            "candidate_digest": candidate_digest,
            "evolution_contract_hash": canonical_sha256(proposal.evolution_contract),
            "provenance": proposal.provenance.model_dump(mode="json"),
            "evidence_bundle_ids": list(proposal.evidence_bundle_ids),
            "restore_point_ref": proposal.restore_point_ref,
        }
        return f"evolution-{canonical_sha256(identity)}"

    def propose(self, proposal: CandidateProposalV1) -> EvolutionCandidateV1:
        adapter = self._adapter(proposal.kind)
        writes: list[ArtifactWriteReceipt] = []
        try:
            with self._storage.unit_of_work() as unit_of_work:
                base, base_write = adapter.materialize_base_current(
                    unit_of_work.connection, proposal
                )
                writes.append(base_write)
                candidate_ref, candidate_write = adapter.validate_source_current(
                    unit_of_work.connection, proposal
                )
                writes.append(candidate_write)
                diff_write = adapter.build_diff_current(
                    unit_of_work.connection,
                    proposal,
                    base=base,
                    candidate=candidate_ref,
                )
                writes.append(diff_write)
                now = self._clock().astimezone(UTC)
                candidate = EvolutionCandidateV1(
                    candidate_id=self._candidate_id(
                        proposal,
                        base_digest=base.canonical_digest,
                        candidate_digest=candidate_ref.canonical_digest,
                    ),
                    kind=proposal.kind,
                    subject_key=proposal.subject_key,
                    provenance=EvolutionProvenanceV1(
                        **proposal.provenance.model_dump(),
                        source_digest=candidate_ref.canonical_digest,
                        received_at=now,
                    ),
                    base=base,
                    candidate=candidate_ref,
                    diff_artifact_digest=diff_write.artifact.digest,
                    evolution_contract=proposal.evolution_contract,
                    evolution_contract_hash=canonical_sha256(proposal.evolution_contract),
                    gate_snapshot_version=0,
                    evidence_bundle_ids=proposal.evidence_bundle_ids,
                    routing=None,
                    rollback=RollbackSpecV1(
                        champion_ref=base,
                        restore_point_ref=proposal.restore_point_ref,
                        adapter_name=proposal.kind.value,
                        max_seconds=proposal.evolution_contract.rollback_slo_seconds,
                    ),
                    lifecycle=CandidateLifecycle.PROPOSED,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                existing = self._repository.get_candidate(
                    unit_of_work.connection, candidate.candidate_id
                )
                if existing is not None:
                    unit_of_work.commit()
                    return existing
                try:
                    durable = self._repository.insert_candidate(unit_of_work.connection, candidate)
                except EvolutionRepositoryConflict as exc:
                    raise CandidateIdentityConflict("candidate command identity conflict") from exc
                unit_of_work.commit()
                return durable
        except BaseException:
            self._artifacts.discard_uncommitted(writes)
            raise

    def stage(self, candidate_id: str) -> StagedCandidateV1:
        writes: list[ArtifactWriteReceipt] = []
        try:
            with self._storage.unit_of_work() as unit_of_work:
                current = self._repository.get_candidate(unit_of_work.connection, candidate_id)
                if current is None:
                    raise CandidateNotFound(f"evolution candidate {candidate_id!r} was not found")
                adapter = self._adapter(current.kind)
                if current.lifecycle is CandidateLifecycle.STAGED:
                    staged, write = adapter.stage_tracked_current(unit_of_work.connection, current)
                    writes.append(write)
                    unit_of_work.commit()
                    return staged
                if current.lifecycle is not CandidateLifecycle.PROPOSED:
                    raise CandidateServiceError("only proposed candidates can be staged")
                staged_envelope = current.model_copy(
                    update={"lifecycle": CandidateLifecycle.STAGED, "updated_at": self._clock()}
                )
                staged, write = adapter.stage_tracked_current(
                    unit_of_work.connection, staged_envelope
                )
                writes.append(write)
                durable = self._repository.save_candidate(
                    unit_of_work.connection,
                    staged_envelope,
                    expected_version=current.version,
                )
                unit_of_work.commit()
                return staged.model_copy(update={"candidate": durable})
        except BaseException:
            self._artifacts.discard_uncommitted(writes)
            raise


__all__ = [
    "CandidateIdentityConflict",
    "CandidateLiveAuthorities",
    "CandidateNotFound",
    "CandidateProposalV1",
    "CandidateService",
    "CandidateServiceError",
    "CandidateSourceV1",
    "ProvenanceInputV1",
]
