"""Tests for Planner."""

from unittest.mock import AsyncMock

import pytest

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict
from tianshu.models.events import make_event
from tianshu.planner.planner import Planner


class TestPlanner:
    @pytest.fixture
    def event_bus(self):
        return EventBus()

    @pytest.fixture
    def planner(self, event_bus, storage, config_manager):
        config_manager.update(enabled=False)
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
        assert plan.planning_mode == "fallback"
        assert plan.fallback_reason == "llm_disabled"

    async def test_direct_assignment_is_not_reported_as_degradation(self, planner):
        edict = Edict(goal="direct task", assigned_persona_id="bingbu")

        plan = await planner.plan(edict)

        assert plan.planning_mode == "direct"
        assert plan.fallback_reason is None

    async def test_handle_scheduled(self, planner, event_bus, storage, config_manager):
        handler = AsyncMock()
        event_bus.on("plan.completed", handler, consumer_name="test.plan_completed.v1")

        edict = Edict(goal="test")
        storage.save_edict(edict)

        event = make_event("edict.scheduled", edict_id=edict.id)
        await planner.handle_scheduled(event)

        handler.assert_called_once()
        call_event = handler.call_args[0][0]
        assert call_event.event_type == "plan.completed"
        assert "plan" in call_event.payload

    async def test_plan_attempt_returns_plan_without_event_bus_chaining(
        self, planner, event_bus, storage
    ):
        edict = Edict(id="edict-managed", goal="managed planning")
        storage.save_edict(edict)
        from tianshu.models import Memorial

        memorial = Memorial(id="root-managed", edict_id=edict.id, instruction=edict.goal)
        storage.save_memorial(memorial)
        authority = AttemptAuthority(
            attempt_id="attempt-managed",
            memorial_id=memorial.id,
            owner_id="worker",
            fencing_token=1,
        )

        result = await planner.plan_attempt(authority)

        assert result.suspended is False
        assert result.plan.tasks[0].description == edict.goal
        assert storage.get_memorial(memorial.id).status.value == "planning"
