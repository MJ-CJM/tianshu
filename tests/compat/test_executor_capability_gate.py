"""Executor resolves and persists governance before invoking an implementation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.adapters import PreparedExecutor
from tianshu.executor.agent import AgentResult
from tianshu.executor.executor import Executor
from tianshu.executor.worker import Worker
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.dag import DAGNode
from tianshu.models.edict import EdictRuntime
from tianshu.models.governance_contract import (
    CapabilityRequirementsV1,
    LegacyEdictGovernanceMapper,
)
from tianshu.models.plan import Plan, PlanTask


@pytest.fixture
def agent() -> AsyncMock:
    value = AsyncMock()
    value.execute.return_value = AgentResult(
        status=TaskStatus.COMPLETED,
        result="done",
        usage=UsageSummary(total_tokens=1),
    )
    return value


@pytest.fixture
def executor(storage, config_manager, agent) -> Executor:
    value = Executor(
        event_bus=EventBus(),
        storage=storage,
        config_manager=config_manager,
        hook_registry=HookRegistry(),
    )
    value.set_agent(agent)
    return value


def _edict_with_capabilities(
    *,
    adapter_id: str = "native",
    mandatory: tuple[str, ...] = (),
    advisory: tuple[str, ...] = (),
) -> Edict:
    base = Edict(goal="governed", runtime=EdictRuntime(executor=adapter_id))
    contract = LegacyEdictGovernanceMapper.from_edict(
        base,
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "capabilities": CapabilityRequirementsV1(
                mandatory=mandatory,
                advisory=advisory,
            )
        }
    )
    return base.model_copy(update={"governance_contract": contract})


async def test_mandatory_mismatch_blocks_before_agent_invocation(
    executor,
    storage,
    agent,
) -> None:
    edict = _edict_with_capabilities(mandatory=("pre_run_restore_point",))
    storage.save_edict(edict)

    await executor.execute_edict(storage.get_edict(edict.id))

    agent.execute.assert_not_awaited()
    memorial = storage.get_memorial_by_edict(edict.id)
    assert memorial is not None
    assert memorial.status is TaskStatus.FAILED
    assert "pre_run_restore_point=unsupported" in memorial.error
    assert memorial.effective_governance_contract is None


async def test_advisory_gap_is_persisted_per_memorial_before_agent_runs(
    executor,
    storage,
    agent,
) -> None:
    edict = _edict_with_capabilities(advisory=("durable_resume",))
    storage.save_edict(edict)

    await executor.execute_edict(storage.get_edict(edict.id))

    agent.execute.assert_awaited_once()
    memorial = storage.get_memorial_by_edict(edict.id)
    assert memorial is not None
    assert memorial.status is TaskStatus.COMPLETED
    assert memorial.effective_governance_contract is not None
    assert memorial.effective_governance_contract.unsupported_advisory == ("durable_resume",)
    assert (
        memorial.effective_governance_contract.requested_contract_hash
        == edict.governance_contract.content_hash
    )


async def test_follow_up_executor_override_gets_its_own_effective_contract(
    executor,
    storage,
    agent,
) -> None:
    edict = _edict_with_capabilities()
    storage.save_edict(edict)
    memorial = Memorial(
        edict_id=edict.id,
        instruction="use codex for this follow-up",
        runtime_override={"executor": "keqing:codex"},
    )
    storage.save_memorial(memorial)
    executor._keqing.execute = AsyncMock(  # noqa: SLF001 - integration seam
        return_value=AgentResult(
            status=TaskStatus.COMPLETED,
            result="codex done",
            usage=UsageSummary(total_tokens=2),
        )
    )

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

    agent.execute.assert_not_awaited()
    executor._keqing.execute.assert_awaited_once()  # noqa: SLF001
    loaded = storage.get_memorial(memorial.id)
    assert loaded.effective_governance_contract.executor.adapter_id == "keqing:codex"
    assert (
        loaded.effective_governance_contract.requested_contract_hash
        != edict.governance_contract.content_hash
    )


async def test_auto_retry_preserves_run_overrides_and_prepared_instruction(
    executor,
    storage,
    agent,
) -> None:
    base = Edict(
        goal="base objective",
        runtime=EdictRuntime(retry_limit=2, approval_required_tools=["shell"]),
    )
    contract = LegacyEdictGovernanceMapper.from_edict(
        base,
        default_workspace_id="workspace-main",
    )
    edict = base.model_copy(update={"governance_contract": contract})
    storage.save_edict(edict)
    memorial = Memorial(
        edict_id=edict.id,
        instruction="retry this instruction",
        runtime_override={"executor": "keqing:codex"},
    )
    storage.save_memorial(memorial)
    executor._keqing.execute = AsyncMock(  # noqa: SLF001 - integration seam
        side_effect=[
            AgentResult(status=TaskStatus.FAILED, error="transient"),
            AgentResult(
                status=TaskStatus.COMPLETED,
                result="done",
                usage=UsageSummary(total_tokens=1),
            ),
        ]
    )

    await executor.execute_edict(
        storage.get_edict(edict.id),
        memorial=memorial,
        user_content=memorial.instruction,
    )
    while executor.running_tasks:
        await asyncio.gather(*list(executor.running_tasks))

    memorials = sorted(storage.list_memorials_by_edict(edict.id), key=lambda item: item.attempt)
    assert [item.runtime_override for item in memorials] == [
        {"executor": "keqing:codex"},
        {"executor": "keqing:codex"},
    ]
    assert memorials[1].effective_governance_contract.executor.adapter_id == "keqing:codex"
    assert executor._keqing.execute.await_count == 2  # noqa: SLF001
    assert [call.args[0].goal for call in executor._keqing.execute.await_args_list] == [  # noqa: SLF001
        "retry this instruction",
        "retry this instruction",
    ]
    agent.execute.assert_not_awaited()


async def test_dag_rejects_contained_executor_before_scheduler_runs(
    executor,
    storage,
) -> None:
    scheduler = SimpleNamespace(run=AsyncMock())
    executor.set_dag_scheduler(scheduler)
    edict = _edict_with_capabilities(adapter_id="keqing:codex")
    storage.save_edict(edict)
    plan = Plan(
        tasks=[
            PlanTask(task_id="one", description="one"),
            PlanTask(task_id="two", description="two", depends_on=["one"]),
        ]
    )

    await executor._execute_dag(storage.get_edict(edict.id), plan)  # noqa: SLF001

    scheduler.run.assert_not_awaited()
    memorial = storage.get_memorial_by_edict(edict.id)
    assert memorial.status is TaskStatus.FAILED
    assert "does not support execution mode 'dag'" in memorial.error
    assert memorial.effective_governance_contract is None


async def test_native_dag_passes_prepared_executor_to_scheduler(
    executor,
    storage,
) -> None:
    scheduler = SimpleNamespace(run=AsyncMock())
    executor.set_dag_scheduler(scheduler)
    edict = _edict_with_capabilities()
    storage.save_edict(edict)
    plan = Plan(tasks=[PlanTask(task_id="one", description="one")])

    await executor._execute_dag(storage.get_edict(edict.id), plan)  # noqa: SLF001

    prepared = scheduler.run.await_args.kwargs["prepared_executor"]
    assert isinstance(prepared, PreparedExecutor)
    assert prepared.effective.executor.adapter_id == "native"


async def test_dag_retry_reuses_governed_prepared_executor(
    executor,
    storage,
) -> None:
    scheduler = SimpleNamespace(run=AsyncMock())
    executor.set_dag_scheduler(scheduler)
    edict = _edict_with_capabilities()
    storage.save_edict(edict)
    plan = Plan(tasks=[PlanTask(task_id="one", description="one")])
    await executor._execute_dag(storage.get_edict(edict.id), plan)  # noqa: SLF001
    execution = storage.get_dag_by_edict(edict.id)
    storage.update_dag_node_status(execution.id, "one", "failed", error="retry")
    storage.update_dag_execution_status(execution.id, "failed")
    scheduler.run.reset_mock()

    assert await executor.retry_dag(execution.id) == ["one"]
    while executor.running_tasks:
        await asyncio.gather(*list(executor.running_tasks))

    prepared = scheduler.run.await_args.kwargs["prepared_executor"]
    assert isinstance(prepared, PreparedExecutor)
    assert prepared.effective.executor.adapter_id == "native"


async def test_dag_node_persists_effective_contract_before_agent_runs(
    executor,
    storage,
    agent,
) -> None:
    edict = _edict_with_capabilities()
    storage.save_edict(edict)
    root = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(root)
    prepared = executor._prepare_governed_executor(  # noqa: SLF001
        storage.get_edict(edict.id),
        root,
        execution_mode="dag",
    )
    worker = Worker(agent, storage)
    node = DAGNode(node_id="node-1", description="work")

    await worker.execute_node(
        storage.get_edict(edict.id),
        node,
        {},
        prepared_executor=prepared,
    )

    memorial = storage.get_memorial(node.memorial_id)
    assert memorial.effective_governance_contract is not None
    assert memorial.effective_governance_contract.content_hash == prepared.effective.content_hash


async def test_outer_loop_rejects_contained_executor_before_orchestrator_runs(
    executor,
    storage,
    monkeypatch,
) -> None:
    run = AsyncMock()
    monkeypatch.setattr("tianshu.executor.orchestrator.run", run)
    executor.set_orchestrator_context(SimpleNamespace(agent=object()))
    edict = _edict_with_capabilities(adapter_id="keqing:codex").model_copy(
        update={"acceptance": AcceptanceCriteria()}
    )
    storage.save_edict(edict)

    await executor._execute_outer_loop(storage.get_edict(edict.id), None)  # noqa: SLF001

    run.assert_not_awaited()
    memorial = storage.get_memorial_by_edict(edict.id)
    assert memorial.status is TaskStatus.FAILED
    assert "does not support execution mode 'outer_loop'" in memorial.error
    assert memorial.effective_governance_contract is None


async def test_native_outer_loop_executes_through_prepared_adapter(
    executor,
    storage,
    monkeypatch,
) -> None:
    async def run(_edict, _memorial, ctx):
        assert isinstance(ctx.agent, PreparedExecutor)
        assert ctx.agent.effective.executor.adapter_id == "native"
        return SimpleNamespace(
            status=TaskStatus.COMPLETED,
            final_output="done",
            error=None,
        )

    monkeypatch.setattr("tianshu.executor.orchestrator.run", run)
    executor.set_orchestrator_context(SimpleNamespace(agent=object()))
    base = Edict(goal="governed", acceptance=AcceptanceCriteria())
    contract = LegacyEdictGovernanceMapper.from_edict(
        base,
        default_workspace_id="workspace-main",
    )
    edict = base.model_copy(update={"governance_contract": contract})
    storage.save_edict(edict)

    await executor._execute_outer_loop(storage.get_edict(edict.id), None)  # noqa: SLF001

    memorial = storage.get_memorial_by_edict(edict.id)
    assert memorial.status is TaskStatus.COMPLETED
    assert memorial.effective_governance_contract.executor.adapter_id == "native"
