"""Registry that resolves governance before exposing an executor implementation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from tianshu.executor.adapters.protocol import (
    DelegatingExecutorAdapter,
    ExecutionMode,
    ExecutorAdapter,
    PreparedExecution,
)
from tianshu.executor.capabilities import resolve_governance_contract
from tianshu.models.governance_contract import (
    EffectiveGovernanceContractV1,
    RequestedGovernanceContractV1,
)


@dataclass(frozen=True)
class PreparedExecutor:
    adapter: ExecutorAdapter
    effective: EffectiveGovernanceContractV1
    prepared: PreparedExecution

    def bind_run(self, run_id: str, *, instruction: str) -> PreparedExecutor:
        return PreparedExecutor(
            adapter=self.adapter,
            effective=self.effective,
            prepared=self.adapter.prepare(
                self.effective,
                run_id=run_id,
                instruction=instruction,
                execution_mode=self.prepared.execution_mode,
            ),
        )

    async def execute(self, edict: Any, **kwargs: Any) -> Any:
        return await self.adapter.execute(self.prepared, edict, **kwargs)

    async def cancel(self) -> bool:
        return await self.adapter.cancel(self.prepared.run_id)


class UnsupportedExecutorMode(ValueError):
    def __init__(self, adapter_id: str, execution_mode: ExecutionMode) -> None:
        self.adapter_id = adapter_id
        self.execution_mode = execution_mode
        super().__init__(
            f"executor adapter '{adapter_id}' does not support execution mode '{execution_mode}'"
        )


class ExecutorAdapterRegistry:
    def __init__(self, adapters: Iterable[ExecutorAdapter] = ()) -> None:
        self._adapters: dict[str, ExecutorAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ExecutorAdapter) -> None:
        if adapter.adapter_id != adapter.manifest.adapter_id:
            raise ValueError("adapter id must match its capability manifest")
        if adapter.supported_execution_modes != adapter.manifest.execution_modes:
            raise ValueError("adapter execution modes must match its capability manifest")
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"executor adapter already registered: {adapter.adapter_id}")
        self._adapters[adapter.adapter_id] = adapter

    def replace(self, adapter: ExecutorAdapter) -> None:
        if adapter.adapter_id != adapter.manifest.adapter_id:
            raise ValueError("adapter id must match its capability manifest")
        if adapter.supported_execution_modes != adapter.manifest.execution_modes:
            raise ValueError("adapter execution modes must match its capability manifest")
        self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> ExecutorAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise KeyError(f"unknown executor adapter: {adapter_id}") from exc

    def prepare(
        self,
        requested: RequestedGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
    ) -> PreparedExecutor:
        adapter = self.get(requested.executor.adapter_id)
        if execution_mode not in adapter.supported_execution_modes:
            raise UnsupportedExecutorMode(adapter.adapter_id, execution_mode)
        effective = resolve_governance_contract(requested, adapter.manifest, adapter.probe())
        return self.bind_effective(
            effective,
            run_id=run_id,
            instruction=instruction,
            execution_mode=execution_mode,
        )

    def bind_effective(
        self,
        effective: EffectiveGovernanceContractV1,
        *,
        run_id: str,
        instruction: str,
        execution_mode: ExecutionMode,
    ) -> PreparedExecutor:
        adapter = self.get(effective.executor.adapter_id)
        if execution_mode not in adapter.supported_execution_modes:
            raise UnsupportedExecutorMode(adapter.adapter_id, execution_mode)
        prepared = adapter.prepare(
            effective,
            run_id=run_id,
            instruction=instruction,
            execution_mode=execution_mode,
        )
        return PreparedExecutor(adapter=adapter, effective=effective, prepared=prepared)


__all__ = [
    "DelegatingExecutorAdapter",
    "ExecutionMode",
    "ExecutorAdapter",
    "ExecutorAdapterRegistry",
    "PreparedExecution",
    "PreparedExecutor",
    "UnsupportedExecutorMode",
]
