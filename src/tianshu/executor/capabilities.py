"""Truthful executor capability manifests and contract resolution."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from tianshu.models.governance_contract import (
    CAPABILITY_IDS,
    CanonicalContractModel,
    CapabilityDegradationV1,
    CapabilityId,
    CapabilityResolutionV1,
    EffectiveGovernanceContractV1,
    RequestedGovernanceContractV1,
)

type ExecutionMode = Literal["single", "dag", "outer_loop"]


class CapabilityState(StrEnum):
    ENFORCED = "enforced"
    BEST_EFFORT = "best_effort"
    OBSERVED = "observed"
    UNSUPPORTED = "unsupported"


class ExecutorLevel(StrEnum):
    MANAGED = "managed"
    CONTAINED = "contained"
    OBSERVE_ONLY = "observe-only"


class CapabilityDeclarationV1(CanonicalContractModel):
    capability: CapabilityId
    state: CapabilityState
    evidence: tuple[str, ...] = ()

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_evidence(cls, values: Any) -> tuple[str, ...]:
        return tuple(sorted({str(value).strip() for value in values or () if str(value).strip()}))


class ExecutorCapabilityManifestV1(CanonicalContractModel):
    manifest_id: str = Field(min_length=1)
    manifest_version: str = Field(min_length=1)
    adapter_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    level: ExecutorLevel
    experimental: bool
    execution_modes: tuple[ExecutionMode, ...] = ("single",)
    capabilities: tuple[CapabilityDeclarationV1, ...]
    limitations: tuple[str, ...] = ()

    @field_validator("capabilities", mode="after")
    @classmethod
    def normalize_capabilities(
        cls, values: tuple[CapabilityDeclarationV1, ...]
    ) -> tuple[CapabilityDeclarationV1, ...]:
        return tuple(sorted(values, key=lambda value: value.capability))

    @field_validator("execution_modes", mode="before")
    @classmethod
    def normalize_execution_modes(cls, values: Any) -> tuple[str, ...]:
        order = {"single": 0, "dag": 1, "outer_loop": 2}
        return tuple(sorted(set(values or ()), key=order.__getitem__))

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        keys = [entry.capability for entry in self.capabilities]
        if set(keys) != set(CAPABILITY_IDS) or len(keys) != len(CAPABILITY_IDS):
            raise ValueError("manifest must declare every capability exactly once")
        if self.level is ExecutorLevel.MANAGED and any(
            entry.state is not CapabilityState.ENFORCED for entry in self.capabilities
        ):
            raise ValueError("managed executor must enforce every managed-v1 capability")
        if self.level is ExecutorLevel.OBSERVE_ONLY and any(
            entry.state is CapabilityState.ENFORCED for entry in self.capabilities
        ):
            raise ValueError("observe-only executor cannot claim enforced controls")
        if not self.execution_modes:
            raise ValueError("executor manifest must support at least one execution mode")
        return self

    def state(self, capability: CapabilityId) -> CapabilityState:
        return next(entry.state for entry in self.capabilities if entry.capability == capability)

    def declaration(self, capability: CapabilityId) -> CapabilityDeclarationV1:
        return next(entry for entry in self.capabilities if entry.capability == capability)


class HostCapabilityProbeV1(CanonicalContractModel):
    probe_id: str = Field(min_length=1)
    os_name: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    git_available: bool
    process_groups_available: bool
    sandbox_backend: str | None
    overrides: tuple[CapabilityDeclarationV1, ...] = ()

    @field_validator("overrides", mode="after")
    @classmethod
    def validate_overrides(
        cls, values: tuple[CapabilityDeclarationV1, ...]
    ) -> tuple[CapabilityDeclarationV1, ...]:
        keys = [value.capability for value in values]
        if len(keys) != len(set(keys)):
            raise ValueError("host probe capability overrides must be unique")
        return tuple(sorted(values, key=lambda value: value.capability))

    def override(self, capability: CapabilityId) -> CapabilityDeclarationV1 | None:
        return next(
            (entry for entry in self.overrides if entry.capability == capability),
            None,
        )

    @property
    def semantic_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"probe_id"})
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def semantic_hash(self) -> str:
        return self.semantic_id


class CapabilityMismatchV1(CanonicalContractModel):
    capability: CapabilityId
    required_state: CapabilityState = CapabilityState.ENFORCED
    available_state: CapabilityState
    manifest_id: str
    reason: str


class MandatoryCapabilityMismatch(RuntimeError):
    def __init__(self, mismatches: tuple[CapabilityMismatchV1, ...]) -> None:
        self.mismatches = mismatches
        detail = ", ".join(f"{item.capability}={item.available_state.value}" for item in mismatches)
        super().__init__(f"mandatory executor capabilities are not enforced: {detail}")


_STATE_RANK = {
    CapabilityState.UNSUPPORTED: 0,
    CapabilityState.OBSERVED: 1,
    CapabilityState.BEST_EFFORT: 2,
    CapabilityState.ENFORCED: 3,
}


def _lower_state(left: CapabilityState, right: CapabilityState) -> CapabilityState:
    return left if _STATE_RANK[left] <= _STATE_RANK[right] else right


def _semantic_mandatory_capabilities(
    requested: RequestedGovernanceContractV1,
) -> set[CapabilityId]:
    required: set[CapabilityId] = set()
    if requested.workspace.staging_mode == "isolated" or requested.workspace.require_clean_source:
        required.add("workspace_control")
    if requested.workspace.apply_mode == "governed":
        required.add("governed_apply_merge")
    if requested.recovery.require_restore_point:
        required.add("pre_run_restore_point")
    return required


def resolve_governance_contract(
    requested: RequestedGovernanceContractV1,
    manifest: ExecutorCapabilityManifestV1,
    probe: HostCapabilityProbeV1,
) -> EffectiveGovernanceContractV1:
    if requested.executor.adapter_id != manifest.adapter_id:
        raise ValueError(
            f"requested executor {requested.executor.adapter_id!r} does not match "
            f"manifest {manifest.adapter_id!r}"
        )

    controls: list[CapabilityResolutionV1] = []
    mismatches: list[CapabilityMismatchV1] = []
    advisory_gaps: list[CapabilityId] = []
    degradations: list[CapabilityDegradationV1] = []
    mandatory = set(requested.capabilities.mandatory) | _semantic_mandatory_capabilities(requested)
    advisory = set(requested.capabilities.advisory)

    for capability in CAPABILITY_IDS:
        declaration = manifest.declaration(capability)
        host_override = probe.override(capability)
        state = (
            _lower_state(declaration.state, host_override.state)
            if host_override is not None
            else declaration.state
        )
        evidence = declaration.evidence + (host_override.evidence if host_override else ())
        requested_mode: Literal["mandatory", "advisory", "unrequested"] = "unrequested"
        if capability in mandatory:
            requested_mode = "mandatory"
            if state is not CapabilityState.ENFORCED:
                mismatches.append(
                    CapabilityMismatchV1(
                        capability=capability,
                        available_state=state,
                        manifest_id=manifest.manifest_id,
                        reason="mandatory capabilities accept only enforced controls",
                    )
                )
        elif capability in advisory:
            requested_mode = "advisory"
            if state is not CapabilityState.ENFORCED:
                advisory_gaps.append(capability)
        if host_override is not None and state is not declaration.state:
            degradations.append(
                CapabilityDegradationV1(
                    capability=capability,
                    manifest_state=declaration.state.value,
                    effective_state=state.value,
                    reason="host probe reduced the adapter capability",
                )
            )
        controls.append(
            CapabilityResolutionV1(
                capability=capability,
                requested_mode=requested_mode,
                state=state.value,
                evidence=evidence,
            )
        )

    if mismatches:
        raise MandatoryCapabilityMismatch(tuple(mismatches))

    return EffectiveGovernanceContractV1(
        requested_contract_hash=requested.content_hash,
        objective=requested.objective,
        acceptance=requested.acceptance,
        executor=requested.executor,
        permissions=requested.permissions,
        network=requested.network,
        workspace=requested.workspace,
        budget=requested.budget,
        recovery=requested.recovery,
        executor_manifest_id=manifest.manifest_id,
        executor_manifest_version=manifest.manifest_version,
        executor_manifest_hash=manifest.content_hash,
        runtime_probe_id=probe.semantic_id,
        effective_controls=tuple(controls),
        unsupported_advisory=tuple(advisory_gaps),
        degradations=tuple(degradations),
        resolved_source_id=requested.workspace.source_id,
        resolved_base_revision=requested.workspace.base_revision,
    )


def _declarations(
    states: dict[CapabilityId, CapabilityState],
    *,
    evidence_prefix: str,
) -> tuple[CapabilityDeclarationV1, ...]:
    return tuple(
        CapabilityDeclarationV1(
            capability=capability,
            state=states[capability],
            evidence=(f"{evidence_prefix}:{capability}",),
        )
        for capability in CAPABILITY_IDS
    )


def native_manifest() -> ExecutorCapabilityManifestV1:
    states: dict[CapabilityId, CapabilityState] = {
        "action_interception": CapabilityState.BEST_EFFORT,
        "workspace_control": CapabilityState.BEST_EFFORT,
        "network_control": CapabilityState.BEST_EFFORT,
        "secret_control": CapabilityState.BEST_EFFORT,
        "budget_enforcement": CapabilityState.BEST_EFFORT,
        "decision_bridge": CapabilityState.ENFORCED,
        "pause": CapabilityState.BEST_EFFORT,
        "durable_resume": CapabilityState.UNSUPPORTED,
        "event_fidelity": CapabilityState.BEST_EFFORT,
        "artifact_export": CapabilityState.OBSERVED,
        "side_effect_receipts": CapabilityState.UNSUPPORTED,
        "pre_run_restore_point": CapabilityState.UNSUPPORTED,
        "governed_apply_merge": CapabilityState.UNSUPPORTED,
    }
    return ExecutorCapabilityManifestV1(
        manifest_id="tianshu.native.v1",
        manifest_version="1",
        adapter_id="native",
        display_name="Tianshu Native",
        level=ExecutorLevel.CONTAINED,
        experimental=False,
        execution_modes=("single", "dag", "outer_loop"),
        capabilities=_declarations(states, evidence_prefix="native-current"),
        limitations=(
            "shared workspace until G1 workspace isolation",
            "external process bypasses remain until ExecutionGateway",
            "no durable resume or side-effect receipts",
        ),
    )


def _keqing_manifest(
    adapter_id: str,
    display_name: str,
    *,
    budget_state: CapabilityState,
) -> ExecutorCapabilityManifestV1:
    states: dict[CapabilityId, CapabilityState] = {
        "action_interception": CapabilityState.UNSUPPORTED,
        "workspace_control": CapabilityState.BEST_EFFORT,
        "network_control": CapabilityState.UNSUPPORTED,
        "secret_control": CapabilityState.BEST_EFFORT,
        "budget_enforcement": budget_state,
        "decision_bridge": CapabilityState.UNSUPPORTED,
        "pause": CapabilityState.UNSUPPORTED,
        "durable_resume": CapabilityState.UNSUPPORTED,
        "event_fidelity": CapabilityState.OBSERVED,
        "artifact_export": CapabilityState.OBSERVED,
        "side_effect_receipts": CapabilityState.UNSUPPORTED,
        "pre_run_restore_point": CapabilityState.UNSUPPORTED,
        "governed_apply_merge": CapabilityState.UNSUPPORTED,
    }
    return ExecutorCapabilityManifestV1(
        manifest_id=f"tianshu.{adapter_id.replace(':', '.')}.v1",
        manifest_version="1",
        adapter_id=adapter_id,
        display_name=display_name,
        level=ExecutorLevel.CONTAINED,
        experimental=True,
        capabilities=_declarations(states, evidence_prefix="opaque-cli-current"),
        limitations=(
            "opaque CLI actions are observed rather than intercepted",
            "workspace and budget controls have escape or overshoot windows",
            "no durable resume, receipts, pre-run restore point, or governed apply",
        ),
    )


def claude_code_manifest() -> ExecutorCapabilityManifestV1:
    return _keqing_manifest(
        "keqing:claude-code",
        "Claude Code CLI",
        budget_state=CapabilityState.BEST_EFFORT,
    )


def codex_manifest() -> ExecutorCapabilityManifestV1:
    return _keqing_manifest(
        "keqing:codex",
        "Codex CLI",
        budget_state=CapabilityState.OBSERVED,
    )


def default_executor_manifests() -> tuple[ExecutorCapabilityManifestV1, ...]:
    return native_manifest(), claude_code_manifest(), codex_manifest()


def get_executor_manifest(adapter_id: str) -> ExecutorCapabilityManifestV1:
    manifests = {manifest.adapter_id: manifest for manifest in default_executor_manifests()}
    try:
        return manifests[adapter_id]
    except KeyError as exc:
        raise KeyError(f"unknown executor adapter: {adapter_id}") from exc


def probe_host_capabilities() -> HostCapabilityProbeV1:
    git_available = shutil.which("git") is not None
    sandbox = next(
        (binary for binary in ("docker", "podman") if shutil.which(binary) is not None),
        None,
    )
    overrides = (
        CapabilityDeclarationV1(
            capability="workspace_control",
            state=(CapabilityState.BEST_EFFORT if git_available else CapabilityState.UNSUPPORTED),
            evidence=("git executable available" if git_available else "git unavailable",),
        ),
        CapabilityDeclarationV1(
            capability="network_control",
            state=(
                CapabilityState.BEST_EFFORT if sandbox is not None else CapabilityState.UNSUPPORTED
            ),
            evidence=(
                f"{sandbox} executable detected but runtime isolation not yet proven"
                if sandbox
                else "no container sandbox executable detected",
            ),
        ),
    )
    fingerprint = (
        f"{platform.system()}:{platform.machine()}:{int(git_available)}:{sandbox or 'none'}"
    )
    probe_id = "host-" + hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    return HostCapabilityProbeV1(
        probe_id=probe_id,
        os_name=platform.system() or "unknown",
        architecture=platform.machine() or "unknown",
        git_available=git_available,
        process_groups_available=platform.system() in {"Darwin", "Linux"},
        sandbox_backend=sandbox,
        overrides=overrides,
    )


__all__ = [
    "CapabilityDeclarationV1",
    "CapabilityMismatchV1",
    "CapabilityState",
    "ExecutorCapabilityManifestV1",
    "ExecutionMode",
    "ExecutorLevel",
    "HostCapabilityProbeV1",
    "MandatoryCapabilityMismatch",
    "claude_code_manifest",
    "codex_manifest",
    "default_executor_manifests",
    "get_executor_manifest",
    "native_manifest",
    "probe_host_capabilities",
    "resolve_governance_contract",
]
