"""Executor manifests and governance resolution must report enforcement truth."""

from __future__ import annotations

import os
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
    pi_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.workspace_context import WorkspaceBindingError
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
    for manifest in manifests:
        assert manifest.state("pre_run_restore_point") is CapabilityState.ENFORCED
        assert manifest.state("workspace_control") is CapabilityState.BEST_EFFORT
        assert manifest.state("governed_apply_merge") is CapabilityState.ENFORCED
        assert "test_" in manifest.declaration("governed_apply_merge").evidence[0]
        assert manifest.state("durable_resume") is CapabilityState.UNSUPPORTED
        assert manifest.state("side_effect_receipts") is CapabilityState.UNSUPPORTED


def test_native_manifest_reports_named_git_receipt_limit_not_process_bypasses() -> None:
    limitations = native_manifest().limitations

    assert all("process bypass" not in limitation for limitation in limitations)
    assert any("Git" in limitation and "receipt" in limitation for limitation in limitations)


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
    requested = _requested(mandatory=("durable_resume",))

    with pytest.raises(MandatoryCapabilityMismatch) as exc_info:
        resolve_governance_contract(requested, native_manifest(), _probe())

    mismatch = exc_info.value.mismatches[0]
    assert mismatch.capability == "durable_resume"
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


def test_network_downgraded_when_backend_cannot_enforce() -> None:
    # keqing:pi 的 network_control=UNSUPPORTED(host 模式无法强制网络);敕令默认 network=deny
    # 无法被 backend enforce → 治理层降级为 unrestricted_requested,避免 gateway 死锁
    # (enforcement_unavailable);客卿须能访问自身 LLM 出口。降级透明记 degradation。
    requested = _requested("keqing:pi", advisory=("network_control",))
    assert requested.network.mode == "deny"  # 前提:默认禁网
    effective = resolve_governance_contract(requested, pi_manifest(), _probe())
    assert effective.network.mode == "unrestricted_requested"
    assert any(
        d.capability == "network_control" and "unrestricted" in d.reason
        for d in effective.degradations
    )


def test_network_not_downgraded_when_control_not_unsupported() -> None:
    # native network_control=BEST_EFFORT(非 UNSUPPORTED)→ 不降级,保留原 network 语义。
    requested = _requested(advisory=("network_control",))
    effective = resolve_governance_contract(requested, native_manifest(), _probe())
    assert effective.network.mode == requested.network.mode


def test_git_unavailable_probe_downgrades_restore_and_apply_truth(monkeypatch) -> None:
    monkeypatch.setattr(
        "tianshu.executor.capabilities.shutil.which",
        lambda _name, path=None: None,
    )

    probe = probe_host_capabilities()

    assert probe.git_available is False
    assert probe.override("pre_run_restore_point").state is CapabilityState.UNSUPPORTED
    assert probe.override("governed_apply_merge").state is CapabilityState.UNSUPPORTED
    effective = resolve_governance_contract(_requested(), native_manifest(), probe)
    assert effective.state("pre_run_restore_point") == "unsupported"
    assert effective.state("governed_apply_merge") == "unsupported"

    mandatory = _requested(mandatory=("governed_apply_merge",)).model_copy(
        update={"recovery": RecoveryPolicyV1(require_restore_point=True)}
    )
    with pytest.raises(MandatoryCapabilityMismatch) as exc_info:
        resolve_governance_contract(mandatory, native_manifest(), probe)
    assert {item.capability for item in exc_info.value.mismatches} == {
        "governed_apply_merge",
        "pre_run_restore_point",
    }


def test_host_probe_ignores_git_found_only_on_untrusted_current_path(monkeypatch) -> None:
    lookups: list[tuple[str, str | None]] = []

    def fake_which(name: str, *, path: str | None = None) -> str | None:
        lookups.append((name, path))
        if name == "git" and path is None:
            return "/tmp/untrusted-current-path/git"
        return None

    monkeypatch.setattr("tianshu.executor.capabilities.shutil.which", fake_which)

    probe = probe_host_capabilities()

    assert ("git", os.defpath) in lookups
    assert probe.git_available is False
    assert probe.override("pre_run_restore_point").state is CapabilityState.UNSUPPORTED
    assert probe.override("governed_apply_merge").state is CapabilityState.UNSUPPORTED


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
    requested = _requested(mandatory=("durable_resume",))

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


async def test_keqing_network_downgrade_survives_runtime_consistency_check() -> None:
    # 回归:keqing:pi 的 network_control=UNSUPPORTED → resolve 把 effective network 降级为
    # unrestricted_requested。DelegatingExecutorAdapter 的防篡改校验须对 mapped 施加同一降级,
    # 否则合法降级被误判为 "runtime policy does not match prepared effective contract"。
    class _Delegate:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, edict, **kwargs):
            self.calls += 1
            return None

    delegate = _Delegate()
    adapter = DelegatingExecutorAdapter(
        adapter_id="keqing:pi", manifest=pi_manifest(), delegate=delegate
    )
    registry = ExecutorAdapterRegistry((adapter,))
    requested = _requested("keqing:pi", advisory=("network_control",))
    prepared = registry.prepare(
        requested, run_id="run-1", instruction="hi", execution_mode="single"
    )
    assert prepared.effective.network.mode == "unrestricted_requested"  # 降级生效
    await prepared.execute(
        Edict(goal="hi", runtime=EdictRuntime(executor="keqing:pi")),
        memorial=SimpleNamespace(id="run-1"),
    )
    assert delegate.calls == 1  # 校验通过,委托被调用(未抛 policy mismatch)


def test_governed_policy_reports_verified_apply_capability() -> None:
    requested = _requested().model_copy(
        update={"workspace": WorkspacePolicyV1(apply_mode="governed")}
    )

    effective = resolve_governance_contract(requested, native_manifest(), _probe())

    control = next(
        item for item in effective.effective_controls if item.capability == "governed_apply_merge"
    )
    assert control.requested_mode == "unrequested"
    assert control.state == "enforced"


def test_restore_point_semantics_are_satisfied_by_production_manifest() -> None:
    requested = _requested().model_copy(
        update={"recovery": RecoveryPolicyV1(require_restore_point=True)}
    )

    effective = resolve_governance_contract(requested, native_manifest(), _probe())

    control = next(
        item for item in effective.effective_controls if item.capability == "pre_run_restore_point"
    )
    assert control.requested_mode == "mandatory"
    assert control.state == "enforced"


@pytest.mark.parametrize(
    "workspace",
    (
        WorkspacePolicyV1(staging_mode="isolated"),
        WorkspacePolicyV1(require_clean_source=True),
    ),
)
def test_workspace_isolation_does_not_promote_best_effort_control_to_mandatory(
    workspace: WorkspacePolicyV1,
) -> None:
    requested = _requested().model_copy(update={"workspace": workspace})

    effective = resolve_governance_contract(requested, native_manifest(), _probe())

    control = next(
        item for item in effective.effective_controls if item.capability == "workspace_control"
    )
    assert control.requested_mode == "unrequested"
    assert control.state == "best_effort"


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

    with pytest.raises(
        WorkspaceBindingError,
        match="requires a bound workspace lease",
    ):
        await prepared.execute(Edict(goal="test"), memorial=SimpleNamespace(id="run-1"))

    delegate.execute.assert_not_awaited()
