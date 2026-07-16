"""Production runner to ToolRegistry managed-effect integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tianshu.application.fenced_run_completion import FencedRunCompletion
from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.application.run_execution import (
    ManagedExecutionProjection,
    ManagedPlanningResult,
    ProductionAttemptCompleter,
    ProductionRunRunner,
)
from tianshu.executor.managed_tools import ManagedToolEffectExecutor
from tianshu.governance.decision_service import DecisionService
from tianshu.kernel.ambient import get_current_tool_invocation_id
from tianshu.models import Edict, Memorial, Plan, PlanTask, TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.side_effect import SideEffectSemantics
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, ok_result

_NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)
_PLAN = Plan(
    tasks=[PlanTask(task_id="main", description="invoke tool")],
    priority_order=["main"],
)


class _Planner:
    async def plan_attempt(self, authority: AttemptAuthority) -> ManagedPlanningResult:
        del authority
        return ManagedPlanningResult(plan=_PLAN)


class _RegistryExecutor:
    def __init__(self, registry: ToolRegistry, tool_name: str) -> None:
        self._registry = registry
        self._tool_name = tool_name

    async def execute_attempt(
        self,
        authority: AttemptAuthority,
        plan: Plan,
    ) -> ManagedExecutionProjection:
        del authority, plan
        result = await self._registry.execute(
            self._tool_name,
            {"value": "safe"},
            invocation_id="provider-call-1",
        )
        return ManagedExecutionProjection(
            status=TaskStatus.FAILED if result.is_error else TaskStatus.COMPLETED,
            result=result.content,
        )


def _claim(storage, *, max_attempts: int = 2) -> AttemptAuthority:
    storage.save_edict(Edict(id="edict-managed-tool", goal="invoke a managed tool"))
    storage.save_memorial(
        Memorial(
            id="memorial-managed-tool",
            edict_id="edict-managed-tool",
            status=TaskStatus.RUNNING,
        )
    )
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="memorial-managed-tool",
            available_at=_NOW,
            max_attempts=max_attempts,
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-managed-tool",
        owner_id="worker-managed-tool",
        now=_NOW,
        lease_seconds=60,
    )
    assert claimed is not None and claimed.owner_id is not None
    return AttemptAuthority(
        attempt_id=claimed.attempt_id,
        memorial_id=claimed.memorial_id,
        owner_id=claimed.owner_id,
        fencing_token=claimed.fencing_token,
    )


def _runtime(storage, *, boundary_hook=None) -> ManagedToolEffectExecutor:
    return ManagedToolEffectExecutor(
        storage,
        DecisionService(storage, clock=lambda: _NOW + timedelta(seconds=1)),
        clock=lambda: _NOW + timedelta(seconds=1),
        boundary_hook=boundary_hook,
    )


async def test_runner_managed_tool_persists_receipt_before_attempt_success(storage) -> None:
    authority = _claim(storage)
    registry = ToolRegistry()
    invocation_keys: list[str] = []
    effective: dict[str, ToolResult] = {}

    async def managed_send(value: str) -> ToolResult:
        assert value == "safe"
        key = get_current_tool_invocation_id()
        assert key is not None
        invocation_keys.append(key)
        return effective.setdefault(key, ok_result("accepted"))

    registry.register(
        "managed_send",
        managed_send,
        ToolDefinition(
            name="managed_send",
            description="provider-idempotent test adapter",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            tier=ToolTier.T3_WRITE,
            side_effect=True,
            managed_effect_semantics=SideEffectSemantics.PROVIDER_IDEMPOTENT,
        ),
    )
    registry.set_managed_effect_executor(_runtime(storage))
    runner = ProductionRunRunner(_Planner(), _RegistryExecutor(registry, "managed_send"))

    result = await runner(authority)

    assert result.disposition is AttemptDisposition.SUCCEEDED
    journal = storage._conn.execute(  # noqa: SLF001
        "SELECT status, provider_idempotency_key FROM side_effect_journal"
    ).fetchone()
    assert journal is not None and tuple(journal) == ("receipted", invocation_keys[0])
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id=?",
            (authority.attempt_id,),
        ).fetchone()[0]
        == "claimed"
    )

    completer = ProductionAttemptCompleter(
        FencedRunCompletion(storage.unit_of_work, storage.attempt_repo),
        storage.attempt_repo,
        runner,
    )
    assert completer(
        authority,
        AttemptOutcomeV1(
            disposition=AttemptDisposition.SUCCEEDED,
            completed_at=_NOW + timedelta(seconds=2),
        ),
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id=?",
            (authority.attempt_id,),
        ).fetchone()[0]
        == "succeeded"
    )


async def test_provider_crash_cannot_return_success_before_receipt(storage) -> None:
    authority = _claim(storage)
    registry = ToolRegistry()
    invocations = 0

    async def managed_send(value: str) -> ToolResult:
        nonlocal invocations
        assert value == "safe"
        invocations += 1
        return ok_result("accepted")

    registry.register(
        "managed_send",
        managed_send,
        ToolDefinition(
            name="managed_send",
            description="provider-idempotent test adapter",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
            tier=ToolTier.T3_WRITE,
            side_effect=True,
            managed_effect_semantics=SideEffectSemantics.PROVIDER_IDEMPOTENT,
        ),
    )

    def crash(boundary: str) -> None:
        if boundary == "after_provider":
            raise RuntimeError("provider returned before receipt")

    registry.set_managed_effect_executor(_runtime(storage, boundary_hook=crash))
    runner = ProductionRunRunner(_Planner(), _RegistryExecutor(registry, "managed_send"))

    result = await runner(authority)

    assert result.disposition is AttemptDisposition.FAILED
    assert invocations == 1
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM side_effect_journal WHERE status='receipted'"
        ).fetchone()[0]
        == 0
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id=?",
            (authority.attempt_id,),
        ).fetchone()[0]
        == "claimed"
    )


async def test_runner_opaque_tool_suspends_once_without_invocation(storage) -> None:
    authority = _claim(storage, max_attempts=1)
    registry = ToolRegistry()
    invocations = 0

    async def opaque_write(value: str) -> ToolResult:
        nonlocal invocations
        del value
        invocations += 1
        return ok_result("must not execute")

    registry.register(
        "opaque_write",
        opaque_write,
        ToolDefinition(
            name="opaque_write",
            description="opaque side effect",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
            tier=ToolTier.T4_DANGEROUS,
            side_effect=True,
        ),
    )
    registry.set_managed_effect_executor(_runtime(storage))
    runner = ProductionRunRunner(_Planner(), _RegistryExecutor(registry, "opaque_write"))

    result = await runner(authority)

    assert result.disposition is AttemptDisposition.SUSPENDED
    assert invocations == 0
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM decision_requests WHERE status='pending'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id=?",
            (authority.attempt_id,),
        ).fetchone()[0]
        == "suspended"
    )

    completer = ProductionAttemptCompleter(
        FencedRunCompletion(storage.unit_of_work, storage.attempt_repo),
        storage.attempt_repo,
        runner,
    )
    assert completer(
        authority,
        AttemptOutcomeV1(
            disposition=AttemptDisposition.SUSPENDED,
            completed_at=_NOW + timedelta(seconds=2),
        ),
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM decision_requests"
        ).fetchone()[0]
        == 1
    )


async def test_managed_effect_fails_closed_without_attempt_authority(storage) -> None:
    registry = ToolRegistry()
    invocations = 0

    async def managed_send(value: str) -> ToolResult:
        nonlocal invocations
        del value
        invocations += 1
        return ok_result("accepted")

    registry.register(
        "managed_send",
        managed_send,
        ToolDefinition(
            name="managed_send",
            description="managed side effect",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
            tier=ToolTier.T3_WRITE,
            side_effect=True,
            managed_effect_semantics=SideEffectSemantics.PROVIDER_IDEMPOTENT,
        ),
    )
    registry.set_managed_effect_executor(_runtime(storage))

    result = await registry.execute("managed_send", {"value": "safe"})

    assert result.is_error
    assert "managed attempt authority" in result.content
    assert invocations == 0


async def test_side_effect_fails_closed_with_authority_but_without_managed_adapter(
    storage,
) -> None:
    authority = _claim(storage)
    registry = ToolRegistry()
    invocations = 0

    async def undeclared_write(value: str) -> ToolResult:
        nonlocal invocations
        del value
        invocations += 1
        return ok_result("must not execute")

    registry.register(
        "undeclared_write",
        undeclared_write,
        ToolDefinition(
            name="undeclared_write",
            description="side effect without an installed managed adapter",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
            tier=ToolTier.T3_WRITE,
            side_effect=True,
        ),
    )
    runner = ProductionRunRunner(_Planner(), _RegistryExecutor(registry, "undeclared_write"))

    result = await runner(authority)

    assert result.disposition is AttemptDisposition.FAILED
    assert invocations == 0
