"""Executor manifests and governance resolution must report enforcement truth."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tianshu.executor.adapters import DelegatingExecutorAdapter, ExecutorAdapterRegistry
from tianshu.executor.adapters.protocol import PreparedExecution
from tianshu.executor.capabilities import (
    CapabilityDeclarationV1,
    CapabilityState,
    ExecutorCapabilityManifestV1,
    ExecutorLevel,
    HostCapabilityProbeV1,
    MandatoryCapabilityMismatch,
    claude_code_manifest,
    codex_manifest,
    native_manifest,
    resolve_governance_contract,
)
from tianshu.models.edict import Edict, EdictRuntime
from tianshu.models.governance_contract import (
    CAPABILITY_IDS,
    CapabilityRequirementsV1,
    EffectiveGovernanceContractV1,
    ExecutorSelectionV1,
    LegacyEdictGovernanceMapper,
    RecoveryPolicyV1,
    WorkspacePolicyV1,
)


def _requested(
    adapter_id: str = "native",
    *,
    mandatory: tuple[str, ...] = (),
    advisory: tuple[str, ...] = (),
):
    return LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="test", runtime=EdictRuntime(executor=adapter_id)),
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "capabilities": CapabilityRequirementsV1(
                mandatory=mandatory,
                advisory=advisory,
            )
        }
    )


def _probe(*overrides: CapabilityDeclarationV1) -> HostCapabilityProbeV1:
    return HostCapabilityProbeV1(
        probe_id="probe-test",
        os_name="test-os",
        architecture="test-arch",
        git_available=True,
        process_groups_available=True,
        sandbox_backend=None,
        overrides=overrides,
    )


def test_native_and_contained_manifests_declare_every_capability_once() -> None:
    manifests = (native_manifest(), claude_code_manifest(), codex_manifest())

    for manifest in manifests:
        assert {entry.capability for entry in manifest.capabilities} == set(CAPABILITY_IDS)
        assert len(manifest.capabilities) == len(CAPABILITY_IDS)
        assert len(manifest.content_hash) == 64

    assert native_manifest().level is ExecutorLevel.CONTAINED
    assert native_manifest().experimental is False
    assert claude_code_manifest().level is ExecutorLevel.CONTAINED
    assert codex_manifest().level is ExecutorLevel.CONTAINED
    assert claude_code_manifest().state("action_interception") is CapabilityState.UNSUPPORTED
    assert codex_manifest().state("durable_resume") is CapabilityState.UNSUPPORTED
    assert native_manifest().state("action_interception") is CapabilityState.BEST_EFFORT
    assert codex_manifest().state("budget_enforcement") is CapabilityState.OBSERVED


def test_manifest_rejects_missing_capabilities_and_false_managed_claim() -> None:
    with pytest.raises(ValidationError, match="exactly once"):
        ExecutorCapabilityManifestV1(
            manifest_id="broken",
            manifest_version="1",
            adapter_id="broken",
            display_name="Broken",
            level="contained",
            experimental=True,
            capabilities=(
                CapabilityDeclarationV1(
                    capability="action_interception",
                    state="unsupported",
                    evidence=("test",),
                ),
            ),
        )

    declarations = tuple(
        CapabilityDeclarationV1(
            capability=capability,
            state="best_effort" if capability == "workspace_control" else "enforced",
            evidence=("test",),
        )
        for capability in CAPABILITY_IDS
    )
    with pytest.raises(ValidationError, match="managed"):
        ExecutorCapabilityManifestV1(
            manifest_id="dishonest",
            manifest_version="1",
            adapter_id="dishonest",
            display_name="Dishonest",
            level="managed",
            experimental=False,
            capabilities=declarations,
        )


def test_mandatory_capability_requires_enforced_and_fails_closed() -> None:
    requested = _requested(mandatory=("pre_run_restore_point",))

    with pytest.raises(MandatoryCapabilityMismatch) as exc_info:
        resolve_governance_contract(requested, native_manifest(), _probe())

    mismatch = exc_info.value.mismatches[0]
    assert mismatch.capability == "pre_run_restore_point"
    assert mismatch.required_state is CapabilityState.ENFORCED
    assert mismatch.available_state is CapabilityState.UNSUPPORTED


def test_host_probe_intersection_can_only_reduce_manifest_truth() -> None:
    requested = _requested(advisory=("network_control",))
    probe = _probe(
        CapabilityDeclarationV1(
            capability="network_control",
            state="unsupported",
            evidence=("no sandbox backend",),
        )
    )

    effective = resolve_governance_contract(requested, native_manifest(), probe)

    assert isinstance(effective, EffectiveGovernanceContractV1)
    assert effective.state("network_control") == "unsupported"
    assert effective.unsupported_advisory == ("network_control",)
    assert effective.degradations[0].capability == "network_control"
    assert effective.runtime_probe_id == probe.semantic_id


def test_host_probe_semantic_id_ignores_label_but_changes_with_semantics() -> None:
    left = _probe()
    relabeled = left.model_copy(update={"probe_id": "another-label"})
    changed = left.model_copy(
        update={
            "overrides": (
                CapabilityDeclarationV1(
                    capability="workspace_control",
                    state="unsupported",
                    evidence=("changed",),
                ),
            )
        }
    )

    assert left.semantic_id == relabeled.semantic_id
    assert left.semantic_id != changed.semantic_id
    effective = resolve_governance_contract(_requested(), native_manifest(), left)
    assert effective.runtime_probe_id == left.semantic_id


def test_advisory_gap_is_visible_without_blocking_resolution() -> None:
    requested = _requested(advisory=("durable_resume", "artifact_export"))

    effective = resolve_governance_contract(requested, native_manifest(), _probe())

    assert set(effective.unsupported_advisory) == {"artifact_export", "durable_resume"}
    assert effective.requested_contract_hash == requested.content_hash
    assert (
        effective.content_hash
        == EffectiveGovernanceContractV1.model_validate_json(
            effective.model_dump_json()
        ).content_hash
    )


@dataclass
class _Adapter:
    manifest: ExecutorCapabilityManifestV1
    probe_result: HostCapabilityProbeV1
    execute_calls: int = 0
    prepared_effective: EffectiveGovernanceContractV1 | None = None

    @property
    def supported_execution_modes(self) -> tuple[str, ...]:
        return self.manifest.execution_modes

    @property
    def adapter_id(self) -> str:
        return self.manifest.adapter_id

    def probe(self) -> HostCapabilityProbeV1:
        return self.probe_result

    def prepare(
        self,
        effective: EffectiveGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: str,
    ) -> PreparedExecution:
        self.prepared_effective = effective
        return PreparedExecution(
            run_id=run_id,
            effective=effective,
            instruction=instruction,
            execution_mode=execution_mode,
        )

    async def execute(self, prepared: PreparedExecution, *args, **kwargs):
        assert prepared.effective is self.prepared_effective
        self.execute_calls += 1
        return None

    async def cancel(self, run_id: str) -> bool:
        return False


def test_registry_rejects_mismatch_before_adapter_execute() -> None:
    adapter = _Adapter(native_manifest(), _probe())
    registry = ExecutorAdapterRegistry((adapter,))
    requested = _requested(mandatory=("pre_run_restore_point",))

    with pytest.raises(MandatoryCapabilityMismatch):
        registry.prepare(
            requested,
            run_id="run-1",
            instruction="test",
            execution_mode="single",
        )

    assert adapter.execute_calls == 0


def test_registry_resolves_adapter_by_requested_id() -> None:
    adapter = _Adapter(native_manifest(), _probe())
    registry = ExecutorAdapterRegistry((adapter,))
    requested = _requested().model_copy(
        update={"executor": ExecutorSelectionV1(adapter_id="native")}
    )

    prepared = registry.prepare(
        requested,
        run_id="run-1",
        instruction="test",
        execution_mode="single",
    )

    assert prepared.adapter is adapter
    assert prepared.effective.executor.adapter_id == "native"
    assert prepared.prepared.run_id == "run-1"
    assert adapter.prepared_effective is prepared.effective


def test_registry_rejects_unsupported_execution_mode_before_prepare() -> None:
    single_manifest = native_manifest().model_copy(update={"execution_modes": ("single",)})
    adapter = _Adapter(single_manifest, _probe())
    registry = ExecutorAdapterRegistry((adapter,))

    with pytest.raises(ValueError, match="does not support execution mode 'dag'"):
        registry.prepare(
            _requested(),
            run_id="run-dag",
            instruction="test",
            execution_mode="dag",
        )

    assert adapter.prepared_effective is None


async def test_prepared_executor_passes_run_bound_effective_contract_to_adapter() -> None:
    adapter = _Adapter(native_manifest(), _probe())
    registry = ExecutorAdapterRegistry((adapter,))
    requested = _requested()
    prepared = registry.prepare(
        requested,
        run_id="run-1",
        instruction="test",
        execution_mode="single",
    )

    await prepared.execute(
        Edict(goal="test"),
        memorial=SimpleNamespace(id="run-1"),
    )

    assert adapter.execute_calls == 1


@pytest.mark.parametrize(
    ("contract_update", "required_capability"),
    [
        ({"workspace": WorkspacePolicyV1(staging_mode="isolated")}, "workspace_control"),
        ({"workspace": WorkspacePolicyV1(require_clean_source=True)}, "workspace_control"),
        ({"workspace": WorkspacePolicyV1(apply_mode="governed")}, "governed_apply_merge"),
        (
            {"recovery": RecoveryPolicyV1(require_restore_point=True)},
            "pre_run_restore_point",
        ),
    ],
)
def test_contract_semantics_implicitly_require_enforced_capabilities(
    contract_update,
    required_capability,
) -> None:
    requested = _requested().model_copy(update=contract_update)

    with pytest.raises(MandatoryCapabilityMismatch) as exc_info:
        resolve_governance_contract(requested, native_manifest(), _probe())

    assert required_capability in {item.capability for item in exc_info.value.mismatches}


def _persisted_effective():
    original = _Adapter(native_manifest(), _probe())
    return (
        ExecutorAdapterRegistry((original,))
        .prepare(
            _requested(),
            run_id="run-1",
            instruction="test",
            execution_mode="single",
        )
        .effective
    )


def test_bind_effective_rejects_manifest_drift() -> None:
    effective = _persisted_effective()
    drifted_manifest = native_manifest().model_copy(update={"manifest_version": "2"})
    with pytest.raises(ValueError, match="manifest drift"):
        ExecutorAdapterRegistry((_Adapter(drifted_manifest, _probe()),)).bind_effective(
            effective,
            run_id="run-1",
            instruction="test",
            execution_mode="single",
        )


def test_bind_effective_rejects_probe_semantic_drift() -> None:
    effective = _persisted_effective()
    drifted_probe = HostCapabilityProbeV1(
        **{
            **_probe().model_dump(),
            "overrides": (
                CapabilityDeclarationV1(
                    capability="workspace_control",
                    state="unsupported",
                    evidence=("same id, changed semantics",),
                ),
            ),
        }
    )
    with pytest.raises(ValueError, match="probe drift"):
        ExecutorAdapterRegistry((_Adapter(native_manifest(), drifted_probe),)).bind_effective(
            effective,
            run_id="run-1",
            instruction="test",
            execution_mode="single",
        )


async def test_prepared_runtime_rejects_unmaterialized_workspace_and_recovery() -> None:
    enforced = {"workspace_control", "pre_run_restore_point"}
    base_manifest = native_manifest()
    manifest = base_manifest.model_copy(
        update={
            "capabilities": tuple(
                entry.model_copy(update={"state": CapabilityState.ENFORCED})
                if entry.capability in enforced
                else entry
                for entry in base_manifest.capabilities
            )
        }
    )
    delegate = SimpleNamespace(execute=AsyncMock())
    adapter = DelegatingExecutorAdapter(
        adapter_id="native",
        manifest=manifest,
        delegate=delegate,
        probe_factory=_probe,
    )
    requested = _requested().model_copy(
        update={
            "workspace": WorkspacePolicyV1(staging_mode="isolated"),
            "recovery": RecoveryPolicyV1(require_restore_point=True),
        }
    )
    prepared = ExecutorAdapterRegistry((adapter,)).prepare(
        requested,
        run_id="run-1",
        instruction="test",
        execution_mode="single",
    )

    with pytest.raises(ValueError, match="runtime policy"):
        await prepared.execute(Edict(goal="test"), memorial=SimpleNamespace(id="run-1"))

    delegate.execute.assert_not_awaited()
