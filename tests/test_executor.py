"""Tests for Executor."""

from unittest.mock import AsyncMock

import pytest

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

    async def test_shutdown(self, executor):
        await executor.shutdown()
