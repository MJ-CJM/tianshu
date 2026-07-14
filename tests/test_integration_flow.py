"""Integration test — full event chain: submit → schedule → plan → execute → audit → notify."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tianshu.application.event_history import EventHistoryConsumer
from tianshu.auditor.auditor import Auditor
from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
from tianshu.executor.agent import Agent, AgentResult
from tianshu.executor.executor import Executor
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.events import make_event
from tianshu.notifier.notifier import Notifier
from tianshu.planner.planner import Planner
from tianshu.scheduler.scheduler import Scheduler


class TestFullEventChain:
    @pytest.fixture
    def config_manager(self):
        initial = LLMConfigState(
            name="test", model="test", api_key="k", api_base="http://localhost"
        )
        agent_cfg = AgentConfigState(agent_max_iterations=5, agent_timeout_seconds=10)
        return ConfigManager(initial, agent_config=agent_cfg)

    @pytest.fixture
    def event_bus(self, storage):
        bus = EventBus()
        history = EventHistoryConsumer(storage)
        bus.on("*", history, consumer_name=history.consumer_name, priority=0)
        return bus

    @pytest.fixture
    def hooks(self):
        return HookRegistry()

    async def test_full_chain(self, storage, event_bus, config_manager, hooks):
        """Test: edict.submitted → scheduled → plan.completed → execution → audit → notify."""

        tracked_events = (
            "edict.submitted",
            "edict.scheduled",
            "plan.completed",
            "execution.started",
            "execution.completed",
            "audit.completed",
        )
        events_seen: list[str] = []

        async def track(e):
            events_seen.append(e.event_type)

        for etype in tracked_events:
            event_bus.on(
                etype,
                track,
                consumer_name="test.integration_track.v1",
                priority=999,
            )

        # Create components
        scheduler = Scheduler(event_bus=event_bus, storage=storage)
        planner = Planner(event_bus=event_bus, storage=storage, config_manager=config_manager)
        executor = Executor(
            event_bus=event_bus,
            storage=storage,
            config_manager=config_manager,
            hook_registry=hooks,
        )
        auditor = Auditor(event_bus=event_bus, storage=storage, config_manager=config_manager)
        notifier = Notifier(storage=storage)

        # Mock agent
        mock_agent = AsyncMock(spec=Agent)
        mock_agent.execute.return_value = AgentResult(
            status=TaskStatus.COMPLETED,
            summary="Done",
            result="Task completed",
            usage=UsageSummary(total_tokens=50),
        )
        executor.set_agent(mock_agent)

        # Wire subscriptions
        event_bus.on(
            "edict.submitted",
            scheduler.handle_submitted,
            consumer_name="test.scheduler.v1",
        )
        event_bus.on(
            "edict.scheduled",
            planner.handle_scheduled,
            consumer_name="test.planner.v1",
            priority=50,
        )
        event_bus.on(
            "plan.completed",
            executor.handle_plan_completed,
            consumer_name="test.executor.v1",
            priority=100,
        )
        event_bus.on(
            "execution.completed",
            auditor.handle_execution_completed,
            consumer_name="test.auditor.v1",
        )
        event_bus.on(
            "audit.completed",
            notifier.handle_audit_completed,
            consumer_name="test.notifier.v1",
        )

        # Create edict
        edict = Edict(goal="test full chain", assigned_persona_id="bingbu")
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
        storage.save_memorial(memorial)

        # Trigger the chain
        await event_bus.emit(
            make_event("edict.submitted", edict_id=edict.id, payload={"goal": edict.goal})
        )
        await asyncio.gather(*tuple(executor.running_tasks))

        # Verify the chain executed
        assert len(events_seen) == len(tracked_events)
        assert set(events_seen) == set(tracked_events)

        persisted_events = [
            event["event_type"]
            for event in storage.get_events(edict.id)
            if event["event_type"] in tracked_events
        ]
        assert persisted_events == list(tracked_events)

        # Verify memorial was created/updated
        memorials = storage.list_memorials_by_edict(edict.id)
        assert len(memorials) >= 1

        # Cleanup
        await executor.shutdown()

    async def test_passthrough_for_simple_edict(self, storage, event_bus, config_manager, hooks):
        """Simple edict should skip planning (passthrough)."""
        planner = Planner(event_bus=event_bus, storage=storage, config_manager=config_manager)

        edict = Edict(goal="simple")
        storage.save_edict(edict)

        plan_events = []

        async def capture(e):
            plan_events.append(e)

        event_bus.on("plan.completed", capture, consumer_name="test.plan_capture.v1")

        await planner.handle_scheduled(make_event("edict.scheduled", edict_id=edict.id))

        assert len(plan_events) == 1
        plan_data = plan_events[0].payload["plan"]
        assert len(plan_data["tasks"]) == 1
        assert plan_data["tasks"][0]["task_id"] == "main"
