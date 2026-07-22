"""Governance context shared by Universe gate and sandbox executions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

from ulid import ULID

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import ExecutionContext
from tianshu.models.governance_contract import (
    BudgetPolicyV1,
    NetworkPolicyV1,
    ObjectiveV1,
    PermissionPolicyV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind

type SecurityMode = Literal["trusted-local", "secure-remote"]
type UniverseOperation = Literal["gate", "sandbox"]


def _current_or_system_actor() -> Principal:
    try:
        from tianshu.gateway.auth import get_current_auth_context

        auth_context = get_current_auth_context()
    except (ImportError, RuntimeError):
        auth_context = None
    if auth_context is not None:
        return auth_context.principal
    return Principal(
        id="system:universe",
        kind=PrincipalKind.SERVICE,
        display_name="Universe Evolution",
    )


class UniverseExecutionContextFactory:
    """Create one traceable contract/identity context per Universe operation."""

    def __init__(
        self,
        *,
        security_mode: SecurityMode,
        actor_provider: Callable[[], Principal] | None = None,
    ) -> None:
        self.security_mode = security_mode
        self._actor_provider = actor_provider or _current_or_system_actor

    def create(
        self,
        *,
        operation: UniverseOperation,
        timeout_seconds: float,
        secret_refs: Sequence[str] = (),
    ) -> ExecutionContext:
        requested = RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal=f"Run governed Universe {operation}"),
            permissions=PermissionPolicyV1(secret_refs=tuple(secret_refs)),
            network=NetworkPolicyV1(
                mode=("unrestricted_requested" if self.security_mode == "trusted-local" else "deny")
            ),
            budget=BudgetPolicyV1(wall_clock_seconds=max(1, int(timeout_seconds))),
        )
        effective = resolve_governance_contract(
            requested,
            native_manifest(),
            probe_host_capabilities(),
        )
        run_id = str(ULID())
        return ExecutionContext(
            correlation_id=f"universe:{operation}:{run_id}",
            actor=self._actor_provider(),
            effective_contract=effective,
            workspace_lease_id=f"universe:{operation}:{run_id}",
        )


__all__ = ["SecurityMode", "UniverseExecutionContextFactory", "UniverseOperation"]
