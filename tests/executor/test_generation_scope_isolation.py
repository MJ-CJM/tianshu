"""Executor recovery ignores process-scope generations and releases."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.evolution.reconciler import GenerationReconciler
from tianshu.executor.adapters import ExecutorAdapterRegistry
from tianshu.executor.generation_controller import GenerationController, GenerationControllerError
from tianshu.executor.keqing.generation import PI_GENERATION_SCOPE
from tianshu.models.canonical import canonical_sha256
from tianshu.models.runtime_generation import (
    PROCESS_GENERATION_SCOPE,
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
)
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage.generation_repo import GenerationRepository

_NOW = datetime(2026, 8, 27, tzinfo=UTC)
_PI_SCOPE = PI_GENERATION_SCOPE
_ROOT = Path(__file__).resolve().parents[2]


class _RejectingMaterializer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def materialize(self, release: RuntimeReleaseV1):
        self.calls.append(release.release_digest)
        raise AssertionError("process releases must never reach the Pi materializer")


class _ProcessReleaseReadGuard(GenerationRepository):
    def __init__(self) -> None:
        self.process_release_reads: list[str] = []

    def get_release(
        self,
        connection,
        *,
        scope: str,
        release_digest: str,
    ) -> RuntimeReleaseV1 | None:
        if scope == PROCESS_GENERATION_SCOPE:
            self.process_release_reads.append(release_digest)
            raise AssertionError("Pi control must not decode process release material")
        return super().get_release(
            connection,
            scope=scope,
            release_digest=release_digest,
        )


async def _warm_probe(_bundle: object) -> tuple[bool, str | None]:
    raise AssertionError("process releases must never reach the Pi warm probe")


def _snapshot(marker: str) -> SystemSnapshotV1:
    components = {"kernel": marker * 64}
    return SystemSnapshotV1(
        components=components,
        digest=canonical_sha256(components),
    )


def _process_generation(
    snapshot: SystemSnapshotV1,
    marker: str,
    seconds: int,
) -> RuntimeGenerationV1:
    created_at = _NOW + timedelta(seconds=seconds)
    return RuntimeGenerationV1(
        generation_id="rg-" + marker * 32,
        scope=PROCESS_GENERATION_SCOPE,
        release_digest=snapshot.digest,
        state=RuntimeGenerationState.STAGED,
        version=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _ready_process_generation(
    repository: GenerationRepository,
    connection,
    snapshot: SystemSnapshotV1,
    marker: str,
    seconds: int,
) -> RuntimeGenerationV1:
    repository.insert_process_release(connection, snapshot, first_seen_at=_NOW)
    generation = repository.insert_staged(
        connection,
        _process_generation(snapshot, marker, seconds),
    )
    generation = repository.transition_pre_activation(
        connection,
        scope=PROCESS_GENERATION_SCOPE,
        generation_id=generation.generation_id,
        target_state=RuntimeGenerationState.WARMING,
        expected_version=generation.version,
        updated_at=_NOW + timedelta(seconds=seconds + 1),
    )
    return repository.transition_pre_activation(
        connection,
        scope=PROCESS_GENERATION_SCOPE,
        generation_id=generation.generation_id,
        target_state=RuntimeGenerationState.READY,
        expected_version=generation.version,
        updated_at=_NOW + timedelta(seconds=seconds + 2),
    )


def _seed_process_active_and_last_good(storage) -> tuple[str, str]:
    repository = GenerationRepository()
    first_snapshot = _snapshot("a")
    second_snapshot = _snapshot("b")
    with storage.unit_of_work() as unit_of_work:
        first = _ready_process_generation(
            repository,
            unit_of_work.connection,
            first_snapshot,
            "1",
            0,
        )
        first_activation = repository.activate(
            unit_of_work.connection,
            scope=PROCESS_GENERATION_SCOPE,
            target_generation_id=first.generation_id,
            expected_generation_version=first.version,
            expected_pointer_version=None,
            updated_at=_NOW + timedelta(seconds=3),
        )
        second = _ready_process_generation(
            repository,
            unit_of_work.connection,
            second_snapshot,
            "2",
            10,
        )
        second_activation = repository.activate(
            unit_of_work.connection,
            scope=PROCESS_GENERATION_SCOPE,
            target_generation_id=second.generation_id,
            expected_generation_version=second.version,
            expected_pointer_version=first_activation.pointer.version,
            updated_at=_NOW + timedelta(seconds=13),
        )
        unit_of_work.commit()
    return (
        second_activation.pointer.active_generation_id,
        second_activation.pointer.last_good_generation_id,
    )


def _corrupt_process_active_state(storage, generation_id: str) -> None:
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            "UPDATE runtime_generations SET state='draining' WHERE generation_id=?",
            (generation_id,),
        )
        unit_of_work.commit()


def _executor_release(scope: str) -> RuntimeReleaseV1:
    manifest = {"schema_version": "1", "manifest_id": "test"}
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": scope,
        "manifest": manifest,
        "manifest_hash": canonical_sha256(manifest),
        "cli_version": "0.83.0",
        "cli_version_source": "package_json",
        "binary_path": "/opt/tianshu/bin/pi",
        "binary_digest": "c" * 64,
        "package_name": "@earendil-works/pi-coding-agent",
        "package_entrypoint": "dist/cli.js",
        "package_digest": "d" * 64,
        "single_argv_shape": "single-v1",
        "session_argv_shape": "session-v1",
        "pi_wire_version": 3,
        "materializer_id": "test",
        "materializer_version": "1",
    }
    return RuntimeReleaseV1(**material, release_digest=canonical_sha256(material))


def test_pi_controller_recovery_ignores_process_active_and_last_good(storage) -> None:
    active_id, last_good_id = _seed_process_active_and_last_good(storage)
    materializer = _RejectingMaterializer()
    controller = GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        materializer,
        ExecutorAdapterRegistry(()),
        warm_probe=_warm_probe,
        managed_scopes=(_PI_SCOPE,),
        recovery_scopes=(_PI_SCOPE,),
        clock=lambda: _NOW + timedelta(seconds=20),
    )

    report = controller.recover()

    assert report.materialized_generation_ids == ()
    assert report.failed_generation_ids == ()
    assert materializer.calls == []
    with storage.unit_of_work() as unit_of_work:
        pointer = GenerationRepository().get_pointer(
            unit_of_work.connection,
            scope=PROCESS_GENERATION_SCOPE,
        )
        unit_of_work.commit()
    assert pointer is not None
    assert pointer.active_generation_id == active_id
    assert pointer.last_good_generation_id == last_good_id


def test_scoped_repository_queries_do_not_read_process_release_roots(storage) -> None:
    _seed_process_active_and_last_good(storage)
    repository = _ProcessReleaseReadGuard()

    with storage.unit_of_work() as unit_of_work:
        candidates = repository.list_recovery_candidates(
            unit_of_work.connection,
            scope=PI_GENERATION_SCOPE,
        )
        retained_ids = repository.retained_generation_ids(
            unit_of_work.connection,
            scope=PI_GENERATION_SCOPE,
        )
        unit_of_work.commit()

    assert candidates == ()
    assert retained_ids == frozenset()
    assert repository.process_release_reads == []


def test_managed_scope_rejects_unrelated_executor_before_materialization(storage) -> None:
    materializer = _RejectingMaterializer()
    controller = GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        materializer,
        ExecutorAdapterRegistry(()),
        warm_probe=_warm_probe,
        managed_scopes=(_PI_SCOPE,),
        recovery_scopes=(_PI_SCOPE,),
    )

    with pytest.raises(GenerationControllerError, match="not managed"):
        controller.stage(_executor_release("executor:keqing:other"))
    with pytest.raises(GenerationControllerError, match="not managed"):
        controller.stage(_executor_release(PROCESS_GENERATION_SCOPE))
    assert materializer.calls == []


def test_managed_scope_rejects_process_failure_compensation_before_writes(storage) -> None:
    repository = GenerationRepository()
    snapshot = _snapshot("a")
    with storage.unit_of_work() as unit_of_work:
        repository.insert_process_release(unit_of_work.connection, snapshot, first_seen_at=_NOW)
        staged = repository.insert_staged(
            unit_of_work.connection,
            _process_generation(snapshot, "1", 0),
        )
        unit_of_work.commit()
    materializer = _RejectingMaterializer()
    controller = GenerationController(
        repository,
        storage.unit_of_work,
        materializer,
        ExecutorAdapterRegistry(()),
        warm_probe=_warm_probe,
        managed_scopes=(_PI_SCOPE,),
        recovery_scopes=(_PI_SCOPE,),
    )
    hook_calls: list[str] = []

    with pytest.raises(GenerationControllerError, match="not managed"):
        controller.fail_pre_active_exact(
            PROCESS_GENERATION_SCOPE,
            generation_id=staged.generation_id,
            expected_release_digest=staged.release_digest,
            failure_commit_hook=lambda _connection, generation: hook_calls.append(
                generation.generation_id
            ),
        )

    with storage.unit_of_work() as unit_of_work:
        durable = repository.get_generation(
            unit_of_work.connection,
            scope=PROCESS_GENERATION_SCOPE,
            generation_id=staged.generation_id,
        )
        journal = repository.list_journal(
            unit_of_work.connection,
            generation_id=staged.generation_id,
        )
        unit_of_work.commit()
    assert durable == staged
    assert tuple(entry.to_state for entry in journal) == (RuntimeGenerationState.STAGED,)
    assert hook_calls == []
    assert materializer.calls == []


def test_pi_reconciler_ignores_process_scope_rows(storage) -> None:
    active_id, last_good_id = _seed_process_active_and_last_good(storage)
    reconciler = GenerationReconciler(
        GenerationRepository(),
        storage.unit_of_work,
        ExecutorAdapterRegistry(()),
        scope=_PI_SCOPE,
        snapshot_binding_available=lambda: False,
        clock=lambda: _NOW + timedelta(seconds=20),
    )

    assert reconciler.reconcile_once() == 0
    assert reconciler.readiness_snapshot() == (True, ())
    with storage.unit_of_work() as unit_of_work:
        pointer = GenerationRepository().get_pointer(
            unit_of_work.connection,
            scope=PROCESS_GENERATION_SCOPE,
        )
        unit_of_work.commit()
    assert pointer is not None
    assert pointer.active_generation_id == active_id
    assert pointer.last_good_generation_id == last_good_id


def test_pi_controller_recovery_does_not_validate_corrupt_process_pointer(storage) -> None:
    active_id, _last_good_id = _seed_process_active_and_last_good(storage)
    _corrupt_process_active_state(storage, active_id)
    materializer = _RejectingMaterializer()
    controller = GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        materializer,
        ExecutorAdapterRegistry(()),
        warm_probe=_warm_probe,
        managed_scopes=(_PI_SCOPE,),
        recovery_scopes=(_PI_SCOPE,),
    )

    report = controller.recover()

    assert report.materialized_generation_ids == ()
    assert report.failed_generation_ids == ()
    assert materializer.calls == []


def test_pi_reconciler_readiness_does_not_validate_corrupt_process_pointer(storage) -> None:
    active_id, _last_good_id = _seed_process_active_and_last_good(storage)
    _corrupt_process_active_state(storage, active_id)
    reconciler = GenerationReconciler(
        GenerationRepository(),
        storage.unit_of_work,
        ExecutorAdapterRegistry(()),
        scope=_PI_SCOPE,
    )

    assert reconciler.reconcile_once() == 0
    assert reconciler.readiness_snapshot() == (True, ())


def test_production_wiring_pins_generation_control_to_pi_scope() -> None:
    source_path = _ROOT / "src/tianshu/bootstrap/wiring_executor.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    constructor_calls = {
        node.func.id: node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"GenerationController", "GenerationReconciler"}
    }

    controller_keywords = {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in constructor_calls["GenerationController"].keywords
    }
    reconciler_keywords = {
        keyword.arg: ast.unparse(keyword.value)
        for keyword in constructor_calls["GenerationReconciler"].keywords
    }
    assert controller_keywords["managed_scopes"] == "(PI_GENERATION_SCOPE,)"
    assert controller_keywords["recovery_scopes"] == "(PI_GENERATION_SCOPE,)"
    assert reconciler_keywords["scope"] == "PI_GENERATION_SCOPE"
