"""Governed executor promotion backed by exact runtime-generation authority."""

from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import ValidationError

from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.base import (
    ActivationReceiptV1,
    AdapterError,
    AdapterOperationUnavailable,
    CanaryPreparationReceiptV1,
    RollbackReceiptV1,
)
from tianshu.evolution.executor_ports import GenerationControlPort
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import (
    CandidateKind,
    CandidateLifecycle,
    CandidateVersionRefV1,
    EvolutionCandidateV1,
)
from tianshu.models.executor_generation_authority import (
    ExecutorGenerationAuthorityStatus,
    ExecutorGenerationAuthorityV1,
    new_pending_executor_generation_authority,
    transition_executor_generation_authority,
)
from tianshu.models.runtime_generation import (
    GenerationPointerV1,
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.storage.executor_generation_authority_repo import (
    ExecutorGenerationAuthorityRepository,
)
from tianshu.storage.generation_repo import GenerationRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GENERATION_ID = re.compile(r"^rg-[0-9a-f]{32}$")
_EXECUTOR_MEDIA_TYPE = "application/vnd.tianshu.evolution.executor+json"
type UnitOfWorkFactory = Callable[[], SqliteUnitOfWork]
type _RollbackMode = Literal[
    "canary_apply",
    "canary_complete",
    "promoted_apply",
    "promoted_finalize",
    "promoted_complete",
]


class ExecutorPromotionAdapter:
    """Prepare, activate, and roll back one exact executor generation."""

    rollback_is_idempotent = True

    def __init__(
        self,
        artifacts: ArtifactStore,
        generation_controller: GenerationControlPort,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        evolution_enabled: bool = True,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._artifacts = artifacts
        self._generation_controller = generation_controller
        self._unit_of_work_factory = unit_of_work_factory
        self._authority_repository = ExecutorGenerationAuthorityRepository()
        self._generation_repository = GenerationRepository()
        self._evolution_enabled = evolution_enabled
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def new_evolution_enabled(self) -> bool:
        return self._evolution_enabled

    async def prepare_canary(
        self,
        candidate: EvolutionCandidateV1,
        *,
        command_key: str,
        generation_id: str,
        promotion_journal_id: str,
    ) -> CanaryPreparationReceiptV1:
        """Deterministically stage, warm, and authorize one challenger generation."""

        authority: ExecutorGenerationAuthorityV1 | None = None
        try:
            self._require_candidate(candidate, lifecycle=CandidateLifecycle.READY)
            self._require_command_identity(
                command_key=command_key,
                generation_id=generation_id,
                promotion_journal_id=promotion_journal_id,
            )
            if not self._evolution_enabled:
                raise AdapterOperationUnavailable("executor generation evolution is disabled")
            release, base_release, base_generation_id = self._load_preparation_inputs(candidate)

            def bind_pending_authority(
                connection: sqlite3.Connection,
                staged_generation: RuntimeGenerationV1,
            ) -> None:
                nonlocal authority
                if (
                    staged_generation.scope != candidate.subject_key
                    or staged_generation.release_digest != release.release_digest
                    or staged_generation.generation_id != generation_id
                ):
                    raise AdapterError("executor staged generation identity mismatch")
                authority = self._ensure_pending_authority_current(
                    connection,
                    candidate,
                    release=release,
                    base_release=base_release,
                    base_generation_id=base_generation_id,
                    generation_id=generation_id,
                    command_key=command_key,
                    promotion_journal_id=promotion_journal_id,
                )

            staged = self._generation_controller.stage_exact(
                release,
                generation_id=generation_id,
                stage_commit_hook=bind_pending_authority,
            )
            if (
                staged.scope != candidate.subject_key
                or staged.release_digest != release.release_digest
                or staged.generation_id != generation_id
                or staged.state
                not in {
                    RuntimeGenerationState.STAGED,
                    RuntimeGenerationState.WARMING,
                    RuntimeGenerationState.READY,
                }
            ):
                raise AdapterError("executor staged generation identity mismatch")
            if authority is None:
                raise AdapterError("executor generation authority is missing")
            ready = await self._generation_controller.warm_or_resume(generation_id)
            if (
                ready.state is not RuntimeGenerationState.READY
                or ready.scope != authority.scope
                or ready.generation_id != authority.generation_id
                or ready.release_digest != authority.release_digest
            ):
                raise AdapterError("executor warm generation identity mismatch")
            authorized = self._ensure_authorized(authority)
            return self._preparation_receipt(authorized)
        except asyncio.CancelledError:
            if authority is not None:
                self._revoke_terminal_preparation(authority)
            raise
        except AdapterError:
            raise
        except Exception:
            if authority is not None:
                self._revoke_terminal_preparation(authority)
            raise AdapterError("executor canary preparation failed") from None

    def validate_canary_preparation_current(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        receipt: CanaryPreparationReceiptV1,
    ) -> None:
        """Validate the exact READY authority before routing becomes visible."""

        try:
            if not self._evolution_enabled:
                raise AdapterOperationUnavailable("executor generation evolution is disabled")
            if not connection.in_transaction:
                raise RuntimeError("executor canary validation requires a caller-owned transaction")
            self._require_candidate(candidate, lifecycle=CandidateLifecycle.READY)
            self._require_durable_candidate_core(connection, candidate)
            authority = self._authority_repository.get_current(
                connection,
                candidate_id=candidate.candidate_id,
            )
            if authority is None:
                raise AdapterError("executor generation authority is missing")
            self._require_start_authority(authority, candidate)
            if (
                authority.status is not ExecutorGenerationAuthorityStatus.AUTHORIZED
                or receipt != self._preparation_receipt(authority)
            ):
                raise AdapterError("executor generation authority is not current")
            target = self._generation_repository.get_generation(
                connection,
                scope=authority.scope,
                generation_id=authority.generation_id,
            )
            pointer = self._generation_repository.get_pointer(
                connection,
                scope=authority.scope,
            )
            base = self._generation_repository.get_generation(
                connection,
                scope=authority.scope,
                generation_id=authority.base_generation_id,
            )
            promotion = connection.execute(
                """SELECT command_key, candidate_id, action, status
                   FROM evolution_promotion_journal
                   WHERE promotion_journal_id=?""",
                (authority.promotion_journal_id,),
            ).fetchone()
            if (
                target is None
                or target.state is not RuntimeGenerationState.READY
                or target.release_digest != authority.release_digest
                or pointer is None
                or pointer.active_generation_id != authority.base_generation_id
                or base is None
                or base.state is not RuntimeGenerationState.ACTIVE
                or base.release_digest != authority.base_release_digest
                or promotion is None
                or promotion["command_key"] != authority.start_command_key
                or promotion["candidate_id"] != authority.candidate_id
                or promotion["action"] != "start_canary"
                or promotion["status"] != "intended"
            ):
                raise AdapterError("executor canary preparation is no longer current")
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("executor canary preparation validation failed") from None

    def abort_canary_preparation(
        self,
        candidate: EvolutionCandidateV1,
        *,
        command_key: str,
        generation_id: str,
        promotion_journal_id: str,
    ) -> None:
        """Compensate a prepared effect that cannot become visible as CANARY."""

        try:
            self._require_candidate(candidate, lifecycle=CandidateLifecycle.READY)
            self._require_command_identity(
                command_key=command_key,
                generation_id=generation_id,
                promotion_journal_id=promotion_journal_id,
            )
            with self._unit_of_work_factory() as unit_of_work:
                authority = self._authority_repository.get_current(
                    unit_of_work.connection,
                    candidate_id=candidate.candidate_id,
                )
                generation = self._generation_repository.get_generation(
                    unit_of_work.connection,
                    scope=candidate.subject_key,
                    generation_id=generation_id,
                )
                if authority is None:
                    if generation is not None:
                        raise AdapterError(
                            "executor preparation generation exists without authority"
                        )
                    unit_of_work.commit()
                    return
                self._require_durable_candidate_core(unit_of_work.connection, candidate)
                try:
                    self._require_start_authority(
                        authority,
                        candidate,
                        generation_id=generation_id,
                        command_key=command_key,
                        promotion_journal_id=promotion_journal_id,
                        require_candidate_version=True,
                    )
                except AdapterError:
                    if generation is None:
                        unit_of_work.commit()
                        return
                    raise
                if generation is None:
                    raise AdapterError("executor preparation generation is missing")
                unit_of_work.commit()

            def revoke_failed_authority(
                connection: sqlite3.Connection,
                failed: RuntimeGenerationV1,
            ) -> None:
                current = self._authority_repository.get_current(
                    connection,
                    candidate_id=authority.candidate_id,
                )
                if (
                    current is None
                    or self._authority_epoch_identity(current)
                    != self._authority_epoch_identity(authority)
                    or failed.generation_id != authority.generation_id
                    or failed.release_digest != authority.release_digest
                ):
                    raise AdapterError("executor preparation authority changed during abort")
                if current.status in {
                    ExecutorGenerationAuthorityStatus.PENDING,
                    ExecutorGenerationAuthorityStatus.AUTHORIZED,
                }:
                    revoked = transition_executor_generation_authority(
                        current,
                        ExecutorGenerationAuthorityStatus.REVOKED,
                        now=self._now(),
                        revocation_reason="executor_canary_finalization_failed",
                    )
                    self._authority_repository.save(
                        connection,
                        revoked,
                        expected_version=current.version,
                        reason_code="executor_canary_finalization_failed",
                    )
                elif current.status is not ExecutorGenerationAuthorityStatus.REVOKED:
                    raise AdapterError("executor preparation authority cannot be aborted")

            self._generation_controller.fail_pre_active_exact(
                authority.scope,
                generation_id=authority.generation_id,
                expected_release_digest=authority.release_digest,
                failure_commit_hook=revoke_failed_authority,
            )
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("executor canary preparation abort failed") from None

    def activate(self, candidate: EvolutionCandidateV1) -> ActivationReceiptV1:
        """Activate the authorized READY generation, accepting only an exact replay."""

        try:
            self._require_candidate(candidate, lifecycle=CandidateLifecycle.CANARY)
            with self._unit_of_work_factory() as unit_of_work:
                authority = self._require_current_authority(unit_of_work.connection, candidate)
                if (
                    authority.status is not ExecutorGenerationAuthorityStatus.AUTHORIZED
                    or candidate.version != authority.candidate_version + 1
                ):
                    raise AdapterError("executor activation authority is not current")
                target = self._generation_repository.get_generation(
                    unit_of_work.connection,
                    scope=authority.scope,
                    generation_id=authority.generation_id,
                )
                pointer = self._generation_repository.get_pointer(
                    unit_of_work.connection,
                    scope=authority.scope,
                )
                if target is None or pointer is None:
                    raise AdapterError("executor activation state is missing")
                fresh = (
                    target.state is RuntimeGenerationState.READY
                    and pointer.active_generation_id == authority.base_generation_id
                )
                replay = (
                    target.state is RuntimeGenerationState.ACTIVE
                    and pointer.active_generation_id == authority.generation_id
                    and pointer.last_good_generation_id == authority.base_generation_id
                )
                if not (fresh or replay):
                    raise AdapterError("executor activation state conflicts with authority")
                if fresh and not self._evolution_enabled:
                    raise AdapterOperationUnavailable("executor generation evolution is disabled")
                unit_of_work.commit()

            def require_activation_authority_current(
                connection: sqlite3.Connection,
                target: RuntimeGenerationV1,
                pointer: GenerationPointerV1 | None,
            ) -> None:
                current_authority = self._authority_repository.get_current(
                    connection,
                    candidate_id=candidate.candidate_id,
                )
                durable_candidate = EvolutionRepository().get_candidate(
                    connection,
                    candidate.candidate_id,
                )
                if (
                    current_authority != authority
                    or current_authority.status is not ExecutorGenerationAuthorityStatus.AUTHORIZED
                    or durable_candidate != candidate
                    or target.generation_id != authority.generation_id
                    or target.release_digest != authority.release_digest
                    or pointer is None
                    or not (
                        (
                            target.state is RuntimeGenerationState.READY
                            and pointer.active_generation_id == authority.base_generation_id
                        )
                        or (
                            target.state is RuntimeGenerationState.ACTIVE
                            and pointer.active_generation_id == authority.generation_id
                            and pointer.last_good_generation_id == authority.base_generation_id
                        )
                    )
                ):
                    raise AdapterError("executor activation authority changed before commit")
                previous_authority = self._authority_repository.get_by_generation(
                    connection,
                    generation_id=authority.base_generation_id,
                )
                if (
                    previous_authority is not None
                    and previous_authority.candidate_id != authority.candidate_id
                ):
                    previous_candidate = EvolutionRepository().get_candidate(
                        connection,
                        previous_authority.candidate_id,
                    )
                    if (
                        previous_candidate is None
                        or previous_candidate.lifecycle is not CandidateLifecycle.PROMOTED
                        or previous_candidate.version != previous_authority.candidate_version + 2
                        or previous_candidate.candidate.artifact_digest
                        != previous_authority.candidate_artifact_digest
                        or previous_candidate.candidate.canonical_digest
                        != previous_authority.candidate_canonical_digest
                    ):
                        raise AdapterError(
                            "executor base generation candidate cannot be superseded"
                        )
                    if previous_authority.status is ExecutorGenerationAuthorityStatus.AUTHORIZED:
                        retired = transition_executor_generation_authority(
                            previous_authority,
                            ExecutorGenerationAuthorityStatus.REVOKED,
                            now=self._now(),
                            revocation_reason="executor_generation_superseded",
                        )
                        self._authority_repository.save(
                            connection,
                            retired,
                            expected_version=previous_authority.version,
                            reason_code="executor_generation_superseded",
                        )
                    elif previous_authority.status is not ExecutorGenerationAuthorityStatus.REVOKED:
                        raise AdapterError(
                            "executor base generation authority cannot be superseded"
                        )

            result = self._generation_controller.activate_exact(
                authority.generation_id,
                expected_active_generation_id=authority.base_generation_id,
                expected_active_release_digest=authority.base_release_digest,
                activation_commit_hook=require_activation_authority_current,
            )
            if (
                result.activated.generation_id != authority.generation_id
                or result.activated.release_digest != authority.release_digest
                or result.pointer.active_generation_id != authority.generation_id
                or result.pointer.last_good_generation_id != authority.base_generation_id
                or result.draining is None
                or result.draining.generation_id != authority.base_generation_id
                or result.draining.release_digest != authority.base_release_digest
            ):
                raise AdapterError("executor activation result conflicts with authority")
            return ActivationReceiptV1(
                candidate_id=candidate.candidate_id,
                artifact_digest=candidate.candidate.artifact_digest,
                generation_id=authority.generation_id,
                release_digest=authority.release_digest,
            )
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("executor activation failed") from None

    def rollback(self, candidate: EvolutionCandidateV1) -> RollbackReceiptV1:
        """Withdraw a canary or restore the exact last-good promoted generation."""

        try:
            self._require_candidate(candidate, lifecycle=CandidateLifecycle.ROLLBACK_PENDING)
            mode, authority = self._rollback_mode(candidate)
            if mode == "canary_apply":
                self._mark_canary_revoking(authority)
            elif mode in {"promoted_apply", "promoted_finalize"}:
                result = self._generation_controller.rollback_exact(
                    authority.scope,
                    expected_active_generation_id=authority.generation_id,
                    expected_last_good_generation_id=authority.base_generation_id,
                )
                if (
                    result.pointer.active_generation_id != authority.base_generation_id
                    or result.pointer.last_good_generation_id != authority.base_generation_id
                    or result.activated.generation_id != authority.base_generation_id
                    or result.activated.release_digest != authority.base_release_digest
                    or result.draining.generation_id != authority.generation_id
                    or result.draining.release_digest != authority.release_digest
                ):
                    raise AdapterError("executor rollback result conflicts with authority")
                self._mark_promoted_revoked(authority)
            elif mode not in {"canary_complete", "promoted_complete"}:
                raise AdapterError("executor rollback mode is invalid")
            verified = self.verify_rollback(candidate)
            if verified is None:
                raise AdapterError("executor rollback is not durably complete")
            return verified
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("executor rollback failed") from None

    def verify_rollback(self, candidate: EvolutionCandidateV1) -> RollbackReceiptV1 | None:
        """Return a receipt only when withdrawal or pointer restoration is durable."""

        try:
            self._require_candidate(candidate, lifecycle=CandidateLifecycle.ROLLBACK_PENDING)
            mode, _authority = self._rollback_mode(candidate)
            if mode not in {"canary_complete", "promoted_complete"}:
                return None
            return RollbackReceiptV1(
                candidate_id=candidate.candidate_id,
                artifact_digest=candidate.base.artifact_digest,
            )
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("executor rollback verification failed") from None

    def validate_rollback_current(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
    ) -> None:
        """Fail closed before PromotionService persists rollback intent."""

        try:
            if not connection.in_transaction:
                raise RuntimeError("executor rollback validation requires a transaction")
            if candidate.lifecycle not in {
                CandidateLifecycle.CANARY,
                CandidateLifecycle.PROMOTED,
            }:
                raise AdapterError("executor rollback candidate is not live")
            self._rollback_mode_current(
                connection,
                candidate,
                rollback_candidate_version=candidate.version + 1,
            )
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("executor rollback validation failed") from None

    def _load_preparation_inputs(
        self,
        candidate: EvolutionCandidateV1,
    ) -> tuple[RuntimeReleaseV1, RuntimeReleaseV1, str]:
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            self._require_durable_candidate_core(connection, candidate)
            release = self._load_release_current(connection, candidate.candidate)
            base_release = self._load_release_current(connection, candidate.base)
            pointer = self._generation_repository.get_pointer(
                connection,
                scope=candidate.subject_key,
            )
            if pointer is None:
                raise AdapterError("executor base generation pointer is unavailable")
            base = self._generation_repository.get_generation(
                connection,
                scope=candidate.subject_key,
                generation_id=pointer.active_generation_id,
            )
            if (
                release.scope != candidate.subject_key
                or base_release.scope != candidate.subject_key
                or base is None
                or base.state is not RuntimeGenerationState.ACTIVE
                or base.release_digest != base_release.release_digest
            ):
                raise AdapterError("executor candidate does not match the active base")
            unit_of_work.commit()
            return release, base_release, base.generation_id

    def _load_release_current(
        self,
        connection: sqlite3.Connection,
        reference: CandidateVersionRefV1,
    ) -> RuntimeReleaseV1:
        artifact, raw = self._artifacts.get_verified_current(
            connection,
            reference.artifact_digest,
        )
        if (
            artifact.media_type != _EXECUTOR_MEDIA_TYPE
            or artifact.redaction != "governed_candidate"
            or artifact.digest != reference.artifact_digest
            or hashlib.sha256(raw).hexdigest() != reference.artifact_digest
        ):
            raise AdapterError("executor release artifact metadata mismatch")
        try:
            release = RuntimeReleaseV1.model_validate_json(raw)
        except (ValidationError, TypeError, ValueError) as exc:
            raise AdapterError("executor release artifact is invalid") from exc
        if (
            canonical_json_bytes(release) != raw
            or canonical_sha256(release) != reference.canonical_digest
            or release.manifest.get("adapter_id") != "keqing:pi"
        ):
            raise AdapterError("executor release artifact identity mismatch")
        return release

    def _ensure_pending_authority_current(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        *,
        release: RuntimeReleaseV1,
        base_release: RuntimeReleaseV1,
        base_generation_id: str,
        generation_id: str,
        command_key: str,
        promotion_journal_id: str,
    ) -> ExecutorGenerationAuthorityV1:
        if not connection.in_transaction:
            raise RuntimeError("executor pending authority requires a caller-owned transaction")
        current = self._authority_repository.get_current(
            connection,
            candidate_id=candidate.candidate_id,
        )
        if current is not None and current.status in {
            ExecutorGenerationAuthorityStatus.PENDING,
            ExecutorGenerationAuthorityStatus.AUTHORIZED,
        }:
            self._require_start_authority(
                current,
                candidate,
                release_digest=release.release_digest,
                generation_id=generation_id,
                base_release_digest=base_release.release_digest,
                base_generation_id=base_generation_id,
                command_key=command_key,
                promotion_journal_id=promotion_journal_id,
            )
            return current
        if (
            current is not None
            and current.status is ExecutorGenerationAuthorityStatus.REVOKED
            and current.generation_id == generation_id
            and current.start_command_key == command_key
        ):
            raise AdapterError("revoked executor canary command cannot be replayed")
        pending = new_pending_executor_generation_authority(
            candidate_id=candidate.candidate_id,
            candidate_version=candidate.version,
            candidate_artifact_digest=candidate.candidate.artifact_digest,
            candidate_canonical_digest=candidate.candidate.canonical_digest,
            release_digest=release.release_digest,
            scope=candidate.subject_key,
            generation_id=generation_id,
            base_generation_id=base_generation_id,
            base_release_digest=base_release.release_digest,
            promotion_journal_id=promotion_journal_id,
            start_command_key=command_key,
            now=self._now(),
            previous=current,
        )
        return self._authority_repository.save(
            connection,
            pending,
            expected_version=current.version if current is not None else 0,
            reason_code="executor_canary_prepared",
        )

    def _ensure_authorized(
        self,
        expected: ExecutorGenerationAuthorityV1,
    ) -> ExecutorGenerationAuthorityV1:
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            current = self._authority_repository.get_current(
                connection,
                candidate_id=expected.candidate_id,
            )
            if current is None or self._authority_epoch_identity(current) != (
                self._authority_epoch_identity(expected)
            ):
                raise AdapterError("executor generation authority changed while warming")
            if current.status is ExecutorGenerationAuthorityStatus.AUTHORIZED:
                unit_of_work.commit()
                return current
            if current.status is not ExecutorGenerationAuthorityStatus.PENDING:
                raise AdapterError("executor generation authority was revoked while warming")
            authorized = transition_executor_generation_authority(
                current,
                ExecutorGenerationAuthorityStatus.AUTHORIZED,
                now=self._now(),
            )
            saved = self._authority_repository.save(
                connection,
                authorized,
                expected_version=current.version,
                reason_code="executor_generation_ready",
            )
            unit_of_work.commit()
            return saved

    def _revoke_terminal_preparation(
        self,
        expected: ExecutorGenerationAuthorityV1,
    ) -> None:
        """Remove a live authority only when warm durably terminalized its generation."""

        with self._unit_of_work_factory() as unit_of_work:
            generation = self._generation_repository.get_generation(
                unit_of_work.connection,
                scope=expected.scope,
                generation_id=expected.generation_id,
            )
            current = self._authority_repository.get_current(
                unit_of_work.connection,
                candidate_id=expected.candidate_id,
            )
            if generation is None or current is None:
                raise AdapterError("executor failed preparation state is missing")
            if generation.state is not RuntimeGenerationState.FAILED:
                unit_of_work.commit()
                return
            if self._authority_epoch_identity(current) != self._authority_epoch_identity(expected):
                raise AdapterError("executor failed preparation authority changed")
            if current.status is ExecutorGenerationAuthorityStatus.REVOKED:
                unit_of_work.commit()
                return
            if current.status not in {
                ExecutorGenerationAuthorityStatus.PENDING,
                ExecutorGenerationAuthorityStatus.AUTHORIZED,
            }:
                raise AdapterError("executor failed preparation authority is invalid")
            revoked = transition_executor_generation_authority(
                current,
                ExecutorGenerationAuthorityStatus.REVOKED,
                now=self._now(),
                revocation_reason="executor_canary_preparation_failed",
            )
            self._authority_repository.save(
                unit_of_work.connection,
                revoked,
                expected_version=current.version,
                reason_code="executor_canary_preparation_failed",
            )
            unit_of_work.commit()

    def _rollback_mode(
        self,
        candidate: EvolutionCandidateV1,
    ) -> tuple[_RollbackMode, ExecutorGenerationAuthorityV1]:
        with self._unit_of_work_factory() as unit_of_work:
            result = self._rollback_mode_current(unit_of_work.connection, candidate)
            unit_of_work.commit()
            return result

    def _rollback_mode_current(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
        *,
        rollback_candidate_version: int | None = None,
    ) -> tuple[_RollbackMode, ExecutorGenerationAuthorityV1]:
        authority = self._require_current_authority(connection, candidate)
        pointer = self._generation_repository.get_pointer(
            connection,
            scope=authority.scope,
        )
        target = self._generation_repository.get_generation(
            connection,
            scope=authority.scope,
            generation_id=authority.generation_id,
        )
        base = self._generation_repository.get_generation(
            connection,
            scope=authority.scope,
            generation_id=authority.base_generation_id,
        )
        if pointer is None or target is None or base is None:
            raise AdapterError("executor rollback generation state is missing")
        pointer_is_base = (
            pointer.active_generation_id == authority.base_generation_id
            and base.state is RuntimeGenerationState.ACTIVE
            and base.release_digest == authority.base_release_digest
        )
        pointer_is_candidate = (
            pointer.active_generation_id == authority.generation_id
            and pointer.last_good_generation_id == authority.base_generation_id
            and target.state is RuntimeGenerationState.ACTIVE
            and target.release_digest == authority.release_digest
            and base.state is RuntimeGenerationState.DRAINING
            and base.release_digest == authority.base_release_digest
        )
        effective_version = rollback_candidate_version or candidate.version
        canary_version = effective_version == authority.candidate_version + 2
        promoted_version = effective_version == authority.candidate_version + 3
        if (
            canary_version
            and pointer_is_base
            and target.state is RuntimeGenerationState.READY
            and target.release_digest == authority.release_digest
        ):
            if authority.status is ExecutorGenerationAuthorityStatus.AUTHORIZED:
                mode: _RollbackMode = "canary_apply"
            elif authority.status in {
                ExecutorGenerationAuthorityStatus.REVOKING,
                ExecutorGenerationAuthorityStatus.REVOKED,
            }:
                mode = "canary_complete"
            else:
                raise AdapterError("executor canary rollback authority is invalid")
        elif (
            canary_version
            and pointer_is_base
            and target.state
            in {
                RuntimeGenerationState.FAILED,
                RuntimeGenerationState.DISPOSED,
            }
            and authority.status is ExecutorGenerationAuthorityStatus.REVOKED
        ):
            mode = "canary_complete"
        elif (canary_version or promoted_version) and pointer_is_candidate:
            if authority.status is not ExecutorGenerationAuthorityStatus.AUTHORIZED:
                raise AdapterError("executor promoted rollback authority is invalid")
            mode = "promoted_apply"
        elif (
            (canary_version or promoted_version)
            and pointer_is_base
            and target.state is RuntimeGenerationState.DRAINING
            and target.release_digest == authority.release_digest
        ):
            if authority.status is ExecutorGenerationAuthorityStatus.AUTHORIZED:
                mode = "promoted_finalize"
            elif authority.status is ExecutorGenerationAuthorityStatus.REVOKED:
                mode = "promoted_complete"
            else:
                raise AdapterError("executor promoted rollback authority is invalid")
        elif (
            (canary_version or promoted_version)
            and pointer_is_base
            and target.state is RuntimeGenerationState.DISPOSED
            and authority.status is ExecutorGenerationAuthorityStatus.REVOKED
        ):
            mode = "promoted_complete"
        else:
            raise AdapterError("executor rollback state conflicts with authority")
        return mode, authority

    def _mark_canary_revoking(self, expected: ExecutorGenerationAuthorityV1) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            current = self._authority_repository.get_current(
                unit_of_work.connection,
                candidate_id=expected.candidate_id,
            )
            if current is None or self._authority_epoch_identity(current) != (
                self._authority_epoch_identity(expected)
            ):
                raise AdapterError("executor canary rollback authority changed")
            if current.status is ExecutorGenerationAuthorityStatus.AUTHORIZED:
                revoking = transition_executor_generation_authority(
                    current,
                    ExecutorGenerationAuthorityStatus.REVOKING,
                    now=self._now(),
                    revocation_reason="executor_canary_rollback",
                )
                self._authority_repository.save(
                    unit_of_work.connection,
                    revoking,
                    expected_version=current.version,
                    reason_code="executor_canary_rollback",
                )
            elif current.status not in {
                ExecutorGenerationAuthorityStatus.REVOKING,
                ExecutorGenerationAuthorityStatus.REVOKED,
            }:
                raise AdapterError("executor canary rollback authority is invalid")
            unit_of_work.commit()

    def _mark_promoted_revoked(self, expected: ExecutorGenerationAuthorityV1) -> None:
        with self._unit_of_work_factory() as unit_of_work:
            current = self._authority_repository.get_current(
                unit_of_work.connection,
                candidate_id=expected.candidate_id,
            )
            if current is None or self._authority_epoch_identity(current) != (
                self._authority_epoch_identity(expected)
            ):
                raise AdapterError("executor promoted rollback authority changed")
            if current.status is ExecutorGenerationAuthorityStatus.AUTHORIZED:
                revoked = transition_executor_generation_authority(
                    current,
                    ExecutorGenerationAuthorityStatus.REVOKED,
                    now=self._now(),
                    revocation_reason="executor_promoted_rollback",
                )
                self._authority_repository.save(
                    unit_of_work.connection,
                    revoked,
                    expected_version=current.version,
                    reason_code="executor_promoted_rollback",
                )
            elif current.status is not ExecutorGenerationAuthorityStatus.REVOKED:
                raise AdapterError("executor promoted rollback authority is invalid")
            unit_of_work.commit()

    def _require_current_authority(
        self,
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
    ) -> ExecutorGenerationAuthorityV1:
        authority = self._authority_repository.get_current(
            connection,
            candidate_id=candidate.candidate_id,
        )
        if authority is None:
            raise AdapterError("executor generation authority is missing")
        self._require_durable_candidate_core(connection, candidate)
        self._require_start_authority(authority, candidate, require_candidate_version=False)
        return authority

    @staticmethod
    def _require_durable_candidate_core(
        connection: sqlite3.Connection,
        candidate: EvolutionCandidateV1,
    ) -> None:
        durable = EvolutionRepository().get_candidate(connection, candidate.candidate_id)
        immutable_fields = (
            "schema_version",
            "candidate_id",
            "kind",
            "subject_key",
            "provenance",
            "base",
            "candidate",
            "diff_artifact_digest",
            "evolution_contract",
            "evolution_contract_hash",
            "rollback",
            "created_at",
        )
        if durable is None or any(
            getattr(durable, field) != getattr(candidate, field) for field in immutable_fields
        ):
            raise AdapterError("executor candidate durable binding mismatch")

    @staticmethod
    def _require_start_authority(
        authority: ExecutorGenerationAuthorityV1,
        candidate: EvolutionCandidateV1,
        *,
        release_digest: str | None = None,
        generation_id: str | None = None,
        base_release_digest: str | None = None,
        base_generation_id: str | None = None,
        command_key: str | None = None,
        promotion_journal_id: str | None = None,
        require_candidate_version: bool = True,
    ) -> None:
        if (
            authority.candidate_id != candidate.candidate_id
            or (require_candidate_version and authority.candidate_version != candidate.version)
            or authority.candidate_artifact_digest != candidate.candidate.artifact_digest
            or authority.candidate_canonical_digest != candidate.candidate.canonical_digest
            or authority.scope != candidate.subject_key
            or (release_digest is not None and authority.release_digest != release_digest)
            or (generation_id is not None and authority.generation_id != generation_id)
            or (
                base_release_digest is not None
                and authority.base_release_digest != base_release_digest
            )
            or (
                base_generation_id is not None
                and authority.base_generation_id != base_generation_id
            )
            or (command_key is not None and authority.start_command_key != command_key)
            or (
                promotion_journal_id is not None
                and authority.promotion_journal_id != promotion_journal_id
            )
        ):
            raise AdapterError("executor generation authority binding mismatch")

    @staticmethod
    def _authority_epoch_identity(authority: ExecutorGenerationAuthorityV1) -> tuple[object, ...]:
        return (
            authority.authority_id,
            authority.candidate_id,
            authority.epoch,
            authority.candidate_version,
            authority.candidate_artifact_digest,
            authority.candidate_canonical_digest,
            authority.release_digest,
            authority.scope,
            authority.generation_id,
            authority.base_generation_id,
            authority.base_release_digest,
            authority.promotion_journal_id,
            authority.start_command_key,
        )

    @staticmethod
    def _preparation_receipt(
        authority: ExecutorGenerationAuthorityV1,
    ) -> CanaryPreparationReceiptV1:
        if authority.status is not ExecutorGenerationAuthorityStatus.AUTHORIZED:
            raise AdapterError("executor generation authority is not authorized")
        return CanaryPreparationReceiptV1(
            candidate_id=authority.candidate_id,
            candidate_version=authority.candidate_version,
            candidate_artifact_digest=authority.candidate_artifact_digest,
            candidate_canonical_digest=authority.candidate_canonical_digest,
            release_digest=authority.release_digest,
            scope=authority.scope,
            generation_id=authority.generation_id,
            base_release_digest=authority.base_release_digest,
            base_generation_id=authority.base_generation_id,
            authority_id=authority.authority_id,
            authority_epoch=authority.epoch,
            authority_version=authority.version,
            promotion_journal_id=authority.promotion_journal_id,
        )

    @staticmethod
    def _require_candidate(
        candidate: EvolutionCandidateV1,
        *,
        lifecycle: CandidateLifecycle,
    ) -> None:
        if candidate.kind is not CandidateKind.EXECUTOR:
            raise AdapterError("executor promotion kind mismatch")
        if candidate.subject_key != "executor:keqing:pi":
            raise AdapterError("executor promotion subject is unsupported")
        if candidate.lifecycle is not lifecycle:
            raise AdapterError(f"executor promotion requires {lifecycle.value} candidate")

    @staticmethod
    def _require_command_identity(
        *,
        command_key: str,
        generation_id: str,
        promotion_journal_id: str,
    ) -> None:
        if (
            not command_key.strip()
            or _GENERATION_ID.fullmatch(generation_id) is None
            or _DIGEST.fullmatch(promotion_journal_id) is None
        ):
            raise AdapterError("executor canary command identity is invalid")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AdapterError("executor promotion clock must be timezone-aware")
        return value.astimezone(UTC)


__all__ = ["ExecutorPromotionAdapter"]
