"""Common proposal identity, provenance, lifecycle, and persistence authority."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from tianshu.evidence.service import ArtifactStore, ArtifactWriteReceipt
from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter, StagedCandidateV1
from tianshu.evolution.adapters.code import CodeCandidateAdapter
from tianshu.evolution.adapters.executor import ExecutorCandidateAdapter
from tianshu.evolution.adapters.memory import MemoryCandidateAdapter
from tianshu.evolution.adapters.persona import PersonaCandidateAdapter
from tianshu.evolution.adapters.policy import PolicyCandidateAdapter
from tianshu.evolution.adapters.skill import SkillCandidateAdapter
from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateProposalV1,
    CandidateSourceV1,
    CandidateVersionRefV1,
    EvolutionCandidateV1,
    EvolutionProvenanceV1,
    ProvenanceInputV1,
    RollbackSpecV1,
)
from tianshu.models.run_assignment import EffectiveEvolutionOverlayV1
from tianshu.storage.evolution_policy_repo import (
    EvolutionPolicyRepository,
    default_mode_for,
)
from tianshu.storage.evolution_repo import EvolutionRepository, EvolutionRepositoryConflict
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class CandidateServiceError(RuntimeError):
    """Base application-service error."""


class CandidateNotFound(CandidateServiceError):
    """No governed candidate exists for the requested identity."""


class CandidateIdentityConflict(CandidateServiceError):
    """A deterministic command identity is already bound differently."""


class CandidateOverlayUnavailable(CandidateServiceError):
    """The selected immutable artifact cannot be safely consumed."""


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
    executor_root: Path

    def for_kind(self, kind: CandidateKind) -> Path:
        return {
            CandidateKind.MEMORY: self.memory_root,
            CandidateKind.SKILL: self.skill_target,
            CandidateKind.POLICY: self.policy_root,
            CandidateKind.PERSONA: self.persona_root,
            CandidateKind.CODE: self.code_worktree,
            CandidateKind.EXECUTOR: self.executor_root,
        }[kind]


_ADAPTER_TYPES: dict[CandidateKind, type[BaseCandidateAdapter]] = {
    CandidateKind.MEMORY: MemoryCandidateAdapter,
    CandidateKind.SKILL: SkillCandidateAdapter,
    CandidateKind.POLICY: PolicyCandidateAdapter,
    CandidateKind.PERSONA: PersonaCandidateAdapter,
    CandidateKind.CODE: CodeCandidateAdapter,
    CandidateKind.EXECUTOR: ExecutorCandidateAdapter,
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
        self._policy_repository = EvolutionPolicyRepository()
        self._live_authorities = live_authorities
        self._clock = clock or (lambda: datetime.now(UTC))

    def _adapter(self, kind: CandidateKind) -> BaseCandidateAdapter:
        return _ADAPTER_TYPES[kind](
            self._artifacts,
            live_root=self._live_authorities.for_kind(kind),
        )

    def resolve_effective_payload_current(
        self,
        connection: sqlite3.Connection,
        selected_ref: CandidateVersionRefV1,
        overlay: EffectiveEvolutionOverlayV1,
    ) -> dict[str, JsonValue]:
        """Verify metadata and bytes, then normalize once at the owning adapter boundary."""

        if overlay.kind is None or overlay.subject_key is None:
            raise CandidateOverlayUnavailable("candidate_overlay_unavailable")
        if (
            overlay.artifact_digest != selected_ref.artifact_digest
            or overlay.canonical_digest != selected_ref.canonical_digest
        ):
            raise CandidateOverlayUnavailable("candidate_overlay_unavailable")
        try:
            artifact, raw = self._artifacts.get_verified_current(
                connection, selected_ref.artifact_digest
            )
            expected_media_type = f"application/vnd.tianshu.evolution.{overlay.kind.value}+json"
            if (
                artifact.media_type != expected_media_type
                or artifact.redaction != "governed_candidate"
                or artifact.digest != selected_ref.artifact_digest
                or hashlib.sha256(raw).hexdigest() != selected_ref.artifact_digest
            ):
                raise CandidateOverlayUnavailable("candidate_overlay_unavailable")
            decoded = json.loads(raw)
            if not isinstance(decoded, dict) or canonical_json_bytes(decoded) != raw:
                raise CandidateOverlayUnavailable("candidate_overlay_unavailable")
            normalized = self._adapter(overlay.kind).resolve_effective_payload(
                decoded,
                overlay=overlay,
            )
            if (
                canonical_json_bytes(normalized) != raw
                or canonical_sha256(normalized) != selected_ref.canonical_digest
            ):
                raise CandidateOverlayUnavailable("candidate_overlay_unavailable")
            return normalized
        except CandidateOverlayUnavailable:
            raise
        except (AdapterError, LookupError, RuntimeError, TypeError, ValueError) as exc:
            raise CandidateOverlayUnavailable("candidate_overlay_unavailable") from exc

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

    def propose(
        self,
        proposal: CandidateProposalV1,
        *,
        on_persist: Callable[[sqlite3.Connection, EvolutionCandidateV1], None] | None = None,
    ) -> EvolutionCandidateV1:
        adapter = self._adapter(proposal.kind)
        writes: list[ArtifactWriteReceipt] = []
        try:
            with self._storage.unit_of_work() as unit_of_work:
                policy = self._policy_repository.get_policy(
                    unit_of_work.connection, proposal.subject_key
                )
                if policy is not None and policy.kind is not proposal.kind:
                    raise CandidateServiceError("evolution_policy_kind_conflict")
                mode = policy.mode if policy is not None else default_mode_for(proposal.kind)
                if mode == "frozen":
                    raise CandidateServiceError("subject_frozen")
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
                if on_persist is not None:
                    on_persist(unit_of_work.connection, durable)
                unit_of_work.commit()
                return durable
        except BaseException:
            self._artifacts.discard_uncommitted(writes)
            raise

    def stage(
        self,
        candidate_id: str,
        *,
        on_persist: Callable[[sqlite3.Connection, EvolutionCandidateV1], None] | None = None,
    ) -> StagedCandidateV1:
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
                if on_persist is not None:
                    on_persist(unit_of_work.connection, durable)
                unit_of_work.commit()
                return staged.model_copy(update={"candidate": durable})
        except BaseException:
            self._artifacts.discard_uncommitted(writes)
            raise


__all__ = [
    "CandidateIdentityConflict",
    "CandidateLiveAuthorities",
    "CandidateNotFound",
    "CandidateOverlayUnavailable",
    "CandidateProposalV1",
    "CandidateService",
    "CandidateServiceError",
    "CandidateSourceV1",
    "ProvenanceInputV1",
]
