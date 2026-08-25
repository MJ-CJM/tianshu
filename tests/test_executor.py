"""Tests for Executor."""

from unittest.mock import AsyncMock

import pytest

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.bus.event_bus import EventBus
from tianshu.executor.adapters import ExecutorGenerationUnavailable
from tianshu.executor.dag_scheduler import DAGScheduler
from tianshu.executor.executor import Executor
from tianshu.executor.worker_pool import WorkerPool
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.acceptance import AcceptanceCriteria, CriticSpec
from tianshu.models.edict import EdictRuntime
from tianshu.models.events import make_event


class TestExecutor:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def hooks(self):
        return HookRegistry()

    @pytest.fixture
    def mock_agent(self):
        from tianshu.executor.agent import AgentResult

        agent = AsyncMock()
        agent.execute.return_value = AgentResult(
            status=TaskStatus.COMPLETED,
            summary="Done",
            result="Task completed",
            usage=UsageSummary(total_tokens=50),
        )
        return agent

    @pytest.fixture
    def executor(self, event_bus, storage, config_manager, hooks, mock_agent):
        ex = Executor(
            event_bus=event_bus,
            storage=storage,
            config_manager=config_manager,
            hook_registry=hooks,
        )
        ex.set_agent(mock_agent)
        return ex

    async def test_execute_edict(self, executor, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)

        await executor.execute_edict(edict)

        memorials = storage.list_memorials_by_edict(edict.id)
        assert len(memorials) == 1
        assert memorials[0].status == TaskStatus.COMPLETED
        assert memorials[0].result == "Task completed"
        # 单 task 路径：final_output 应等于 result（无中间过程混淆）
        assert memorials[0].final_output == "Task completed"

    async def test_execute_with_existing_memorial(self, executor, storage):
        edict = Edict(goal="test")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, instruction="test")
        storage.save_memorial(memorial)

        await executor.execute_edict(edict, memorial=memorial)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.status == TaskStatus.COMPLETED

    async def test_legacy_plan_completed_without_binding_fails_closed(
        self, executor, event_bus, storage
    ):
        edict = Edict(goal="via event")
        storage.save_edict(edict)

        event = make_event(
            "plan.completed",
            edict_id=edict.id,
            payload={"plan": {"tasks": [], "priority_order": []}},
        )
        with pytest.raises(RuntimeError, match="managed run ingress is not configured"):
            await executor.handle_plan_completed(event)
        assert storage.list_memorials_by_edict(edict.id) == []

    async def test_execute_attempt_defers_root_terminal_to_fenced_completer(
        self, executor, event_bus, storage
    ):
        edict = Edict(id="edict-managed", goal="managed execution")
        memorial = Memorial(id="root-managed", edict_id=edict.id, instruction=edict.goal)
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        terminal_events = AsyncMock()
        event_bus.on(
            "execution.completed",
            terminal_events,
            consumer_name="test.no_unfenced_terminal.v1",
        )
        authority = AttemptAuthority(
            attempt_id="attempt-managed",
            memorial_id=memorial.id,
            owner_id="worker",
            fencing_token=1,
        )
        from tianshu.models import Plan, PlanTask

        plan = Plan(
            tasks=[PlanTask(task_id="main", description=edict.goal)],
            priority_order=["main"],
        )

        projection = await executor.execute_attempt(authority, plan)

        assert projection.status is TaskStatus.COMPLETED
        assert projection.result == "Task completed"
        assert storage.get_memorial(memorial.id).status is TaskStatus.RUNNING
        terminal_events.assert_not_called()

    async def test_managed_dag_projects_generation_retired_without_early_terminal(
        self,
        executor,
        event_bus,
        storage,
        mock_agent,
    ):
        edict = Edict(id="edict-managed-dag", goal="managed DAG")
        memorial = Memorial(id="root-managed-dag", edict_id=edict.id, instruction=edict.goal)
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        mock_agent.execute.side_effect = ExecutorGenerationUnavailable("managed package drifted")
        pool = WorkerPool(max_concurrency=1)
        executor.set_dag_scheduler(DAGScheduler(pool, mock_agent, storage, event_bus))
        authority = AttemptAuthority(
            attempt_id="attempt-managed-dag",
            memorial_id=memorial.id,
            owner_id="worker",
            fencing_token=1,
        )
        from tianshu.models import Plan, PlanTask

        plan = Plan(
            tasks=[
                PlanTask(task_id="first", description="first"),
                PlanTask(task_id="second", description="second", depends_on=["first"]),
            ],
            priority_order=["first", "second"],
        )

        try:
            projection = await executor.execute_attempt(authority, plan)
        finally:
            await pool.shutdown()

        assert projection.status is TaskStatus.FAILED
        assert projection.failure_reason == "generation_retired"
        assert projection.error is not None
        assert projection.error.code == "generation_retired"
        assert storage.get_memorial(memorial.id).status is TaskStatus.RUNNING

    @pytest.mark.parametrize(
        ("base_executor", "override_executor"),
        [("keqing:pi", "native"), ("native", "keqing:pi")],
    )
    async def test_managed_dag_applies_one_turn_runtime_override_before_preparation(
        self,
        executor,
        storage,
        monkeypatch,
        base_executor: str,
        override_executor: str,
    ):
        edict = Edict(
            id=f"edict-{base_executor}-{override_executor}",
            goal="managed override",
            runtime=EdictRuntime(executor=base_executor),
        )
        memorial = Memorial(
            id=f"root-{base_executor}-{override_executor}",
            edict_id=edict.id,
            runtime_override={"executor": override_executor},
        )
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        execute_dag = AsyncMock()
        monkeypatch.setattr(executor, "_execute_dag", execute_dag)
        executor._dag_scheduler = object()
        authority = AttemptAuthority(
            attempt_id=f"attempt-{base_executor}-{override_executor}",
            memorial_id=memorial.id,
            owner_id="worker",
            fencing_token=1,
        )
        from tianshu.models import Plan, PlanTask

        plan = Plan(
            tasks=[
                PlanTask(task_id="first", description="first"),
                PlanTask(task_id="second", description="second"),
            ],
            priority_order=["first", "second"],
        )

        await executor.execute_attempt(authority, plan)

        applied = execute_dag.await_args.args[0]
        assert applied.runtime.executor == override_executor
        assert storage.get_edict(edict.id).runtime.executor == base_executor

    async def test_managed_attempt_applies_acceptance_override_before_path_selection(
        self,
        executor,
        storage,
        monkeypatch,
    ):
        acceptance = AcceptanceCriteria(
            max_outer_iterations=2,
            critic=CriticSpec(persona_ids=["ducha"]),
        )
        edict = Edict(id="edict-acceptance-override", goal="managed override")
        memorial = Memorial(
            id="root-acceptance-override",
            edict_id=edict.id,
            acceptance_override=acceptance,
        )
        storage.save_edict(edict)
        storage.save_memorial(memorial)
        execute_outer = AsyncMock()
        monkeypatch.setattr(executor, "_execute_outer_loop", execute_outer)
        executor._orchestrator_ctx = object()
        authority = AttemptAuthority(
            attempt_id="attempt-acceptance-override",
            memorial_id=memorial.id,
            owner_id="worker",
            fencing_token=1,
        )
        from tianshu.models import Plan, PlanTask

        plan = Plan(
            tasks=[PlanTask(task_id="main", description="main")],
            priority_order=["main"],
        )

        await executor.execute_attempt(authority, plan)

        applied = execute_outer.await_args.args[0]
        assert applied.acceptance == acceptance
        assert storage.get_edict(edict.id).acceptance is None

    async def test_shutdown(self, executor):
        await executor.shutdown()
