"""Runtime-generation reconciliation retention and readiness contract."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from tianshu.evolution.promotion import PromotionService
from tianshu.evolution.reconciler import (
    EvolutionRollbackReconciler,
    GenerationReconciler,
)
from tianshu.executor.adapters import (
    ExecutorAdapterRegistry,
    ExecutorGenerationConflict,
)
from tianshu.executor.adapters.protocol import ExecutionMode, PreparedExecution
from tianshu.executor.capabilities import (
    ExecutorCapabilityManifestV1,
    HostCapabilityProbeV1,
    pi_manifest,
)
from tianshu.models.canonical import canonical_sha256
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.storage import Storage
from tianshu.storage.generation_repo import GenerationRepository

_NOW = datetime(2026, 8, 26, tzinfo=UTC)
_SCOPE = "executor:keqing:pi"
_FIRST = "rg-" + "1" * 32
_SECOND = "rg-" + "2" * 32
_THIRD = "rg-" + "3" * 32
_SNAPSHOT = "f" * 64


@dataclass
class _Adapter:
    manifest: ExecutorCapabilityManifestV1

    @property
    def adapter_id(self) -> str:
        return self.manifest.adapter_id

    @property
    def supported_execution_modes(self) -> tuple[ExecutionMode, ...]:
        return self.manifest.execution_modes

    def probe(self) -> HostCapabilityProbeV1:
        return HostCapabilityProbeV1(
            probe_id="generation-reconciler-test",
            os_name="test",
            architecture="test",
            git_available=True,
            process_groups_available=True,
            sandbox_backend=None,
        )

    def prepare(
        self,
        effective: EffectiveGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
    ) -> PreparedExecution:
        return PreparedExecution(
            run_id=run_id,
            effective=effective,
            instruction=instruction,
            execution_mode=execution_mode,
        )

    async def execute(self, prepared: PreparedExecution, *_args: Any, **_kwargs: Any) -> str:
        return prepared.run_id

    async def cancel(self, _run_id: str) -> bool:
        return False


class _ObservedRegistry(ExecutorAdapterRegistry):
    def __init__(self, storage: Storage, adapter: _Adapter) -> None:
        super().__init__((adapter,))
        self._storage = storage
        self.guard_entered_in_transaction: list[bool] = []
        self.removed_in_transaction: list[bool] = []

    @contextmanager
    def generation_guard(self) -> Iterator[None]:
        self.guard_entered_in_transaction.append(self._storage._conn.in_transaction)  # noqa: SLF001
        with super().generation_guard():
            yield

    def remove_generation(self, generation_id: str) -> object | None:
        self.removed_in_transaction.append(self._storage._conn.in_transaction)  # noqa: SLF001
        return super().remove_generation(generation_id)


class _FaultingObservedRegistry(_ObservedRegistry):
    def __init__(self, storage: Storage, adapter: _Adapter) -> None:
        super().__init__(storage, adapter)
        self.update_failures = 0
        self.remove_failures = 0

    def update_generation_state(self, generation_id: str, state: str):
        if self.update_failures:
            self.update_failures -= 1
            raise ExecutorGenerationConflict("injected state publication failure")
        return super().update_generation_state(generation_id, state)

    def remove_generation(self, generation_id: str) -> object | None:
        if self.remove_failures:
            self.remove_failures -= 1
            raise ExecutorGenerationConflict("injected bundle removal failure")
        return super().remove_generation(generation_id)


def _release() -> RuntimeReleaseV1:
    manifest = pi_manifest()
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": _SCOPE,
        "manifest": manifest.model_dump(mode="json"),
        "manifest_hash": manifest.content_hash,
        "cli_version": "0.83.0",
        "cli_version_source": "package_json",
        "binary_path": "/opt/tianshu/bin/pi",
        "binary_digest": "a" * 64,
        "package_name": "@earendil-works/pi-coding-agent",
        "package_entrypoint": "dist/cli.js",
        "package_digest": "b" * 64,
        "single_argv_shape": "single-v1",
        "session_argv_shape": "session-v1",
        "pi_wire_version": 3,
        "materializer_id": "test-materializer",
        "materializer_version": "1",
    }
    return RuntimeReleaseV1(
        schema_version=1,
        scope=_SCOPE,
        manifest=manifest.model_dump(mode="json"),
        manifest_hash=manifest.content_hash,
        cli_version="0.83.0",
        cli_version_source="package_json",
        binary_path="/opt/tianshu/bin/pi",
        binary_digest="a" * 64,
        package_name="@earendil-works/pi-coding-agent",
        package_entrypoint="dist/cli.js",
        package_digest="b" * 64,
        single_argv_shape="single-v1",
        session_argv_shape="session-v1",
        pi_wire_version=3,
        materializer_id="test-materializer",
        materializer_version="1",
        release_digest=canonical_sha256(material),
    )


def _staged(release: RuntimeReleaseV1, generation_id: str, seconds: int) -> RuntimeGenerationV1:
    created_at = _NOW + timedelta(seconds=seconds)
    return RuntimeGenerationV1(
        generation_id=generation_id,
        scope=release.scope,
        release_digest=release.release_digest,
        state=RuntimeGenerationState.STAGED,
        version=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _insert_ready(
    repository: GenerationRepository,
    connection,
    release: RuntimeReleaseV1,
    generation_id: str,
    seconds: int,
) -> RuntimeGenerationV1:
    generation = repository.insert_staged(
        connection,
        _staged(release, generation_id, seconds),
    )
    generation = repository.transition_pre_activation(
        connection,
        scope=_SCOPE,
        generation_id=generation_id,
        target_state=RuntimeGenerationState.WARMING,
        expected_version=generation.version,
        updated_at=_NOW + timedelta(seconds=seconds + 1),
    )
    return repository.transition_pre_activation(
        connection,
        scope=_SCOPE,
        generation_id=generation_id,
        target_state=RuntimeGenerationState.READY,
        expected_version=generation.version,
        updated_at=_NOW + timedelta(seconds=seconds + 2),
    )


def _seed_three_activated(
    storage: Storage,
    registry: ExecutorAdapterRegistry,
) -> tuple[GenerationRepository, RuntimeReleaseV1]:
    repository = GenerationRepository()
    release = _release()
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        repository.insert_release(connection, release, first_seen_at=_NOW)
        first = _insert_ready(repository, connection, release, _FIRST, 0)
        first_activation = repository.activate(
            connection,
            scope=_SCOPE,
            target_generation_id=first.generation_id,
            expected_generation_version=first.version,
            expected_pointer_version=None,
            updated_at=_NOW + timedelta(seconds=3),
        )
        second = _insert_ready(repository, connection, release, _SECOND, 10)
        second_activation = repository.activate(
            connection,
            scope=_SCOPE,
            target_generation_id=second.generation_id,
            expected_generation_version=second.version,
            expected_pointer_version=first_activation.pointer.version,
            updated_at=_NOW + timedelta(seconds=13),
        )
        third = _insert_ready(repository, connection, release, _THIRD, 20)
        repository.activate(
            connection,
            scope=_SCOPE,
            target_generation_id=third.generation_id,
            expected_generation_version=third.version,
            expected_pointer_version=second_activation.pointer.version,
            updated_at=_NOW + timedelta(seconds=23),
        )
        durable = tuple(
            repository.get_generation(connection, scope=_SCOPE, generation_id=generation_id)
            for generation_id in (_FIRST, _SECOND, _THIRD)
        )
        unit_of_work.commit()

    adapter = _Adapter(pi_manifest())
    for generation in durable:
        assert generation is not None
        registry.install_generation(
            generation_id=generation.generation_id,
            scope=generation.scope,
            release_digest=generation.release_digest,
            state=generation.state.value,
            adapter=adapter,
            bundle=object(),
        )
    return repository, release


def _insert_open_continuity(storage: Storage, generation_id: str) -> None:
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute(
            """
            INSERT INTO edicts (id, goal, status, created_at, schedule_json)
            VALUES ('edict-generation', 'generation retention', 'open', ?, ?)
            """,
            (_NOW.isoformat(), json.dumps({"type": "immediate"}, separators=(",", ":"))),
        )
        connection.execute(
            """
            INSERT INTO memorials (id, edict_id, status, created_at, dag_node_id)
            VALUES ('memorial-generation', 'edict-generation', 'pending', ?, NULL)
            """,
            (_NOW.isoformat(),),
        )
        connection.execute(
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
            (_SNAPSHOT, _NOW.isoformat()),
        )
        connection.execute(
            """
            INSERT INTO run_system_bindings (
                memorial_id, attempt_id, snapshot_digest, generation_ids_json, created_at
            ) VALUES ('memorial-generation', 'attempt-generation', ?, ?, ?)
            """,
            (
                _SNAPSHOT,
                json.dumps([generation_id], separators=(",", ":")),
                _NOW.isoformat(),
            ),
        )
        unit_of_work.commit()


def _generation(
    storage: Storage,
    repository: GenerationRepository,
    generation_id: str,
) -> RuntimeGenerationV1:
    with storage.unit_of_work() as unit_of_work:
        generation = repository.get_generation(
            unit_of_work.connection,
            scope=_SCOPE,
            generation_id=generation_id,
        )
        unit_of_work.commit()
    assert generation is not None
    return generation


def test_retained_roots_are_not_disposed_then_unreferenced_draining_converges(
    storage: Storage,
) -> None:
    registry = _ObservedRegistry(storage, _Adapter(pi_manifest()))
    repository, _release_record = _seed_three_activated(storage, registry)
    _insert_open_continuity(storage, _FIRST)
    reconciler = GenerationReconciler(
        repository,
        storage.unit_of_work,
        registry,
        clock=lambda: _NOW + timedelta(seconds=30),
    )

    assert reconciler.reconcile_once() == 0
    assert _generation(storage, repository, _FIRST).state is RuntimeGenerationState.DRAINING
    assert _generation(storage, repository, _SECOND).state is RuntimeGenerationState.DRAINING
    assert _generation(storage, repository, _THIRD).state is RuntimeGenerationState.ACTIVE
    assert reconciler.readiness_probe() is True

    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            "UPDATE edicts SET status='archived' WHERE id='edict-generation'"
        )
        unit_of_work.commit()

    assert reconciler.readiness_probe() is False
    assert reconciler.readiness_error_codes == ("generation_draining_pending",)
    assert reconciler.reconcile_once() == 1
    assert _generation(storage, repository, _FIRST).state is RuntimeGenerationState.DISPOSED
    assert registry.generation_record(_FIRST) is None
    assert registry.generation_record(_SECOND) is not None
    assert registry.generation_record(_THIRD) is not None
    assert reconciler.readiness_probe() is True
    assert all(registry.guard_entered_in_transaction)
    assert registry.removed_in_transaction == [False]


def test_process_local_attempt_lease_defers_disposal_without_a_fixed_wait(storage: Storage) -> None:
    registry = ExecutorAdapterRegistry((_Adapter(pi_manifest()),))
    repository, _release_record = _seed_three_activated(storage, registry)
    registry.reserve_binding(
        "attempt-live",
        pinned_ids=(_FIRST,),
        required_scopes=(_SCOPE,),
    )
    reconciler = GenerationReconciler(
        repository,
        storage.unit_of_work,
        registry,
        clock=lambda: _NOW + timedelta(seconds=30),
    )

    assert reconciler.reconcile_once() == 0
    assert _generation(storage, repository, _FIRST).state is RuntimeGenerationState.DRAINING
    assert reconciler.readiness_probe() is False

    assert registry.release("attempt-live") is True
    assert reconciler.reconcile_once() == 1
    assert _generation(storage, repository, _FIRST).state is RuntimeGenerationState.DISPOSED


@pytest.mark.parametrize("failure_point", ("update", "remove"))
def test_disposed_registry_publication_failure_converges_on_next_tick(
    storage: Storage,
    failure_point: str,
) -> None:
    registry = _FaultingObservedRegistry(storage, _Adapter(pi_manifest()))
    repository, _release_record = _seed_three_activated(storage, registry)
    reconciler = GenerationReconciler(
        repository,
        storage.unit_of_work,
        registry,
        clock=lambda: _NOW + timedelta(seconds=30),
    )
    if failure_point == "update":
        registry.update_failures = 1
    else:
        registry.remove_failures = 1

    assert reconciler.reconcile_once() == 0
    assert _generation(storage, repository, _FIRST).state is RuntimeGenerationState.DISPOSED
    assert registry.generation_record(_FIRST) is not None
    assert reconciler.readiness_probe() is False
    assert reconciler.readiness_error_codes == ("terminal_generation_material_retained",)

    assert reconciler.reconcile_once() == 0
    assert registry.generation_record(_FIRST) is None
    assert reconciler.readiness_probe() is True
    assert registry.removed_in_transaction == [False]


def test_persistent_terminal_registry_failure_never_reports_ready(storage: Storage) -> None:
    registry = _FaultingObservedRegistry(storage, _Adapter(pi_manifest()))
    repository, _release_record = _seed_three_activated(storage, registry)
    registry.remove_failures = 10
    reconciler = GenerationReconciler(
        repository,
        storage.unit_of_work,
        registry,
        clock=lambda: _NOW + timedelta(seconds=30),
    )

    assert reconciler.reconcile_once() == 0
    assert _generation(storage, repository, _FIRST).state is RuntimeGenerationState.DISPOSED
    assert reconciler.readiness_probe() is False
    assert reconciler.readiness_error_codes == ("terminal_generation_material_retained",)
    assert reconciler.reconcile_once() == 0
    assert reconciler.readiness_probe() is False


def test_active_material_missing_degrades_readiness_until_rehydrated(storage: Storage) -> None:
    registry = ExecutorAdapterRegistry((_Adapter(pi_manifest()),))
    repository = GenerationRepository()
    release = _release()
    with storage.unit_of_work() as unit_of_work:
        repository.insert_release(unit_of_work.connection, release, first_seen_at=_NOW)
        ready = _insert_ready(repository, unit_of_work.connection, release, _FIRST, 0)
        active = repository.activate(
            unit_of_work.connection,
            scope=_SCOPE,
            target_generation_id=ready.generation_id,
            expected_generation_version=ready.version,
            expected_pointer_version=None,
            updated_at=_NOW + timedelta(seconds=3),
        ).activated
        unit_of_work.commit()
    reconciler = GenerationReconciler(repository, storage.unit_of_work, registry)

    assert reconciler.readiness_probe() is False
    assert reconciler.readiness_error_codes == ("active_generation_material_missing",)

    registry.install_generation(
        generation_id=active.generation_id,
        scope=active.scope,
        release_digest=active.release_digest,
        state=active.state.value,
        adapter=_Adapter(pi_manifest()),
        bundle=object(),
    )
    assert reconciler.readiness_probe() is True
    assert reconciler.readiness_error_codes == ()


@pytest.mark.parametrize("provider_mode", ("missing", "broken"))
def test_serving_generation_requires_a_working_snapshot_binding_resolver(
    storage: Storage,
    provider_mode: str,
) -> None:
    registry = ExecutorAdapterRegistry((_Adapter(pi_manifest()),))
    repository = GenerationRepository()
    release = _release()
    with storage.unit_of_work() as unit_of_work:
        repository.insert_release(unit_of_work.connection, release, first_seen_at=_NOW)
        ready = _insert_ready(repository, unit_of_work.connection, release, _FIRST, 0)
        active = repository.activate(
            unit_of_work.connection,
            scope=_SCOPE,
            target_generation_id=ready.generation_id,
            expected_generation_version=ready.version,
            expected_pointer_version=None,
            updated_at=_NOW + timedelta(seconds=3),
        ).activated
        unit_of_work.commit()
    registry.install_generation(
        generation_id=active.generation_id,
        scope=active.scope,
        release_digest=active.release_digest,
        state=active.state.value,
        adapter=_Adapter(pi_manifest()),
        bundle=object(),
    )
    available = False

    def snapshot_binding_available() -> bool:
        if provider_mode == "broken" and not available:
            raise RuntimeError("sensitive resolver failure")
        return available

    reconciler = GenerationReconciler(
        repository,
        storage.unit_of_work,
        registry,
        snapshot_binding_available=snapshot_binding_available,
    )

    assert reconciler.readiness_snapshot() == (
        False,
        ("generation_binding_resolver_unavailable",),
    )
    available = True
    assert reconciler.readiness_snapshot() == (True, ())


def test_committed_activation_state_mismatch_never_reports_ready(storage: Storage) -> None:
    registry = ExecutorAdapterRegistry((_Adapter(pi_manifest()),))
    repository, _release_record = _seed_three_activated(storage, registry)
    registry.update_generation_state(_SECOND, RuntimeGenerationState.ACTIVE.value)
    registry.update_generation_state(_THIRD, RuntimeGenerationState.READY.value)
    reconciler = GenerationReconciler(repository, storage.unit_of_work, registry)

    assert reconciler.readiness_probe() is False
    assert reconciler.readiness_error_codes == (
        "active_generation_material_mismatch",
        "generation_draining_pending",
        "retained_generation_material_mismatch",
    )


def test_reconciler_leaves_pre_active_recovery_to_controller(storage: Storage) -> None:
    registry = ExecutorAdapterRegistry((_Adapter(pi_manifest()),))
    repository = GenerationRepository()
    release = _release()
    with storage.unit_of_work() as unit_of_work:
        repository.insert_release(unit_of_work.connection, release, first_seen_at=_NOW)
        staged = repository.insert_staged(
            unit_of_work.connection,
            _staged(release, _FIRST, 0),
        )
        ready = _insert_ready(repository, unit_of_work.connection, release, _SECOND, 10)
        unit_of_work.commit()
    reconciler = GenerationReconciler(repository, storage.unit_of_work, registry)

    assert reconciler.reconcile_once() == 0
    assert _generation(storage, repository, staged.generation_id) == staged
    assert _generation(storage, repository, ready.generation_id) == ready


class _RollbackService:
    reconciliation_error_codes: tuple[str, ...] = ()

    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def reconcile_pending_rollbacks(self, *, limit: int) -> int:
        assert limit == 50
        self._calls.append("rollback")
        return 1

    def has_pending_rollbacks(self) -> bool:
        return False


def test_generation_and_rollback_reconcilers_have_independent_locks_and_compose_in_order(
    storage: Storage,
) -> None:
    calls: list[str] = []
    rollback = EvolutionRollbackReconciler(cast(PromotionService, _RollbackService(calls)))
    generation = GenerationReconciler(
        GenerationRepository(),
        storage.unit_of_work,
        ExecutorAdapterRegistry(),
    )

    assert rollback._lock is not generation._lock  # noqa: SLF001
    assert rollback.reconcile_once() == 1
    calls.append("generation")
    assert generation.reconcile_once() == 0
    assert calls == ["rollback", "generation"]
    assert rollback.readiness_probe() is True
    assert generation.readiness_probe() is True
