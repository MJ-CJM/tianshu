"""Low-frequency, side-effect-free-on-read executor drift proposal authority."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable

from tianshu.evolution.candidate_service import CandidateService, CandidateServiceError
from tianshu.executor.keqing.generation import PI_GENERATION_SCOPE, PiReleaseMaterializer
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateProposalV1,
    CandidateSourceChannel,
    CandidateSourceV1,
    EvolutionContractV1,
    GateName,
    ProvenanceInputV1,
)
from tianshu.models.runtime_generation import RuntimeGenerationState, RuntimeReleaseV1
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork

type UnitOfWorkFactory = Callable[[], SqliteUnitOfWork]

_PRODUCER_NAME = "tianshu.executor-drift-scanner"
_PRODUCER_VERSION = "1"
_CONTRACT_DIGEST = hashlib.sha256(b"tianshu.executor-gates.v1").hexdigest()


class ExecutorDriftScanner:
    """Compare the trusted active release with current managed Pi material.

    The scanner is deliberately detached from every GET endpoint.  It runs only
    when its caller invokes ``scan_once`` and the deny-by-default switch is on.
    """

    def __init__(
        self,
        *,
        unit_of_work_factory: UnitOfWorkFactory,
        candidate_service: CandidateService,
        materializer: PiReleaseMaterializer,
        enabled: bool = False,
        interval_seconds: float = 3600.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("executor drift scan interval must be positive")
        self._unit_of_work_factory = unit_of_work_factory
        self._candidate_service = candidate_service
        self._materializer = materializer
        self._enabled = enabled
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._repository = GenerationRepository()
        self._evolution_repository = EvolutionRepository()
        self._lock = threading.Lock()
        self._last_scan_at: float | None = None
        self._last_error_code: str | None = None
        self._last_candidate_id: str | None = None

    @property
    def last_error_code(self) -> str | None:
        return self._last_error_code

    @property
    def last_candidate_id(self) -> str | None:
        return self._last_candidate_id

    def scan_once(self) -> int:
        """Propose at most one deterministic candidate for one observed drift."""

        if not self._enabled:
            return 0
        with self._lock:
            now = self._monotonic()
            if self._last_scan_at is not None and now - self._last_scan_at < self._interval_seconds:
                return 0
            self._last_scan_at = now
            try:
                base = self._active_release()
                if base is None:
                    self._last_error_code = "executor_generation_baseline_unestablished"
                    return 0
                self._materializer.verify_release(base)
                observed = self._materializer.create_release()
            except Exception:  # noqa: BLE001 - stable diagnostics must not expose local paths
                self._last_error_code = "executor_drift_scan_unavailable"
                return 0
            if observed.release_digest == base.release_digest:
                self._last_error_code = None
                return 0
            base_digest = canonical_sha256(base.model_dump(mode="json"))
            candidate_digest = canonical_sha256(observed.model_dump(mode="json"))
            if self._candidate_exists(
                base_digest=base_digest,
                candidate_digest=candidate_digest,
            ):
                self._last_error_code = None
                return 0
            try:
                candidate = self._candidate_service.propose(
                    self._proposal(base=base, candidate=observed)
                )
            except CandidateServiceError as exc:
                self._last_error_code = (
                    "executor_drift_subject_frozen"
                    if str(exc) == "subject_frozen"
                    else "executor_drift_proposal_failed"
                )
                return 0
            self._last_candidate_id = candidate.candidate_id
            self._last_error_code = None
            return 1

    def _active_release(self) -> RuntimeReleaseV1 | None:
        with self._unit_of_work_factory() as unit_of_work:
            pointer = self._repository.get_pointer(
                unit_of_work.connection,
                scope=PI_GENERATION_SCOPE,
            )
            if pointer is None:
                unit_of_work.commit()
                return None
            generation = self._repository.get_generation(
                unit_of_work.connection,
                scope=PI_GENERATION_SCOPE,
                generation_id=pointer.active_generation_id,
            )
            if generation is None or generation.state is not RuntimeGenerationState.ACTIVE:
                raise RuntimeError("executor generation pointer is invalid")
            release = self._repository.get_release(
                unit_of_work.connection,
                scope=PI_GENERATION_SCOPE,
                release_digest=generation.release_digest,
            )
            if release is None:
                raise RuntimeError("executor generation release is missing")
            unit_of_work.commit()
            return release

    def _candidate_exists(
        self,
        *,
        base_digest: str,
        candidate_digest: str,
    ) -> bool:
        with self._unit_of_work_factory() as unit_of_work:
            rows = unit_of_work.connection.execute(
                """SELECT candidate_id FROM evolution_candidates
                   WHERE kind=? AND subject_key=?
                   ORDER BY created_at, candidate_id""",
                (CandidateKind.EXECUTOR.value, PI_GENERATION_SCOPE),
            ).fetchall()
            exists = any(
                (
                    candidate := self._evolution_repository.get_candidate(
                        unit_of_work.connection,
                        str(row["candidate_id"]),
                    )
                )
                is not None
                and candidate.base.canonical_digest == base_digest
                and candidate.candidate.canonical_digest == candidate_digest
                for row in rows
            )
            unit_of_work.commit()
            return exists

    @staticmethod
    def _proposal(
        *,
        base: RuntimeReleaseV1,
        candidate: RuntimeReleaseV1,
    ) -> CandidateProposalV1:
        command_digest = canonical_sha256(
            {
                "base_release_digest": base.release_digest,
                "candidate_release_digest": candidate.release_digest,
                "producer": _PRODUCER_NAME,
                "schema_version": 1,
            }
        )
        contract = EvolutionContractV1(
            kind=CandidateKind.EXECUTOR,
            subject_key=PI_GENERATION_SCOPE,
            governance_contract_hash=_CONTRACT_DIGEST,
            required_gates=tuple(GateName),
            regression_policy_artifact_digest=_CONTRACT_DIGEST,
            sample_policy_artifact_digest=_CONTRACT_DIGEST,
            budget_policy_artifact_digest=_CONTRACT_DIGEST,
            minimum_canary_samples=10,
            max_canary_allocation_basis_points=500,
            rollback_slo_seconds=30,
        )
        return CandidateProposalV1(
            command_id=f"executor-drift:{command_digest}",
            kind=CandidateKind.EXECUTOR,
            subject_key=PI_GENERATION_SCOPE,
            base=CandidateSourceV1(
                version=ExecutorDriftScanner._release_version(base),
                payload=base.model_dump(mode="json"),
            ),
            candidate=CandidateSourceV1(
                version=ExecutorDriftScanner._release_version(candidate),
                payload=candidate.model_dump(mode="json"),
            ),
            evolution_contract=contract,
            provenance=ProvenanceInputV1(
                source_channel=CandidateSourceChannel.SYSTEM,
                source_uri_redacted="local://executor/keqing/pi",
                actor_principal_id="system:executor-drift-scanner",
                actor_display_name="Executor drift scanner",
                originating_edict_id=None,
                originating_memorial_id=None,
                producer_name=_PRODUCER_NAME,
                producer_version=_PRODUCER_VERSION,
            ),
            evidence_bundle_ids=(),
            restore_point_ref=f"runtime-release:{base.release_digest}",
        )

    @staticmethod
    def _release_version(release: RuntimeReleaseV1) -> str:
        return f"{release.cli_version}+{release.release_digest[:12]}"


__all__ = ["ExecutorDriftScanner"]
