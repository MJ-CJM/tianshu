"""Tests for Executor."""

from unittest.mock import AsyncMock

import pytest

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.bus.event_bus import EventBus
from tianshu.executor.executor import Executor
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
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

    async def test_handle_plan_completed(self, executor, event_bus, storage):
        edict = Edict(goal="via event")
        storage.save_edict(edict)

        event = make_event(
            "plan.completed",
            edict_id=edict.id,
            payload={"plan": {"tasks": [], "priority_order": []}},
        )
        await executor.handle_plan_completed(event)

        # Wait for background task
        import asyncio

        await asyncio.sleep(0.1)

        memorials = storage.list_memorials_by_edict(edict.id)
        assert len(memorials) >= 1

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

    async def test_shutdown(self, executor):
        await executor.shutdown()
