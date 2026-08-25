"""Serialized reconciliation for Evolution rollbacks and runtime generations."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock

from tianshu.evolution.promotion import PromotionConflict, PromotionService
from tianshu.executor.adapters import (
    ExecutorAdapterRegistry,
    ExecutorGenerationConflict,
    ExecutorGenerationError,
    MaterializedExecutorGeneration,
)
from tianshu.models.runtime_generation import RuntimeGenerationState, RuntimeGenerationV1
from tianshu.storage.generation_repo import (
    GenerationRepository,
    GenerationRepositoryError,
)
from tianshu.storage.unit_of_work import SqliteUnitOfWork

logger = logging.getLogger(__name__)

GENERATION_CLEANUP_ONLY_ERRORS = frozenset(
    {
        "generation_draining_pending",
        "terminal_generation_material_retained",
    }
)


class EvolutionRollbackReconciler:
    """Drive only the existing PromotionService rollback authority."""

    def __init__(self, promotion_service: PromotionService, *, limit: int = 50) -> None:
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self._promotion_service = promotion_service
        self._limit = limit
        self._lock = Lock()
        self._last_error_code: str | None = None

    @property
    def last_error_code(self) -> str | None:
        return self._last_error_code

    def reconcile_once(self) -> int:
        with self._lock:
            try:
                completed = self._promotion_service.reconcile_pending_rollbacks(limit=self._limit)
            except PromotionConflict as exc:
                self._last_error_code = str(exc)
                logger.warning("Evolution rollback reconciliation deferred: %s", exc)
                return 0
            errors = self._promotion_service.reconciliation_error_codes
            self._last_error_code = errors[0] if errors else None
            if errors:
                logger.warning(
                    "Evolution rollback candidates remain pending: %s",
                    ",".join(errors),
                )
            return completed

    def readiness_probe(self) -> bool:
        return not self._promotion_service.has_pending_rollbacks()


type UnitOfWorkFactory = Callable[[], SqliteUnitOfWork]


class GenerationReconciler:
    """Dispose unreferenced draining generations without owning startup recovery.

    Lock order is always ``unit of work -> generation registry``.  The durable
    transition commits before the corresponding process-local bundle is
    removed, while both authorities remain serialized against selection and
    activation.
    """

    def __init__(
        self,
        repository: GenerationRepository,
        unit_of_work_factory: UnitOfWorkFactory,
        registry: ExecutorAdapterRegistry,
        *,
        clock: Callable[[], datetime] | None = None,
        snapshot_binding_available: Callable[[], bool] | None = None,
    ) -> None:
        self._repository = repository
        self._unit_of_work_factory = unit_of_work_factory
        self._registry = registry
        self._clock = clock or (lambda: datetime.now(UTC))
        self._snapshot_binding_available = snapshot_binding_available or (lambda: True)
        self._lock = Lock()
        self._last_error_code: str | None = None
        self._readiness_error_codes: tuple[str, ...] = ()

    @property
    def last_error_code(self) -> str | None:
        return self._last_error_code

    @property
    def readiness_error_codes(self) -> tuple[str, ...]:
        """Return stable codes from the most recent readiness probe."""

        return self._readiness_error_codes

    def reconcile_once(self) -> int:
        """Dispose every currently unreferenced draining generation once."""

        with self._lock:
            disposed: list[RuntimeGenerationV1] = []
            try:
                with (
                    self._unit_of_work_factory() as unit_of_work,
                    self._registry.generation_guard(),
                ):
                    terminal = self._terminal_registry_generations(
                        unit_of_work.connection,
                    )
                    candidates = self._repository.list_recovery_candidates(unit_of_work.connection)
                    self._repair_nonterminal_registry_states(
                        unit_of_work.connection,
                        candidates,
                    )
                    retained_ids = self._repository.retained_generation_ids(unit_of_work.connection)
                    for generation in candidates:
                        if generation.state is not RuntimeGenerationState.DRAINING:
                            continue
                        if generation.generation_id in retained_ids:
                            continue
                        if self._registry.active_attempt_count(generation.generation_id):
                            continue
                        self._validate_registry_record(
                            unit_of_work.connection,
                            generation,
                        )
                        durable = self._repository.dispose_if_unreferenced(
                            unit_of_work.connection,
                            scope=generation.scope,
                            generation_id=generation.generation_id,
                            expected_version=generation.version,
                            updated_at=self._now(),
                        )
                        if durable is not None:
                            disposed.append(durable)

                    unit_of_work.commit()
                    for generation in terminal:
                        self._repair_terminal_registry_record(
                            unit_of_work.connection,
                            generation,
                        )
                    for generation in disposed:
                        if self._registry.generation_record(generation.generation_id) is None:
                            continue
                        self._registry.update_generation_state(
                            generation.generation_id,
                            RuntimeGenerationState.DISPOSED.value,
                        )
                        self._registry.remove_generation(generation.generation_id)
            except GenerationRepositoryError as exc:
                self._last_error_code = "generation_reconciliation_repository_conflict"
                logger.warning("Generation reconciliation deferred: %s", exc)
                return 0
            except ExecutorGenerationError as exc:
                self._last_error_code = "generation_reconciliation_registry_conflict"
                logger.warning("Generation registry reconciliation deferred: %s", exc)
                return 0

            self._last_error_code = None
            return len(disposed)

    def readiness_probe(self) -> bool:
        """Aggregate active-material and unconverged-draining readiness."""

        ready, _error_codes = self.readiness_snapshot()
        return ready

    def readiness_snapshot(self) -> tuple[bool, tuple[str, ...]]:
        """Return one lock-consistent readiness result and its stable codes."""

        try:
            snapshot_binding_available = self._snapshot_binding_available() is True
        except Exception:  # noqa: BLE001 - expose a stable code, never resolver details
            snapshot_binding_available = False
        with self._lock:
            try:
                with (
                    self._unit_of_work_factory() as unit_of_work,
                    self._registry.generation_guard(),
                ):
                    candidates = self._repository.list_recovery_candidates(unit_of_work.connection)
                    retained_ids = self._repository.retained_generation_ids(unit_of_work.connection)
                    error_codes = self._readiness_errors(
                        unit_of_work.connection,
                        candidates,
                        retained_ids=retained_ids,
                        snapshot_binding_available=snapshot_binding_available,
                    )
                    unit_of_work.commit()
            except (GenerationRepositoryError, ExecutorGenerationError) as exc:
                logger.warning("Generation readiness probe failed: %s", exc)
                error_codes = ("generation_readiness_probe_failed",)
            self._readiness_error_codes = error_codes
            return not error_codes, error_codes

    def _readiness_errors(
        self,
        connection: sqlite3.Connection,
        candidates: tuple[RuntimeGenerationV1, ...],
        *,
        retained_ids: frozenset[str],
        snapshot_binding_available: bool,
    ) -> tuple[str, ...]:
        codes: set[str] = set()
        if not snapshot_binding_available and any(
            generation.state in {RuntimeGenerationState.ACTIVE, RuntimeGenerationState.READY}
            or (
                generation.state is RuntimeGenerationState.DRAINING
                and generation.generation_id in retained_ids
            )
            for generation in candidates
        ):
            codes.add("generation_binding_resolver_unavailable")
        records = {record.generation_id: record for record in self._registry.generation_records()}
        for generation in candidates:
            record = records.pop(generation.generation_id, None)
            if generation.state is RuntimeGenerationState.ACTIVE:
                if record is None:
                    codes.add("active_generation_material_missing")
                elif not self._registry_record_matches(
                    connection,
                    generation,
                    record,
                ):
                    codes.add("active_generation_material_mismatch")
            elif (
                generation.state is RuntimeGenerationState.DRAINING
                and generation.generation_id not in retained_ids
            ):
                codes.add("generation_draining_pending")
            elif generation.state is RuntimeGenerationState.DRAINING:
                if record is None:
                    codes.add("retained_generation_material_missing")
                elif not self._registry_record_matches(
                    connection,
                    generation,
                    record,
                ):
                    codes.add("retained_generation_material_mismatch")
            elif record is None:
                codes.add("generation_material_missing")
            elif not self._registry_record_matches(
                connection,
                generation,
                record,
            ):
                codes.add("generation_material_mismatch")

        for record in records.values():
            durable = self._repository.get_generation(
                connection,
                scope=record.scope,
                generation_id=record.generation_id,
            )
            if durable is None or not self._registry_record_matches(
                connection,
                durable,
                record,
                include_state=False,
            ):
                codes.add("generation_material_mismatch")
            elif durable.state in {
                RuntimeGenerationState.FAILED,
                RuntimeGenerationState.DISPOSED,
            }:
                codes.add("terminal_generation_material_retained")
            elif record.state != durable.state.value:
                codes.add("generation_material_mismatch")
        return tuple(sorted(codes))

    def _validate_registry_record(
        self,
        connection: sqlite3.Connection,
        generation: RuntimeGenerationV1,
    ) -> None:
        record = self._registry.generation_record(generation.generation_id)
        if record is None:
            return
        if not self._registry_record_matches(
            connection,
            generation,
            record,
        ):
            raise ExecutorGenerationConflict(
                f"durable and materialized generation disagree: {generation.generation_id}"
            )

    def _terminal_registry_generations(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[RuntimeGenerationV1, ...]:
        terminal: list[RuntimeGenerationV1] = []
        for record in self._registry.generation_records():
            durable = self._repository.get_generation(
                connection,
                scope=record.scope,
                generation_id=record.generation_id,
            )
            if durable is None:
                raise ExecutorGenerationConflict(
                    f"materialized generation has no durable identity: {record.generation_id}"
                )
            if durable.state not in {
                RuntimeGenerationState.FAILED,
                RuntimeGenerationState.DISPOSED,
            }:
                continue
            if not self._registry_record_matches(
                connection,
                durable,
                record,
                include_state=False,
            ):
                raise ExecutorGenerationConflict(
                    f"durable and materialized generation disagree: {record.generation_id}"
                )
            terminal.append(durable)
        return tuple(terminal)

    def _repair_nonterminal_registry_states(
        self,
        connection: sqlite3.Connection,
        candidates: tuple[RuntimeGenerationV1, ...],
    ) -> None:
        for generation in candidates:
            if generation.state not in {
                RuntimeGenerationState.ACTIVE,
                RuntimeGenerationState.DRAINING,
            }:
                continue
            record = self._registry.generation_record(generation.generation_id)
            if record is None:
                continue
            if not self._registry_record_matches(
                connection,
                generation,
                record,
                include_state=False,
            ):
                raise ExecutorGenerationConflict(
                    f"durable and materialized generation disagree: {generation.generation_id}"
                )
            if record.state == generation.state.value:
                continue
            release = self._repository.get_release(
                connection,
                scope=generation.scope,
                release_digest=generation.release_digest,
            )
            if release is None:
                raise ExecutorGenerationConflict(
                    f"generation release is missing: {generation.generation_id}"
                )
            self._registry.reconcile_generation_state(
                generation.generation_id,
                generation.state.value,
                expected_scope=generation.scope,
                expected_release_digest=generation.release_digest,
                expected_manifest_digests={
                    record.adapter.adapter_id: release.manifest_hash,
                },
            )

    def _repair_terminal_registry_record(
        self,
        connection: sqlite3.Connection,
        generation: RuntimeGenerationV1,
    ) -> None:
        record = self._registry.generation_record(generation.generation_id)
        if record is None:
            return
        release = self._repository.get_release(
            connection,
            scope=generation.scope,
            release_digest=generation.release_digest,
        )
        if release is None:
            raise ExecutorGenerationConflict(
                f"generation release is missing: {generation.generation_id}"
            )
        self._registry.reconcile_generation_state(
            generation.generation_id,
            generation.state.value,
            expected_scope=generation.scope,
            expected_release_digest=generation.release_digest,
            expected_manifest_digests={
                record.adapter.adapter_id: release.manifest_hash,
            },
        )
        self._registry.remove_generation(generation.generation_id)

    def _registry_record_matches(
        self,
        connection: sqlite3.Connection,
        generation: RuntimeGenerationV1,
        record: MaterializedExecutorGeneration,
        *,
        include_state: bool = True,
    ) -> bool:
        if (
            record.scope != generation.scope
            or record.release_digest != generation.release_digest
            or (include_state and record.state != generation.state.value)
        ):
            return False
        release = self._repository.get_release(
            connection,
            scope=generation.scope,
            release_digest=generation.release_digest,
        )
        return release is not None and record.executor_manifest_digests == (
            (record.adapter.adapter_id, release.manifest_hash),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generation reconciler clock must be timezone-aware")
        return value.astimezone(UTC)


__all__ = [
    "GENERATION_CLEANUP_ONLY_ERRORS",
    "EvolutionRollbackReconciler",
    "GenerationReconciler",
]
