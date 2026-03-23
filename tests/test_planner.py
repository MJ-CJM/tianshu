"""Tests for Planner."""

from unittest.mock import AsyncMock, patch

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict
from tianshu.models.edict import EdictRuntime
from tianshu.models.events import make_event
from tianshu.planner.planner import Planner


class TestPlanner:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def planner(self, event_bus, storage, config_manager):
        return Planner(
            event_bus=event_bus,
            storage=storage,
            config_manager=config_manager,
        )

    async def test_plan_simple(self, planner):
        edict = Edict(goal="simple task")
        plan = await planner.plan(edict)
        assert len(plan.tasks) == 1
        assert plan.tasks[0].task_id == "main"
        assert plan.tasks[0].description == edict.goal

    async def test_handle_scheduled(self, planner, event_bus, storage, config_manager):
        handler = AsyncMock()
        event_bus.on("plan.completed", handler)

        edict = Edict(goal="test")
        storage.save_edict(edict)

        event = make_event("edict.scheduled", edict_id=edict.id)
        await planner.handle_scheduled(event)

        handler.assert_called_once()
        call_event = handler.call_args[0][0]
        assert call_event.event_type == "plan.completed"
        assert "plan" in call_event.payload
