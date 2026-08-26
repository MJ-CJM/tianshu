from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import pytest

from tianshu.evolution.reconciler import GenerationReconciler
from tianshu.executor.adapters import (
    ExecutorAdapterRegistry,
    ExecutorGenerationConflict,
)
from tianshu.executor.adapters.protocol import PreparedExecution
from tianshu.executor.capabilities import (
    ExecutorCapabilityManifestV1,
    HostCapabilityProbeV1,
    pi_manifest,
)
from tianshu.executor.generation_controller import (
    GenerationController,
    GenerationControllerError,
    GenerationMaterializationError,
    GenerationWarmError,
    requested_executor_scopes,
)
from tianshu.models import Edict, Memorial
from tianshu.models.canonical import canonical_sha256
from tianshu.models.edict import EdictRuntime
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.runtime_generation import (
    RuntimeGenerationState,
    RuntimeReleaseV1,
)
from tianshu.storage.generation_repo import (
    GenerationRepository,
    GenerationRepositoryDecodeError,
)

_NOW = datetime(2026, 8, 26, tzinfo=UTC)
_PI_SCOPE = "executor:keqing:pi"
_ALT_SCOPE = "executor:keqing:alt"
_FIRST = "rg-" + "1" * 32
_SECOND = "rg-" + "2" * 32
_THIRD = "rg-" + "3" * 32


def _probe() -> HostCapabilityProbeV1:
    return HostCapabilityProbeV1(
        probe_id="generation-controller-test",
        os_name="test",
        architecture="test",
        git_available=True,
        process_groups_available=True,
        sandbox_backend=None,
    )


def _manifest(adapter_id: str) -> ExecutorCapabilityManifestV1:
    base = pi_manifest()
    if adapter_id == base.adapter_id:
        return base
    return base.model_copy(
        update={
            "adapter_id": adapter_id,
            "manifest_id": f"manifest:{adapter_id}",
            "display_name": adapter_id,
        }
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

    async def execute(self, prepared: PreparedExecution, *_args, **_kwargs):
        return prepared.run_id

    async def cancel(self, _run_id: str) -> bool:
        return False


def _release(
    adapter_id: str = "keqing:pi",
    *,
    binary_digest: str = "a" * 64,
) -> RuntimeReleaseV1:
    manifest = _manifest(adapter_id)
    material: dict[str, object] = {
        "schema_version": 1,
        "scope": f"executor:{adapter_id}",
        "manifest": manifest.model_dump(mode="json"),
        "manifest_hash": manifest.content_hash,
        "cli_version": "0.83.0",
        "cli_version_source": "package_json",
        "binary_path": f"/opt/tianshu/bin/{adapter_id.replace(':', '-')}",
        "binary_digest": binary_digest,
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
        **material,
        release_digest=canonical_sha256(material),
    )


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
    def __init__(self, storage) -> None:
        self._storage = storage
        self.calls: list[str] = []

    def materialize(self, release: RuntimeReleaseV1) -> _Bundle:
        assert self._storage._conn.in_transaction is False  # noqa: SLF001
        self.calls.append(release.release_digest)
        manifest = ExecutorCapabilityManifestV1.model_validate(release.manifest)
        return _Bundle(release=release, executor_adapter=_Adapter(manifest))


class _SwitchableMaterializer(_Materializer):
    def __init__(self, storage) -> None:
        super().__init__(storage)
        self.unavailable = False

    def materialize(self, release: RuntimeReleaseV1) -> _Bundle:
        if self.unavailable:
            raise RuntimeError("managed material drift")
        return super().materialize(release)


class _DriftingAfterValidationMaterializer(_SwitchableMaterializer):
    def __init__(self, storage) -> None:
        super().__init__(storage)
        self.drift_after_next_validation = False

    def materialize(self, release: RuntimeReleaseV1) -> _Bundle:
        bundle = super().materialize(release)
        if self.drift_after_next_validation:
            self.drift_after_next_validation = False
            self.unavailable = True
        return bundle


class _FaultingRegistry(ExecutorAdapterRegistry):
    def __init__(self, adapters=()) -> None:
        super().__init__(adapters)
        self._update_failures: dict[tuple[str, str], int] = {}

    def fail_next_update(self, generation_id: str, state: str) -> None:
        key = (generation_id, state)
        self._update_failures[key] = self._update_failures.get(key, 0) + 1

    def update_generation_state(self, generation_id: str, state: str):
        key = (generation_id, state)
        remaining = self._update_failures.get(key, 0)
        if remaining:
            self._update_failures[key] = remaining - 1
            raise ExecutorGenerationConflict("injected registry publication failure")
        return super().update_generation_state(generation_id, state)


class _IdentityPollutingRegistry(_FaultingRegistry):
    def __init__(self, adapters=()) -> None:
        super().__init__(adapters)
        self._pollute_on: tuple[str, str] | None = None

    def pollute_next_update(self, generation_id: str, state: str) -> None:
        self._pollute_on = (generation_id, state)

    def update_generation_state(self, generation_id: str, state: str):
        if self._pollute_on == (generation_id, state):
            self._pollute_on = None
            with self._lock:  # noqa: SLF001 - deterministic registry fault injection
                current = self._generation_bundles[generation_id]  # noqa: SLF001
                self._generation_bundles[generation_id] = replace(  # noqa: SLF001
                    current,
                    release_digest="f" * 64,
                )
            raise ExecutorGenerationConflict("injected registry identity pollution")
        return super().update_generation_state(generation_id, state)


class _WarmProbe:
    def __init__(self, storage, outcomes: list[tuple[bool, str | None]]) -> None:
        self._storage = storage
        self._outcomes = iter(outcomes)

    async def __call__(self, _bundle: _Bundle) -> tuple[bool, str | None]:
        assert self._storage._conn.in_transaction is False  # noqa: SLF001
        return next(self._outcomes)


def _registry(*adapter_ids: str) -> ExecutorAdapterRegistry:
    return ExecutorAdapterRegistry(_Adapter(_manifest(adapter_id)) for adapter_id in adapter_ids)


def _controller(
    storage,
    registry: ExecutorAdapterRegistry,
    materializer: _Materializer,
    *,
    ids: tuple[str, ...],
    outcomes: list[tuple[bool, str | None]],
    required_scope_provider=None,
) -> GenerationController:
    id_source = iter(ids)
    kwargs = {}
    if required_scope_provider is not None:
        kwargs["required_scope_provider"] = required_scope_provider
    return GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        materializer,
        registry,
        warm_probe=_WarmProbe(storage, outcomes),
        generation_id_factory=lambda: next(id_source),
        clock=lambda: _NOW,
        **kwargs,
    )


def _provider(*scopes: str):
    def resolve(_connection, _memorial_id: str) -> tuple[str, ...]:
        return tuple(scopes)

    return resolve


def _get_pointer(storage, scope: str):
    repository = GenerationRepository()
    with storage.unit_of_work() as unit_of_work:
        value = repository.get_pointer(unit_of_work.connection, scope=scope)
        unit_of_work.commit()
    return value


def _get_generation(storage, scope: str, generation_id: str):
    repository = GenerationRepository()
    with storage.unit_of_work() as unit_of_work:
        value = repository.get_generation(
            unit_of_work.connection,
            scope=scope,
            generation_id=generation_id,
        )
        unit_of_work.commit()
    return value


async def test_stage_exact_and_warm_or_resume_are_crash_idempotent(storage) -> None:
    registry = _registry("keqing:pi")
    controller = _controller(
        storage,
        registry,
        _Materializer(storage),
        ids=(),
        outcomes=[(True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    release = _release()

    first = controller.stage_exact(release, generation_id=_FIRST)
    replay = controller.stage_exact(release, generation_id=_FIRST)
    assert replay == first

    with storage.unit_of_work() as unit_of_work:
        warming = GenerationRepository().transition_pre_activation(
            unit_of_work.connection,
            scope=_PI_SCOPE,
            generation_id=_FIRST,
            target_state=RuntimeGenerationState.WARMING,
            expected_version=first.version,
            updated_at=_NOW,
        )
        unit_of_work.commit()
        registry.update_generation_state(_FIRST, warming.state.value)

    ready = await controller.warm_or_resume(_FIRST)
    assert ready.state is RuntimeGenerationState.READY
    assert await controller.warm_or_resume(_FIRST) == ready
    with storage.unit_of_work() as unit_of_work:
        assert (
            len(GenerationRepository().list_by_scope(unit_of_work.connection, scope=_PI_SCOPE)) == 1
        )
        unit_of_work.commit()


def test_stage_exact_rejects_generation_identity_reuse(storage) -> None:
    controller = _controller(
        storage,
        _registry("keqing:pi"),
        _Materializer(storage),
        ids=(),
        outcomes=[],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    controller.stage_exact(_release(), generation_id=_FIRST)

    with pytest.raises(GenerationControllerError, match="identity conflicts"):
        controller.stage_exact(
            _release(binary_digest="c" * 64),
            generation_id=_FIRST,
        )


async def test_controller_stage_warm_activate_and_last_good_rollback(storage) -> None:
    registry = _registry("keqing:pi")
    materializer = _Materializer(storage)
    controller = _controller(
        storage,
        registry,
        materializer,
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    release = _release()

    first = controller.stage(release)
    assert first.state is RuntimeGenerationState.STAGED
    assert (await controller.warm(first.generation_id)).state is RuntimeGenerationState.READY
    first_activation = controller.activate(first.generation_id)
    assert first_activation.pointer.active_generation_id == _FIRST
    assert first_activation.pointer.last_good_generation_id == _FIRST

    second = controller.stage(release)
    await controller.warm(second.generation_id)
    second_activation = controller.activate(second.generation_id)
    assert second_activation.pointer.active_generation_id == _SECOND
    assert second_activation.pointer.last_good_generation_id == _FIRST
    assert second_activation.draining is not None
    assert second_activation.draining.generation_id == _FIRST

    rollback = controller.rollback(_PI_SCOPE)
    assert rollback.pointer.active_generation_id == _FIRST
    assert rollback.pointer.last_good_generation_id == _FIRST
    assert rollback.draining.generation_id == _SECOND
    selection = controller.resolve_for_binding("memorial", "attempt")
    assert selection.generation_ids == (_FIRST,)
    assert selection.by_scope == {_PI_SCOPE: _FIRST}


async def test_exact_activation_and_rollback_are_replay_safe(storage) -> None:
    controller = _controller(
        storage,
        _registry("keqing:pi"),
        _Materializer(storage),
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    baseline_release = _release()
    baseline = controller.stage(baseline_release)
    await controller.warm(baseline.generation_id)
    controller.activate(baseline.generation_id)
    challenger = controller.stage(_release(binary_digest="c" * 64))
    await controller.warm(challenger.generation_id)

    with pytest.raises(GenerationControllerError, match="activation authority"):
        controller.activate_exact(
            challenger.generation_id,
            expected_active_generation_id=_THIRD,
            expected_active_release_digest=baseline_release.release_digest,
        )

    activated = controller.activate_exact(
        challenger.generation_id,
        expected_active_generation_id=baseline.generation_id,
        expected_active_release_digest=baseline_release.release_digest,
    )
    replayed_activation = controller.activate_exact(
        challenger.generation_id,
        expected_active_generation_id=baseline.generation_id,
        expected_active_release_digest=baseline_release.release_digest,
    )
    assert replayed_activation == activated

    rolled_back = controller.rollback_exact(
        _PI_SCOPE,
        expected_active_generation_id=challenger.generation_id,
        expected_last_good_generation_id=baseline.generation_id,
    )
    replayed_rollback = controller.rollback_exact(
        _PI_SCOPE,
        expected_active_generation_id=challenger.generation_id,
        expected_last_good_generation_id=baseline.generation_id,
    )
    assert replayed_rollback == rolled_back


async def test_registry_publication_retries_converge_across_generation_lifecycle(storage) -> None:
    registry = _FaultingRegistry((_Adapter(_manifest("keqing:pi")),))
    controller = _controller(
        storage,
        registry,
        _Materializer(storage),
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )

    first = controller.stage(_release())
    registry.fail_next_update(_FIRST, RuntimeGenerationState.WARMING.value)
    registry.fail_next_update(_FIRST, RuntimeGenerationState.READY.value)
    await controller.warm(first.generation_id)
    registry.fail_next_update(_FIRST, RuntimeGenerationState.ACTIVE.value)
    controller.activate(first.generation_id)

    second = controller.stage(_release(binary_digest="c" * 64))
    await controller.warm(second.generation_id)
    registry.fail_next_update(_FIRST, RuntimeGenerationState.DRAINING.value)
    registry.fail_next_update(_SECOND, RuntimeGenerationState.ACTIVE.value)
    controller.activate(second.generation_id)

    registry.fail_next_update(_SECOND, RuntimeGenerationState.DRAINING.value)
    registry.fail_next_update(_FIRST, RuntimeGenerationState.ACTIVE.value)
    controller.rollback(_PI_SCOPE)

    pointer = _get_pointer(storage, _PI_SCOPE)
    assert pointer is not None and pointer.active_generation_id == _FIRST
    assert registry.generation_record(_FIRST).state == "active"  # type: ignore[union-attr]
    assert registry.generation_record(_SECOND).state == "draining"  # type: ignore[union-attr]


async def test_reconciler_repairs_committed_activation_after_lease_and_fault_clear(
    storage,
) -> None:
    registry = _FaultingRegistry((_Adapter(_manifest("keqing:pi")),))
    controller = _controller(
        storage,
        registry,
        _Materializer(storage),
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    first = controller.stage(_release())
    await controller.warm(first.generation_id)
    controller.activate(first.generation_id)
    controller.resolve_for_binding("memorial", "attempt-live")
    second = controller.stage(_release(binary_digest="e" * 64))
    await controller.warm(second.generation_id)
    registry.fail_next_update(_FIRST, RuntimeGenerationState.DRAINING.value)
    registry.fail_next_update(_FIRST, RuntimeGenerationState.DRAINING.value)

    with pytest.raises(GenerationMaterializationError, match="did not converge"):
        controller.activate(second.generation_id)

    pointer = _get_pointer(storage, _PI_SCOPE)
    assert pointer is not None and pointer.active_generation_id == _SECOND
    assert _get_generation(storage, _PI_SCOPE, _FIRST).state is RuntimeGenerationState.DRAINING
    assert _get_generation(storage, _PI_SCOPE, _SECOND).state is RuntimeGenerationState.ACTIVE
    assert registry.generation_record(_FIRST).state == "active"  # type: ignore[union-attr]
    assert registry.generation_record(_SECOND).state == "ready"  # type: ignore[union-attr]

    reconciler = GenerationReconciler(
        GenerationRepository(),
        storage.unit_of_work,
        registry,
        clock=lambda: _NOW,
    )
    assert reconciler.readiness_probe() is False
    assert reconciler.reconcile_once() == 0
    assert reconciler.last_error_code == "generation_reconciliation_registry_conflict"
    assert reconciler.readiness_probe() is False

    assert controller.release_binding("attempt-live") is True
    assert reconciler.reconcile_once() == 0
    assert reconciler.last_error_code is None
    assert reconciler.readiness_probe() is True
    assert registry.generation_record(_FIRST).state == "draining"  # type: ignore[union-attr]
    assert registry.generation_record(_SECOND).state == "active"  # type: ignore[union-attr]


async def test_publish_repair_rejects_registry_identity_pollution(storage) -> None:
    registry = _IdentityPollutingRegistry((_Adapter(_manifest("keqing:pi")),))
    controller = _controller(
        storage,
        registry,
        _Materializer(storage),
        ids=(_FIRST,),
        outcomes=[(True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    generation = controller.stage(_release())
    await controller.warm(generation.generation_id)
    registry.pollute_next_update(_FIRST, RuntimeGenerationState.ACTIVE.value)

    with pytest.raises(GenerationMaterializationError, match="did not converge") as captured:
        controller.activate(generation.generation_id)

    assert isinstance(captured.value.__cause__, ExecutorGenerationConflict)
    pointer = _get_pointer(storage, _PI_SCOPE)
    assert pointer is not None and pointer.active_generation_id == _FIRST
    durable = _get_generation(storage, _PI_SCOPE, _FIRST)
    retained = registry.generation_record(_FIRST)
    assert durable is not None and durable.state is RuntimeGenerationState.ACTIVE
    assert retained is not None and retained.release_digest != durable.release_digest


async def test_activate_revalidates_ready_material_before_moving_pointer(storage) -> None:
    registry = _registry("keqing:pi")
    materializer = _SwitchableMaterializer(storage)
    controller = _controller(
        storage,
        registry,
        materializer,
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    first = controller.stage(_release())
    await controller.warm(first.generation_id)
    controller.activate(first.generation_id)
    second = controller.stage(_release(binary_digest="d" * 64))
    await controller.warm(second.generation_id)
    pointer_before = _get_pointer(storage, _PI_SCOPE)
    materializer.unavailable = True

    with pytest.raises(GenerationMaterializationError, match="activation generation material"):
        controller.activate(second.generation_id)

    assert _get_pointer(storage, _PI_SCOPE) == pointer_before
    assert _get_generation(storage, _PI_SCOPE, _FIRST).state is RuntimeGenerationState.ACTIVE
    assert _get_generation(storage, _PI_SCOPE, _SECOND).state is RuntimeGenerationState.READY
    assert registry.generation_record(_FIRST).state == "active"  # type: ignore[union-attr]
    assert registry.generation_record(_SECOND).state == "ready"  # type: ignore[union-attr]


async def test_rollback_rechecks_material_after_first_validation_returns(storage) -> None:
    registry = _registry("keqing:pi")
    materializer = _DriftingAfterValidationMaterializer(storage)
    controller = _controller(
        storage,
        registry,
        materializer,
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    first = controller.stage(_release())
    await controller.warm(first.generation_id)
    controller.activate(first.generation_id)
    second = controller.stage(_release(binary_digest="d" * 64))
    await controller.warm(second.generation_id)
    controller.activate(second.generation_id)
    pointer_before = _get_pointer(storage, _PI_SCOPE)
    materializer.drift_after_next_validation = True

    with pytest.raises(GenerationMaterializationError, match="last-good generation material"):
        controller.rollback(_PI_SCOPE)

    assert _get_pointer(storage, _PI_SCOPE) == pointer_before
    assert _get_generation(storage, _PI_SCOPE, _FIRST).state is RuntimeGenerationState.DRAINING
    assert _get_generation(storage, _PI_SCOPE, _SECOND).state is RuntimeGenerationState.ACTIVE


async def test_same_process_recovery_repairs_matching_registry_state(storage) -> None:
    registry = _registry("keqing:pi")
    controller = _controller(
        storage,
        registry,
        _Materializer(storage),
        ids=(_FIRST,),
        outcomes=[(True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    generation = controller.stage(_release())
    await controller.warm(generation.generation_id)
    controller.activate(generation.generation_id)
    registry.update_generation_state(generation.generation_id, "ready")

    report = controller.recover()

    assert report.materialized_generation_ids == (generation.generation_id,)
    retained = registry.generation_record(generation.generation_id)
    assert retained is not None and retained.state == "active"


async def test_scope_status_is_read_only_and_counts_exact_active_leases(storage) -> None:
    registry = _registry("keqing:pi")
    controller = _controller(
        storage,
        registry,
        _Materializer(storage),
        ids=(_FIRST,),
        outcomes=[(True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )

    assert controller.status_for_scope(_PI_SCOPE) is None
    generation = controller.stage(_release())
    await controller.warm(generation.generation_id)
    controller.activate(generation.generation_id)

    idle = controller.status_for_scope(_PI_SCOPE)
    assert idle is not None
    assert idle.id == _FIRST
    assert idle.state is RuntimeGenerationState.ACTIVE
    assert idle.active_runs == 0
    assert idle.last_good_id == _FIRST

    controller.resolve_for_binding("memorial", "attempt-status")
    active = controller.status_for_scope(_PI_SCOPE)
    assert active is not None and active.active_runs == 1
    assert controller.release_binding("attempt-status") is True
    released = controller.status_for_scope(_PI_SCOPE)
    assert released is not None and released.active_runs == 0


async def test_warm_failure_is_durable_and_does_not_move_active_pointer(storage) -> None:
    registry = _registry("keqing:pi")
    materializer = _Materializer(storage)
    controller = _controller(
        storage,
        registry,
        materializer,
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (False, "bad_frame")],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    release = _release()
    first = controller.stage(release)
    await controller.warm(first.generation_id)
    controller.activate(first.generation_id)
    second = controller.stage(release)

    with pytest.raises(GenerationWarmError, match="bad_frame"):
        await controller.warm(second.generation_id)

    pointer = _get_pointer(storage, _PI_SCOPE)
    assert pointer is not None
    assert pointer.active_generation_id == _FIRST
    failed = _get_generation(storage, _PI_SCOPE, _SECOND)
    assert failed is not None and failed.state is RuntimeGenerationState.FAILED
    assert registry.generation_record(_SECOND) is None


async def test_current_uow_binding_uses_requested_adapter_and_never_nests(storage) -> None:
    registry = _registry("native", "keqing:pi")
    materializer = _Materializer(storage)
    controller = _controller(
        storage,
        registry,
        materializer,
        ids=(_FIRST,),
        outcomes=[(True, None)],
    )
    generation = controller.stage(_release())
    await controller.warm(generation.generation_id)
    controller.activate(generation.generation_id)

    pi_edict = Edict(goal="pi", runtime=EdictRuntime(executor="keqing:pi"))
    native_edict = Edict(goal="native", runtime=EdictRuntime(executor="native"))
    storage.save_edict(pi_edict)
    storage.save_edict(native_edict)
    pi_memorial = Memorial(edict_id=pi_edict.id)
    native_memorial = Memorial(edict_id=native_edict.id)
    pi_to_native = Memorial(
        edict_id=pi_edict.id,
        runtime_override={"executor": "native"},
    )
    native_to_pi = Memorial(
        edict_id=native_edict.id,
        runtime_override={"executor": "keqing:pi"},
    )
    storage.save_memorial(pi_memorial)
    storage.save_memorial(native_memorial)
    storage.save_memorial(pi_to_native)
    storage.save_memorial(native_to_pi)

    with storage.unit_of_work() as unit_of_work:
        pi_selection = controller.resolve_for_binding_current(
            unit_of_work.connection,
            pi_memorial.id,
            "attempt-pi",
        )
        native_selection = controller.resolve_for_binding_current(
            unit_of_work.connection,
            native_memorial.id,
            "attempt-native",
        )
        pi_to_native_selection = controller.resolve_for_binding_current(
            unit_of_work.connection,
            pi_to_native.id,
            "attempt-pi-to-native",
            pinned_ids=(_FIRST,),
            inherit_pinned=True,
        )
        native_to_pi_selection = controller.resolve_for_binding_current(
            unit_of_work.connection,
            native_to_pi.id,
            "attempt-native-to-pi",
        )
        unit_of_work.commit()

    assert pi_selection.generation_ids == (_FIRST,)
    assert native_selection.generation_ids == ()
    assert pi_to_native_selection.generation_ids == ()
    assert native_to_pi_selection.generation_ids == (_FIRST,)
    assert set(native_selection.executor_manifest_digests) == {"keqing:pi", "native"}


def test_requested_scope_provider_rejects_corrupt_contract_or_runtime_override(storage) -> None:
    edict = Edict(goal="pi", runtime=EdictRuntime(executor="keqing:pi"))
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, runtime_override={"executor": "native"})
    storage.save_memorial(memorial)

    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            "UPDATE memorials SET runtime_override_json = '[]' WHERE id = ?",
            (memorial.id,),
        )
        with pytest.raises(ValueError, match="must be an object"):
            requested_executor_scopes(unit_of_work.connection, memorial.id)

    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            "UPDATE requested_governance_contracts SET contract_hash = ? WHERE edict_id = ?",
            ("f" * 64, edict.id),
        )
        with pytest.raises(ValueError, match="hash mismatch"):
            requested_executor_scopes(unit_of_work.connection, memorial.id)


async def test_controller_selects_multiple_required_scopes_canonically(storage) -> None:
    registry = _registry("keqing:pi", "keqing:alt")
    materializer = _Materializer(storage)
    controller = _controller(
        storage,
        registry,
        materializer,
        ids=(_FIRST, _SECOND),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE, _ALT_SCOPE),
    )
    pi = controller.stage(_release("keqing:pi"))
    await controller.warm(pi.generation_id)
    controller.activate(pi.generation_id)
    alt = controller.stage(_release("keqing:alt"))
    await controller.warm(alt.generation_id)
    controller.activate(alt.generation_id)

    selection = controller.resolve_for_binding("memorial", "attempt-multi")

    assert selection.generation_ids == (_SECOND, _FIRST)
    assert dict(selection.by_scope) == {_ALT_SCOPE: _SECOND, _PI_SCOPE: _FIRST}
    assert set(selection.executor_manifest_digests) == {"keqing:alt", "keqing:pi"}


async def test_restart_recovery_fails_pre_active_and_rehydrates_active_and_draining(
    storage,
) -> None:
    release = _release()
    old_registry = _registry("keqing:pi")
    old_controller = _controller(
        storage,
        old_registry,
        _Materializer(storage),
        ids=(_FIRST, _SECOND, _THIRD),
        outcomes=[(True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    first = old_controller.stage(release)
    await old_controller.warm(first.generation_id)
    old_controller.activate(first.generation_id)
    second = old_controller.stage(release)
    await old_controller.warm(second.generation_id)
    old_controller.activate(second.generation_id)
    old_controller.stage(release)

    recovered_registry = _registry("keqing:pi")
    recovered_materializer = _Materializer(storage)
    recovered_controller = _controller(
        storage,
        recovered_registry,
        recovered_materializer,
        ids=(),
        outcomes=[],
        required_scope_provider=_provider(_PI_SCOPE),
    )

    report = recovered_controller.recover()

    assert report.materialized_generation_ids == (_FIRST, _SECOND)
    assert report.failed_generation_ids == (_THIRD,)
    assert len(recovered_materializer.calls) == 2
    assert recovered_registry.generation_record(_FIRST).state == "draining"  # type: ignore[union-attr]
    assert recovered_registry.generation_record(_SECOND).state == "active"  # type: ignore[union-attr]
    abandoned = _get_generation(storage, _PI_SCOPE, _THIRD)
    assert abandoned is not None and abandoned.state is RuntimeGenerationState.FAILED
    selection = recovered_controller.resolve_for_binding("memorial", "attempt-recovered")
    assert selection.generation_ids == (_SECOND,)


@pytest.mark.parametrize(
    "abandoned_state",
    [
        RuntimeGenerationState.STAGED,
        RuntimeGenerationState.WARMING,
        RuntimeGenerationState.READY,
    ],
)
async def test_restart_recovery_fails_every_abandoned_pre_active_state(
    storage,
    abandoned_state: RuntimeGenerationState,
) -> None:
    old_controller = _controller(
        storage,
        _registry("keqing:pi"),
        _Materializer(storage),
        ids=(_THIRD,),
        outcomes=[],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    staged = old_controller.stage(_release())
    repository = GenerationRepository()
    with storage.unit_of_work() as unit_of_work:
        current = repository.get_generation(
            unit_of_work.connection,
            scope=_PI_SCOPE,
            generation_id=staged.generation_id,
        )
        assert current is not None
        if abandoned_state in {
            RuntimeGenerationState.WARMING,
            RuntimeGenerationState.READY,
        }:
            current = repository.transition_pre_activation(
                unit_of_work.connection,
                scope=_PI_SCOPE,
                generation_id=current.generation_id,
                target_state=RuntimeGenerationState.WARMING,
                expected_version=current.version,
                updated_at=_NOW,
            )
        if abandoned_state is RuntimeGenerationState.READY:
            current = repository.transition_pre_activation(
                unit_of_work.connection,
                scope=_PI_SCOPE,
                generation_id=current.generation_id,
                target_state=RuntimeGenerationState.READY,
                expected_version=current.version,
                updated_at=_NOW,
            )
        unit_of_work.commit()

    recovered_registry = _registry("keqing:pi")
    recovered = _controller(
        storage,
        recovered_registry,
        _Materializer(storage),
        ids=(),
        outcomes=[],
        required_scope_provider=_provider(_PI_SCOPE),
    )

    report = recovered.recover()

    assert report.materialized_generation_ids == ()
    assert report.failed_generation_ids == (_THIRD,)
    failed = _get_generation(storage, _PI_SCOPE, _THIRD)
    assert failed is not None and failed.state is RuntimeGenerationState.FAILED
    assert recovered_registry.generation_record(_THIRD) is None


async def test_restart_and_readiness_reject_generation_table_tamper_before_publish(
    storage,
) -> None:
    controller = _controller(
        storage,
        _registry("keqing:pi"),
        _Materializer(storage),
        ids=(_FIRST,),
        outcomes=[(True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    generation = controller.stage(_release())
    await controller.warm(generation.generation_id)
    controller.activate(generation.generation_id)
    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            "UPDATE runtime_generations SET version = 99 WHERE generation_id = ?",
            (generation.generation_id,),
        )
        unit_of_work.commit()

    recovered_registry = _registry("keqing:pi")
    recovered_materializer = _Materializer(storage)
    recovered = _controller(
        storage,
        recovered_registry,
        recovered_materializer,
        ids=(),
        outcomes=[],
        required_scope_provider=_provider(_PI_SCOPE),
    )

    with pytest.raises(GenerationRepositoryDecodeError, match="journal tail"):
        recovered.recover()

    assert recovered_materializer.calls == []
    assert recovered_registry.generation_records() == ()
    reconciler = GenerationReconciler(
        GenerationRepository(),
        storage.unit_of_work,
        recovered_registry,
        clock=lambda: _NOW,
    )
    assert reconciler.readiness_probe() is False
    assert reconciler.readiness_error_codes == ("generation_readiness_probe_failed",)


async def test_cancelled_warm_probe_durably_fails_and_unpublishes_generation(storage) -> None:
    registry = _registry("keqing:pi")
    started = asyncio.Event()
    blocked = asyncio.Event()

    async def cancelled_probe(_bundle: _Bundle) -> tuple[bool, str | None]:
        started.set()
        await blocked.wait()
        return True, None

    controller = GenerationController(
        GenerationRepository(),
        storage.unit_of_work,
        _Materializer(storage),
        registry,
        warm_probe=cancelled_probe,
        required_scope_provider=_provider(_PI_SCOPE),
        generation_id_factory=lambda: _FIRST,
        clock=lambda: _NOW,
    )
    staged = controller.stage(_release())
    task = asyncio.create_task(controller.warm(staged.generation_id))
    await asyncio.wait_for(started.wait(), timeout=1)
    warming = _get_generation(storage, _PI_SCOPE, staged.generation_id)
    assert warming is not None and warming.state is RuntimeGenerationState.WARMING

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    failed = _get_generation(storage, _PI_SCOPE, staged.generation_id)
    assert failed is not None and failed.state is RuntimeGenerationState.FAILED
    assert registry.generation_record(staged.generation_id) is None


async def test_restart_skips_unreferenced_draining_release_material(storage) -> None:
    old_registry = _registry("keqing:pi")
    old_materializer = _Materializer(storage)
    old_controller = _controller(
        storage,
        old_registry,
        old_materializer,
        ids=(_FIRST, _SECOND, _THIRD),
        outcomes=[(True, None), (True, None), (True, None)],
        required_scope_provider=_provider(_PI_SCOPE),
    )
    for digest in ("1" * 64, "2" * 64, "3" * 64):
        generation = old_controller.stage(_release(binary_digest=digest))
        await old_controller.warm(generation.generation_id)
        old_controller.activate(generation.generation_id)

    recovered_registry = _registry("keqing:pi")
    recovered_materializer = _Materializer(storage)
    recovered_controller = _controller(
        storage,
        recovered_registry,
        recovered_materializer,
        ids=(),
        outcomes=[],
        required_scope_provider=_provider(_PI_SCOPE),
    )

    report = recovered_controller.recover()

    assert report.materialized_generation_ids == (_SECOND, _THIRD)
    assert recovered_materializer.calls == [
        _release(binary_digest="2" * 64).release_digest,
        _release(binary_digest="3" * 64).release_digest,
    ]
    assert recovered_registry.generation_record(_FIRST) is None
    assert recovered_registry.generation_record(_SECOND) is not None
    assert recovered_registry.generation_record(_THIRD) is not None
