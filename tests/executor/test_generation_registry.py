from __future__ import annotations

from dataclasses import dataclass

import pytest

from tianshu.executor.adapters import (
    ExecutorAdapterRegistry,
    ExecutorGenerationConflict,
    ExecutorGenerationUnavailable,
)
from tianshu.executor.adapters.protocol import PreparedExecution
from tianshu.executor.capabilities import (
    ExecutorCapabilityManifestV1,
    HostCapabilityProbeV1,
    pi_manifest,
)
from tianshu.models.edict import Edict, EdictRuntime
from tianshu.models.governance_contract import (
    EffectiveGovernanceContractV1,
    LegacyEdictGovernanceMapper,
)

_PI_GENERATION = "rg-" + "1" * 32
_ALT_GENERATION = "rg-" + "2" * 32
_PI_SCOPE = "executor:keqing:pi"
_ALT_SCOPE = "executor:keqing:alt"
_RELEASE = "a" * 64


def _probe() -> HostCapabilityProbeV1:
    return HostCapabilityProbeV1(
        probe_id="generation-registry-test",
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
    label: str
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


def _adapter(adapter_id: str = "keqing:pi", *, label: str) -> _Adapter:
    return _Adapter(label=label, manifest=_manifest(adapter_id))


def _requested(adapter_id: str = "keqing:pi"):
    edict = Edict(goal="test", runtime=EdictRuntime(executor=adapter_id))
    return LegacyEdictGovernanceMapper.from_edict(
        edict,
        default_workspace_id="workspace-main",
    )


def _install(
    registry: ExecutorAdapterRegistry,
    adapter: _Adapter,
    *,
    generation_id: str,
    state: str = "active",
    bundle: object | None = None,
) -> object:
    materialized = bundle if bundle is not None else object()
    registry.install_generation(
        generation_id=generation_id,
        scope=f"executor:{adapter.adapter_id}",
        release_digest=_RELEASE,
        state=state,
        adapter=adapter,
        bundle=materialized,
    )
    return materialized


def test_empty_generation_selection_is_legacy_equivalent() -> None:
    static = _adapter(label="static")
    registry = ExecutorAdapterRegistry((static,))

    direct = registry.prepare(
        _requested(),
        run_id="run-direct",
        instruction="test",
        execution_mode="single",
    )
    selection = registry.reserve_binding(
        "attempt-legacy",
        pinned_ids=(),
        required_scopes=(_PI_SCOPE,),
    )
    reserved = registry.prepare(
        _requested(),
        run_id="run-reserved",
        instruction="test",
        execution_mode="single",
        attempt_id="attempt-legacy",
    )

    assert selection.generation_ids == ()
    assert selection.by_scope == {}
    assert dict(selection.executor_manifest_digests) == {"keqing:pi": static.manifest.content_hash}
    assert direct.adapter is reserved.adapter is static
    assert direct.generation_ids == reserved.generation_ids == ()
    assert direct.generation_bundle is reserved.generation_bundle is None


def test_selection_is_canonical_and_captures_manifest_and_bundle_under_one_lock() -> None:
    static_pi = _adapter(label="pi-static")
    pi = _Adapter(
        label="pi-generation",
        manifest=_manifest("keqing:pi").model_copy(update={"manifest_version": "generation-v2"}),
    )
    alt = _adapter("keqing:alt", label="alt-generation")
    native = _adapter("native", label="native-static")
    registry = ExecutorAdapterRegistry((native, static_pi))
    pi_bundle = _install(registry, pi, generation_id=_PI_GENERATION)
    alt_bundle = _install(registry, alt, generation_id=_ALT_GENERATION)

    selection = registry.reserve_binding(
        "attempt-multi",
        pinned_ids=(_PI_GENERATION, _ALT_GENERATION),
        required_scopes=(_PI_SCOPE, _ALT_SCOPE),
    )

    assert selection.generation_ids == (_ALT_GENERATION, _PI_GENERATION)
    assert dict(selection.by_scope) == {
        _ALT_SCOPE: _ALT_GENERATION,
        _PI_SCOPE: _PI_GENERATION,
    }
    assert dict(selection.bundles) == {_ALT_SCOPE: alt_bundle, _PI_SCOPE: pi_bundle}
    assert dict(selection.executor_manifest_digests) == {
        "keqing:alt": alt.manifest.content_hash,
        "keqing:pi": pi.manifest.content_hash,
        "native": native.manifest.content_hash,
    }
    assert pi.manifest.content_hash != static_pi.manifest.content_hash
    with pytest.raises(TypeError):
        selection.by_scope[_PI_SCOPE] = _ALT_GENERATION  # type: ignore[index]


def test_attempt_reservation_is_idempotent_but_rejects_a_different_selection() -> None:
    pi = _adapter(label="pi-generation")
    registry = ExecutorAdapterRegistry()
    _install(registry, pi, generation_id=_PI_GENERATION)
    other = "rg-" + "3" * 32
    _install(registry, pi, generation_id=other)

    first = registry.reserve_binding(
        "attempt-one",
        pinned_ids=(_PI_GENERATION,),
        required_scopes=(_PI_SCOPE,),
    )
    replay = registry.reserve_binding(
        "attempt-one",
        pinned_ids=(_PI_GENERATION,),
        required_scopes=(_PI_SCOPE,),
    )

    assert replay == first
    with pytest.raises(ExecutorGenerationConflict, match="already reserved"):
        registry.reserve_binding(
            "attempt-one",
            pinned_ids=(other,),
            required_scopes=(_PI_SCOPE,),
        )


def test_exact_attempt_release_prevents_aba_and_is_idempotent() -> None:
    pi = _adapter(label="pi-generation")
    registry = ExecutorAdapterRegistry()
    _install(registry, pi, generation_id=_PI_GENERATION)
    for attempt_id in ("attempt-a", "attempt-b"):
        registry.reserve_binding(
            attempt_id,
            pinned_ids=(_PI_GENERATION,),
            required_scopes=(_PI_SCOPE,),
        )

    assert registry.active_attempt_count(_PI_GENERATION) == 2
    assert registry.release("attempt-a") is True
    assert registry.release("attempt-a") is False
    assert registry.attempt_leases() == {"attempt-b": ((_PI_SCOPE, _PI_GENERATION),)}
    assert registry.active_attempt_count(_PI_GENERATION) == 1


def test_prepare_and_dag_bind_run_keep_the_reserved_generation_without_reacquiring() -> None:
    static = _adapter(label="static")
    generation = _adapter(label="generation")
    registry = ExecutorAdapterRegistry((static,))
    bundle = _install(registry, generation, generation_id=_PI_GENERATION)
    registry.reserve_binding(
        "attempt-dag",
        pinned_ids=(_PI_GENERATION,),
        required_scopes=(_PI_SCOPE,),
    )

    prepared = registry.prepare(
        _requested(),
        run_id="root",
        instruction="root instruction",
        execution_mode="single",
        attempt_id="attempt-dag",
    )
    leases_before = registry.attempt_leases()
    child = prepared.bind_run("child", instruction="child instruction")

    assert prepared.adapter is child.adapter is generation
    assert child.generation_ids == (_PI_GENERATION,)
    assert child.generation_bundle is bundle
    assert registry.attempt_leases() == leases_before
    assert registry.active_attempt_count(_PI_GENERATION) == 1


def test_ready_generation_requires_an_explicit_canary_reservation() -> None:
    pi = _adapter(label="ready")
    registry = ExecutorAdapterRegistry()
    bundle = _install(
        registry,
        pi,
        generation_id=_PI_GENERATION,
        state="ready",
    )

    with pytest.raises(ExecutorGenerationUnavailable, match="unavailable"):
        registry.reserve_binding(
            "attempt-normal",
            pinned_ids=(_PI_GENERATION,),
            required_scopes=(_PI_SCOPE,),
        )
    registry.reserve_binding(
        "attempt-canary",
        pinned_ids=(_PI_GENERATION,),
        required_scopes=(_PI_SCOPE,),
        allow_ready=True,
    )
    prepared = registry.prepare(
        _requested(),
        run_id="canary",
        instruction="test",
        execution_mode="single",
        attempt_id="attempt-canary",
    )

    assert prepared.adapter is pi
    assert prepared.generation_bundle is bundle


@pytest.mark.parametrize(
    ("pinned_ids", "required_scopes", "message"),
    (
        ((_PI_GENERATION, _PI_GENERATION), (_PI_SCOPE,), "duplicate ids"),
        ((_PI_GENERATION,), (_ALT_SCOPE,), "required scopes"),
        (("rg-" + "f" * 32,), (_PI_SCOPE,), "not materialized"),
    ),
)
def test_invalid_generation_selection_fails_closed(
    pinned_ids: tuple[str, ...],
    required_scopes: tuple[str, ...],
    message: str,
) -> None:
    pi = _adapter(label="pi-generation")
    registry = ExecutorAdapterRegistry()
    _install(registry, pi, generation_id=_PI_GENERATION)

    with pytest.raises((ExecutorGenerationConflict, ExecutorGenerationUnavailable), match=message):
        registry.reserve_binding(
            "attempt-invalid",
            pinned_ids=pinned_ids,
            required_scopes=required_scopes,
        )


def test_failed_generation_and_duplicate_scope_fail_closed() -> None:
    pi = _adapter(label="pi-generation")
    registry = ExecutorAdapterRegistry()
    _install(registry, pi, generation_id=_PI_GENERATION)
    other = "rg-" + "3" * 32
    _install(registry, pi, generation_id=other)

    with pytest.raises(ExecutorGenerationConflict, match="only disposed or failed"):
        registry.remove_generation(_PI_GENERATION)
    with pytest.raises(ExecutorGenerationConflict, match="duplicate scopes"):
        registry.reserve_binding(
            "attempt-duplicate-scope",
            pinned_ids=(_PI_GENERATION, other),
            required_scopes=(_PI_SCOPE,),
        )
    registry.update_generation_state(_PI_GENERATION, "failed")
    with pytest.raises(ExecutorGenerationUnavailable, match="failed"):
        registry.reserve_binding(
            "attempt-failed",
            pinned_ids=(_PI_GENERATION,),
            required_scopes=(_PI_SCOPE,),
        )


def test_non_pi_executor_does_not_bind_pi_when_pi_generation_exists() -> None:
    native = _adapter("native", label="native-static")
    pi = _adapter(label="pi-generation")
    registry = ExecutorAdapterRegistry((native,))
    _install(registry, pi, generation_id=_PI_GENERATION)
    selection = registry.reserve_binding(
        "attempt-native",
        pinned_ids=(),
        required_scopes=(),
    )

    prepared = registry.prepare(
        _requested("native"),
        run_id="native-run",
        instruction="test",
        execution_mode="single",
        attempt_id="attempt-native",
    )

    assert selection.generation_ids == ()
    assert prepared.adapter is native
    assert prepared.generation_bundle is None


def test_registry_reconciliation_requires_exact_identity_and_no_active_lease() -> None:
    pi = _adapter(label="pi-generation")
    registry = ExecutorAdapterRegistry()
    _install(registry, pi, generation_id=_PI_GENERATION, state="active")
    expected_manifests = {pi.adapter_id: pi.manifest.content_hash}
    registry.reserve_binding(
        "attempt-live",
        pinned_ids=(_PI_GENERATION,),
        required_scopes=(_PI_SCOPE,),
    )

    with pytest.raises(ExecutorGenerationConflict, match="identity conflicts"):
        registry.reconcile_generation_state(
            _PI_GENERATION,
            "draining",
            expected_scope=_PI_SCOPE,
            expected_release_digest="b" * 64,
            expected_manifest_digests=expected_manifests,
        )
    with pytest.raises(ExecutorGenerationConflict, match="leased"):
        registry.reconcile_generation_state(
            _PI_GENERATION,
            "draining",
            expected_scope=_PI_SCOPE,
            expected_release_digest=_RELEASE,
            expected_manifest_digests=expected_manifests,
        )

    assert registry.release("attempt-live") is True
    repaired = registry.reconcile_generation_state(
        _PI_GENERATION,
        "draining",
        expected_scope=_PI_SCOPE,
        expected_release_digest=_RELEASE,
        expected_manifest_digests=expected_manifests,
    )
    assert repaired.state == "draining"
