"""Executor adapter boundary independent from process and workspace backends."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from tianshu.executor.capabilities import (
    ExecutionMode,
    ExecutorCapabilityManifestV1,
    HostCapabilityProbeV1,
    probe_host_capabilities,
)
from tianshu.models.governance_contract import (
    EffectiveGovernanceContractV1,
    LegacyEdictGovernanceMapper,
)


@dataclass(frozen=True)
class PreparedExecution:
    """Run-bound proof that an adapter accepted an effective contract."""

    run_id: str
    effective: EffectiveGovernanceContractV1
    instruction: str
    execution_mode: ExecutionMode


@runtime_checkable
class ExecutorAdapter(Protocol):
    @property
    def adapter_id(self) -> str: ...

    @property
    def manifest(self) -> ExecutorCapabilityManifestV1: ...

    @property
    def supported_execution_modes(self) -> tuple[ExecutionMode, ...]: ...

    def probe(self) -> HostCapabilityProbeV1: ...

    def prepare(
        self,
        effective: EffectiveGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
    ) -> PreparedExecution: ...

    async def execute(
        self,
        prepared: PreparedExecution,
        edict: Any,
        **kwargs: Any,
    ) -> Any: ...

    async def cancel(self, run_id: str) -> bool: ...


@dataclass
class DelegatingExecutorAdapter:
    """Compatibility wrapper for current Native and Keqing implementations."""

    adapter_id: str
    manifest: ExecutorCapabilityManifestV1
    delegate: Any
    probe_factory: Callable[[], HostCapabilityProbeV1] = probe_host_capabilities

    def __post_init__(self) -> None:
        if self.adapter_id != self.manifest.adapter_id:
            raise ValueError("adapter id must match its manifest")

    def probe(self) -> HostCapabilityProbeV1:
        return self.probe_factory()

    @property
    def supported_execution_modes(self) -> tuple[ExecutionMode, ...]:
        return self.manifest.execution_modes

    def prepare(
        self,
        effective: EffectiveGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
    ) -> PreparedExecution:
        if effective.executor.adapter_id != self.adapter_id:
            raise ValueError("effective contract executor does not match adapter")
        if execution_mode not in self.supported_execution_modes:
            raise ValueError(f"adapter does not support execution mode {execution_mode!r}")
        return PreparedExecution(
            run_id=run_id,
            effective=effective,
            instruction=instruction,
            execution_mode=execution_mode,
        )

    async def execute(
        self,
        prepared: PreparedExecution,
        edict: Any,
        **kwargs: Any,
    ) -> Any:
        if prepared.effective.executor.adapter_id != self.adapter_id:
            raise ValueError("prepared execution does not match adapter")
        memorial = kwargs.get("memorial")
        if memorial is not None and getattr(memorial, "id", None) != prepared.run_id:
            raise ValueError("prepared execution run id does not match memorial")
        actual_instruction = kwargs.get("user_content") or prepared.instruction
        if prepared.execution_mode in {"single", "dag"}:
            if actual_instruction != prepared.instruction:
                raise ValueError("actual instruction does not match prepared execution")
        elif edict.goal != prepared.instruction:
            raise ValueError("outer-loop objective does not match prepared execution")

        execution_edict = (
            edict
            if edict.goal == prepared.instruction
            else edict.model_copy(update={"goal": prepared.instruction})
        )
        mapped = execution_edict.governance_contract
        if mapped is None:
            mapped = LegacyEdictGovernanceMapper.from_edict(
                execution_edict,
                default_workspace_id=prepared.effective.workspace.source_id,
            )
        elif memorial is not None:
            mapped = LegacyEdictGovernanceMapper.apply_run_overrides(
                mapped,
                execution_edict,
                runtime_overridden=getattr(memorial, "runtime_override", None) is not None,
                acceptance_overridden=(getattr(memorial, "acceptance_override", None) is not None),
            )
        mapped_permissions = mapped.permissions.model_copy(
            update={"secret_refs": prepared.effective.permissions.secret_refs}
        )
        if any(
            actual != expected
            for actual, expected in (
                (mapped.acceptance, prepared.effective.acceptance),
                (mapped.executor, prepared.effective.executor),
                (mapped_permissions, prepared.effective.permissions),
                (mapped.network, prepared.effective.network),
                (mapped.budget, prepared.effective.budget),
                (mapped.workspace, prepared.effective.workspace),
                (mapped.recovery, prepared.effective.recovery),
            )
        ):
            raise ValueError("runtime policy does not match prepared effective contract")
        if prepared.effective.executor.model is not None:
            kwargs["model_override"] = prepared.effective.executor.model
        return await self.delegate.execute(execution_edict, **kwargs)

    async def cancel(self, run_id: str) -> bool:
        cancel = getattr(self.delegate, "cancel", None)
        if cancel is None:
            return False
        result = cancel(run_id)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)


__all__ = [
    "DelegatingExecutorAdapter",
    "ExecutionMode",
    "ExecutorAdapter",
    "PreparedExecution",
]
