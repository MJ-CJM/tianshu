from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from tianshu.evolution.reconciler import GenerationReconciler
from tianshu.executor.adapters import ExecutorAdapterRegistry
from tianshu.executor.adapters.protocol import PreparedExecution
from tianshu.executor.capabilities import (
    ExecutorCapabilityManifestV1,
    HostCapabilityProbeV1,
    pi_manifest,
)
from tianshu.executor.generation_controller import (
    GenerationController,
    GenerationMaterializationError,
    GenerationRecoveryError,
    GenerationWarmError,
)
from tianshu.executor.keqing import generation as generation_module
from tianshu.executor.keqing.generation import (
    PiReleaseMaterializationError,
    PiReleaseMaterializer,
)
from tianshu.models.canonical import canonical_sha256
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeReleaseV1,
)
from tianshu.storage.generation_repo import GenerationRepository

_NOW = datetime(2026, 8, 26, tzinfo=UTC)
_PI_SCOPE = "executor:keqing:pi"


def _probe() -> HostCapabilityProbeV1:
    return HostCapabilityProbeV1(
        probe_id="generation-fault-matrix",
        os_name="test",
        architecture="test",
        git_available=True,
        process_groups_available=True,
        sandbox_backend=None,
    )


@dataclass
class _Adapter:
    manifest: ExecutorCapabilityManifestV1

    @property
    def adapter_id(self) -> str:
        return self.manifest.adapter_id

    @property
    def supported_execution_modes(self) -> tuple[str, ...]:
        return self.manifest.execution_modes

    def probe(self) -> HostCapabilityProbeV1:
        return _probe()

    def prepare(
        self,
        effective: EffectiveGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: str,
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


def _release() -> RuntimeReleaseV1:
    manifest = pi_manifest()
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": _PI_SCOPE,
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
        "materializer_id": "fault-matrix-materializer",
        "materializer_version": "1",
    }
    return RuntimeReleaseV1(**material, release_digest=canonical_sha256(material))


@dataclass(frozen=True, slots=True)
class _Bundle:
    release: RuntimeReleaseV1
    executor_adapter: _Adapter

    @property
    def scope(self) -> str:
        return self.release.scope

    @property
    def adapter_id(self) -> str:
        return self.executor_adapter.adapter_id

    @property
    def release_digest(self) -> str:
        return self.release.release_digest

    @property
    def manifest_content_hash(self) -> str:
        return self.release.manifest_hash


class _Materializer:
    def materialize(self, release: RuntimeReleaseV1) -> _Bundle:
        manifest = ExecutorCapabilityManifestV1.model_validate(release.manifest)
        return _Bundle(release=release, executor_adapter=_Adapter(manifest))


type _ProbeOutcome = tuple[bool, str | None] | Exception


class _EventProbe:
    """Deterministic warm probe: every await consumes one explicit outcome."""

    def __init__(self, outcomes: list[_ProbeOutcome]) -> None:
        self._outcomes = iter(outcomes)
        self.calls: list[str] = []

    async def __call__(self, bundle: Any) -> tuple[bool, str | None]:
        self.calls.append(bundle.release_digest)
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _required_pi(_connection: Any, _memorial_id: str) -> tuple[str, ...]:
    return (_PI_SCOPE,)


def _controller(
    storage: Any,
    registry: ExecutorAdapterRegistry,
    materializer: Any,
    *,
    generation_ids: tuple[str, ...],
    probe: _EventProbe,
) -> GenerationController:
    identities = iter(generation_ids)
    return GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        materializer,
        registry,
        warm_probe=probe,
        required_scope_provider=_required_pi,
        generation_id_factory=lambda: next(identities),
        clock=lambda: _NOW,
    )


def _pointer(storage: Any):
    repository = GenerationRepository()
    with storage.unit_of_work() as unit_of_work:
        pointer = repository.get_pointer(unit_of_work.connection, scope=_PI_SCOPE)
        unit_of_work.commit()
    return pointer


def _generation(storage: Any, generation_id: str):
    repository = GenerationRepository()
    with storage.unit_of_work() as unit_of_work:
        generation = repository.get_generation(
            unit_of_work.connection,
            scope=_PI_SCOPE,
            generation_id=generation_id,
        )
        unit_of_work.commit()
    return generation


def _assert_durable_and_registry_state(
    storage: Any,
    registry: ExecutorAdapterRegistry,
    generation_id: str,
    state: RuntimeGenerationState,
) -> None:
    durable = _generation(storage, generation_id)
    retained = registry.generation_record(generation_id)
    assert durable is not None and durable.state is state
    assert retained is not None and retained.state == state.value
    assert retained.release_digest == durable.release_digest


async def test_one_hundred_switches_keep_pointer_registry_and_exact_attempt_consistent(
    storage: Any,
) -> None:
    generation_ids = tuple(f"rg-{index:032x}" for index in range(101))
    probe = _EventProbe([(True, None)] * len(generation_ids))
    registry = ExecutorAdapterRegistry()
    controller = _controller(
        storage,
        registry,
        _Materializer(),
        generation_ids=generation_ids,
        probe=probe,
    )
    reconciler = GenerationReconciler(
        GenerationRepository(),
        storage.unit_of_work,
        registry,
        clock=lambda: _NOW,
    )

    original = controller.stage(_release())
    await controller.warm(original.generation_id)
    controller.activate(original.generation_id)
    original_selection = controller.resolve_for_binding("memorial", "attempt-running")
    original_bundle = original_selection.bundles[_PI_SCOPE]
    executed_run_ids: list[str] = []

    for generation_id in generation_ids[1:]:
        candidate = controller.stage(_release())
        assert candidate.generation_id == generation_id
        await controller.warm(candidate.generation_id)

        activated = controller.activate(candidate.generation_id)
        assert activated.pointer.active_generation_id == candidate.generation_id
        assert activated.pointer.last_good_generation_id == original.generation_id
        _assert_durable_and_registry_state(
            storage,
            registry,
            candidate.generation_id,
            RuntimeGenerationState.ACTIVE,
        )
        _assert_durable_and_registry_state(
            storage,
            registry,
            original.generation_id,
            RuntimeGenerationState.DRAINING,
        )

        exact_while_draining = controller.resolve_for_binding(
            "memorial",
            "attempt-running",
            pinned_ids=original_selection.generation_ids,
        )
        assert exact_while_draining.generation_ids == (original.generation_id,)
        assert exact_while_draining.bundles[_PI_SCOPE] is original_bundle
        run_id = f"continuity-run-{len(executed_run_ids) + 1}"
        prepared = PreparedExecution(
            run_id=run_id,
            effective=cast(EffectiveGovernanceContractV1, object()),
            instruction="execute through the pinned fake delegate",
            execution_mode="single",
        )
        executed_run_ids.append(
            await exact_while_draining.bundles[_PI_SCOPE].executor_adapter.execute(prepared)
        )

        rolled_back = controller.rollback(_PI_SCOPE)
        assert rolled_back.pointer.active_generation_id == original.generation_id
        assert rolled_back.pointer.last_good_generation_id == original.generation_id
        _assert_durable_and_registry_state(
            storage,
            registry,
            original.generation_id,
            RuntimeGenerationState.ACTIVE,
        )
        _assert_durable_and_registry_state(
            storage,
            registry,
            candidate.generation_id,
            RuntimeGenerationState.DRAINING,
        )

        exact_after_rollback = controller.resolve_for_binding(
            "memorial",
            "attempt-running",
            pinned_ids=original_selection.generation_ids,
        )
        assert exact_after_rollback.generation_ids == (original.generation_id,)
        assert exact_after_rollback.bundles[_PI_SCOPE] is original_bundle

        assert reconciler.readiness_probe() is False
        assert reconciler.readiness_error_codes == ("generation_draining_pending",)
        assert reconciler.reconcile_once() == 1
        disposed = _generation(storage, candidate.generation_id)
        assert disposed is not None
        assert disposed.state is RuntimeGenerationState.DISPOSED
        assert disposed.version == 6
        assert registry.generation_record(candidate.generation_id) is None
        assert reconciler.readiness_probe() is True
        assert reconciler.readiness_error_codes == ()

    pointer = _pointer(storage)
    assert pointer is not None
    assert pointer.active_generation_id == original.generation_id
    assert pointer.last_good_generation_id == original.generation_id
    assert pointer.version == 201
    assert registry.attempt_leases() == {"attempt-running": ((_PI_SCOPE, original.generation_id),)}
    assert registry.active_attempt_count(original.generation_id) == 1
    assert len(probe.calls) == 101
    assert executed_run_ids == [f"continuity-run-{index}" for index in range(1, 101)]
    assert controller.release_binding("attempt-running") is True
    assert registry.attempt_leases() == {}
    assert all(
        registry.active_attempt_count(generation_id) == 0 for generation_id in generation_ids
    )

    repository = GenerationRepository()
    with storage.unit_of_work() as unit_of_work:
        durable = repository.list_by_scope(unit_of_work.connection, scope=_PI_SCOPE)
        retained_ids = repository.retained_generation_ids(unit_of_work.connection)
        recovery_candidates = repository.list_recovery_candidates(unit_of_work.connection)
        unit_of_work.commit()
    durable_by_id = {generation.generation_id: generation for generation in durable}
    assert len(durable_by_id) == 101
    assert durable_by_id[original.generation_id].state is RuntimeGenerationState.ACTIVE
    assert all(
        durable_by_id[generation_id].state is RuntimeGenerationState.DISPOSED
        and durable_by_id[generation_id].version == 6
        and registry.generation_record(generation_id) is None
        for generation_id in generation_ids[1:]
    )
    assert retained_ids == frozenset({original.generation_id})
    assert tuple(generation.generation_id for generation in recovery_candidates) == (
        original.generation_id,
    )
    assert reconciler.reconcile_once() == 0
    assert reconciler.last_error_code is None
    assert reconciler.readiness_probe() is True
    assert reconciler.readiness_error_codes == ()


@pytest.mark.parametrize("probe_mode", ["rejected", "exception"])
async def test_warm_probe_failure_never_moves_the_active_pointer(
    storage: Any,
    probe_mode: str,
) -> None:
    failure: _ProbeOutcome
    if probe_mode == "rejected":
        failure = (False, "bad_frame")
        expected_reason = "bad_frame"
    else:
        failure = RuntimeError("probe exploded")
        expected_reason = "probe_error:RuntimeError"
    generation_ids = ("rg-" + "a" * 32, "rg-" + "b" * 32)
    probe = _EventProbe([(True, None), failure])
    registry = ExecutorAdapterRegistry()
    controller = _controller(
        storage,
        registry,
        _Materializer(),
        generation_ids=generation_ids,
        probe=probe,
    )

    active = controller.stage(_release())
    await controller.warm(active.generation_id)
    controller.activate(active.generation_id)
    pointer_before = _pointer(storage)
    candidate = controller.stage(_release())

    with pytest.raises(GenerationWarmError, match=expected_reason) as captured:
        await controller.warm(candidate.generation_id)

    assert _pointer(storage) == pointer_before
    assert _generation(storage, candidate.generation_id).state is RuntimeGenerationState.FAILED
    assert registry.generation_record(candidate.generation_id) is None
    _assert_durable_and_registry_state(
        storage,
        registry,
        active.generation_id,
        RuntimeGenerationState.ACTIVE,
    )
    if probe_mode == "exception":
        assert isinstance(captured.value.__cause__, RuntimeError)


def _pi_install(tmp_path: Path) -> Path:
    package = tmp_path / "pi-package"
    executable = package / "bin" / "pi"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    (package / "package.json").write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.83.0",
                "bin": {"pi": "bin/pi"},
            }
        )
    )
    return executable


async def test_warm_reverifies_managed_pi_package_before_running_probe(
    storage: Any,
    tmp_path: Path,
) -> None:
    executable = _pi_install(tmp_path)
    materializer = PiReleaseMaterializer(
        release_root=tmp_path / "managed-releases",
        root=tmp_path / "runs",
    )
    generation_id = "rg-" + "f" * 32
    probe = _EventProbe([(True, None)])
    registry = ExecutorAdapterRegistry()
    controller = _controller(
        storage,
        registry,
        materializer,
        generation_ids=(generation_id,),
        probe=probe,
    )
    release = materializer.create_release(binary=str(executable))
    generation = controller.stage(release)
    managed_package = Path(release.binary_path).parent.parent / "package" / "package.json"
    managed_package.write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.83.0",
                "bin": {"pi": "bin/pi"},
                "tampered": True,
            }
        )
    )

    with pytest.raises(GenerationWarmError, match="material_verification_failed"):
        await controller.warm(generation.generation_id)

    assert probe.calls == []
    assert _generation(storage, generation_id).state is RuntimeGenerationState.FAILED
    assert registry.generation_record(generation_id) is None


async def test_warm_material_verification_runs_off_the_event_loop_thread(storage: Any) -> None:
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release_worker = threading.Event()

    class BlockingSecondMaterializer(_Materializer):
        def __init__(self) -> None:
            self.calls = 0
            self.worker_thread_id: int | None = None

        def materialize(self, release: RuntimeReleaseV1) -> _Bundle:
            self.calls += 1
            if self.calls == 2:
                self.worker_thread_id = threading.get_ident()
                loop.call_soon_threadsafe(started.set)
                if not release_worker.wait(timeout=2):
                    raise RuntimeError("test did not release warm verification")
            return super().materialize(release)

    materializer = BlockingSecondMaterializer()
    generation_id = "rg-" + "e" * 32
    controller = _controller(
        storage,
        ExecutorAdapterRegistry(),
        materializer,
        generation_ids=(generation_id,),
        probe=_EventProbe([(True, None)]),
    )
    generation = controller.stage(_release())
    event_loop_thread_id = threading.get_ident()

    warm_task = asyncio.create_task(controller.warm(generation.generation_id))
    await asyncio.wait_for(started.wait(), timeout=1)
    ticked = asyncio.Event()
    loop.call_soon(ticked.set)
    await asyncio.wait_for(ticked.wait(), timeout=1)

    assert warm_task.done() is False
    assert materializer.worker_thread_id != event_loop_thread_id
    release_worker.set()
    ready = await asyncio.wait_for(warm_task, timeout=1)
    assert ready.state is RuntimeGenerationState.READY


async def test_rollback_revalidates_last_good_managed_material_before_pointer_move(
    storage: Any,
    tmp_path: Path,
) -> None:
    executable = _pi_install(tmp_path)
    materializer = PiReleaseMaterializer(
        release_root=tmp_path / "managed-releases",
        root=tmp_path / "runs",
    )
    first_id = "rg-" + "a" * 32
    second_id = "rg-" + "b" * 32
    registry = ExecutorAdapterRegistry()
    controller = _controller(
        storage,
        registry,
        materializer,
        generation_ids=(first_id, second_id),
        probe=_EventProbe([(True, None), (True, None)]),
    )

    first_release = materializer.create_release(binary=str(executable))
    first = controller.stage(first_release)
    await controller.warm(first.generation_id)
    controller.activate(first.generation_id)

    source_package = executable.parent.parent / "package.json"
    source_package.write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.84.0",
                "bin": {"pi": "bin/pi"},
            }
        )
    )
    second_release = materializer.create_release(binary=str(executable))
    second = controller.stage(second_release)
    await controller.warm(second.generation_id)
    controller.activate(second.generation_id)
    pointer_before = _pointer(storage)

    managed_last_good_package = (
        Path(first_release.binary_path).parent.parent / "package" / "package.json"
    )
    managed_last_good_package.write_text(
        json.dumps(
            {
                "name": "@earendil-works/pi-coding-agent",
                "version": "0.83.0",
                "bin": {"pi": "bin/pi"},
                "tampered": True,
            }
        )
    )

    with pytest.raises(GenerationMaterializationError, match="last-good generation material"):
        controller.rollback(_PI_SCOPE)

    assert _pointer(storage) == pointer_before
    assert _generation(storage, first_id).state is RuntimeGenerationState.DRAINING
    assert _generation(storage, second_id).state is RuntimeGenerationState.ACTIVE
    assert registry.generation_record(first_id).state == "draining"  # type: ignore[union-attr]
    assert registry.generation_record(second_id).state == "active"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("drift", "expected_reason"),
    [
        ("binary", "binary_digest drift"),
        ("package", "package_digest drift"),
        ("materializer", "materializer_version drift"),
    ],
)
async def test_restart_fails_closed_when_retained_pi_material_drifts(
    storage: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drift: str,
    expected_reason: str,
) -> None:
    executable = _pi_install(tmp_path)
    release_root = tmp_path / "managed-releases"
    materializer = PiReleaseMaterializer(
        release_root=release_root,
        root=tmp_path / "runs",
    )
    generation_id = "rg-" + "c" * 32
    registry = ExecutorAdapterRegistry()
    controller = _controller(
        storage,
        registry,
        materializer,
        generation_ids=(generation_id,),
        probe=_EventProbe([(True, None)]),
    )
    release = materializer.create_release(binary=str(executable))
    generation = controller.stage(release)
    await controller.warm(generation.generation_id)
    controller.activate(generation.generation_id)
    pointer_before = _pointer(storage)

    if drift == "binary":
        Path(release.binary_path).resolve().write_text("#!/bin/sh\nexit 7\n")
    elif drift == "package":
        package = Path(release.binary_path).parent.parent / "package" / "package.json"
        package.write_text(
            json.dumps(
                {
                    "name": "@earendil-works/pi-coding-agent",
                    "version": "0.83.0",
                    "bin": {"pi": "bin/pi"},
                    "drift": True,
                }
            )
        )
    else:
        monkeypatch.setattr(generation_module, "PI_MATERIALIZER_VERSION", "2")

    recovered_registry = ExecutorAdapterRegistry()
    recovered = _controller(
        storage,
        recovered_registry,
        PiReleaseMaterializer(
            release_root=release_root,
            root=tmp_path / "recovered-runs",
        ),
        generation_ids=(),
        probe=_EventProbe([]),
    )

    with pytest.raises(GenerationRecoveryError) as captured:
        recovered.recover()

    cause = captured.value.__cause__
    assert isinstance(cause, PiReleaseMaterializationError)
    assert expected_reason in str(cause)
    assert _pointer(storage) == pointer_before
    assert _generation(storage, generation_id).state is RuntimeGenerationState.ACTIVE
    assert recovered_registry.generation_record(generation_id) is None


async def test_restart_preserves_retained_manifest_across_source_manifest_upgrade(
    storage: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = _pi_install(tmp_path)
    release_root = tmp_path / "managed-releases"
    materializer = PiReleaseMaterializer(
        release_root=release_root,
        root=tmp_path / "runs",
    )
    generation_id = "rg-" + "d" * 32
    controller = _controller(
        storage,
        ExecutorAdapterRegistry(),
        materializer,
        generation_ids=(generation_id,),
        probe=_EventProbe([(True, None)]),
    )
    release = materializer.create_release(binary=str(executable))
    generation = controller.stage(release)
    await controller.warm(generation.generation_id)
    controller.activate(generation.generation_id)
    pointer_before = _pointer(storage)

    changed_manifest = pi_manifest().model_copy(update={"display_name": "Upgraded Pi"})
    assert changed_manifest.content_hash != release.manifest_hash
    monkeypatch.setattr(generation_module, "pi_manifest", lambda: changed_manifest)

    recovered_registry = ExecutorAdapterRegistry()
    recovered = _controller(
        storage,
        recovered_registry,
        PiReleaseMaterializer(
            release_root=release_root,
            root=tmp_path / "recovered-runs",
        ),
        generation_ids=(),
        probe=_EventProbe([]),
    )

    report = recovered.recover()

    assert report.materialized_generation_ids == (generation_id,)
    assert _pointer(storage) == pointer_before
    retained = recovered_registry.generation_record(generation_id)
    assert retained is not None
    assert dict(retained.executor_manifest_digests)["keqing:pi"] == release.manifest_hash
