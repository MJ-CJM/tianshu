"""Integration test — durable managed execution through audit and notification."""

from unittest.mock import AsyncMock

import pytest

from tianshu.application.event_history import EventHistoryConsumer
from tianshu.application.fenced_run_completion import FencedRunCompletion
from tianshu.application.managed_run_ingress import ManagedRunCommand, ManagedRunIngress
from tianshu.application.outbox import OutboxDispatcher
from tianshu.application.run_dispatcher import RunDispatcher
from tianshu.application.run_execution import ProductionAttemptCompleter, ProductionRunRunner
from tianshu.application.run_reconciler import RunReconciler
from tianshu.auditor.auditor import Auditor
from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
from tianshu.executor.agent import Agent, AgentResult
from tianshu.executor.executor import Executor
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, TaskStatus, UsageSummary
from tianshu.models.events import make_event
from tianshu.notifier.notifier import Notifier
from tianshu.planner.planner import Planner
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.universe.router import ChallengerRouter


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
        """Managed ingress executes under a lease, then outbox delivery audits and notifies."""

        tracked_events = (
            "edict.submitted",
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

        planner = Planner(event_bus=event_bus, storage=storage, config_manager=config_manager)
        executor = Executor(
            event_bus=event_bus,
            storage=storage,
            config_manager=config_manager,
            hook_registry=hooks,
        )
        auditor = Auditor(event_bus=event_bus, storage=storage, config_manager=config_manager)
        notifier = Notifier(storage=storage)
        runner = ProductionRunRunner(planner, executor)
        fenced_completion = FencedRunCompletion(storage.unit_of_work, storage.attempt_repo)
        completer = ProductionAttemptCompleter(
            fenced_completion,
            storage.attempt_repo,
            runner,
        )
        challenger_router = ChallengerRouter(storage)
        dispatcher = RunDispatcher(
            storage.attempt_repo,
            runner,
            owner_id="test-full-chain",
            completer=completer,
            exit_cleanup=runner.discard_projection,
            challenger_router=challenger_router,
        )
        reconciler = RunReconciler(storage.attempt_repo, dispatcher)
        ingress = ManagedRunIngress(
            storage,
            reconciler,
            challenger_router=challenger_router,
        )
        executor.set_fenced_completion(fenced_completion)
        executor.set_managed_run_ingress(ingress)

        # Mock agent
        mock_agent = AsyncMock(spec=Agent)
        mock_agent.execute.return_value = AgentResult(
            status=TaskStatus.COMPLETED,
            summary="Done",
            result="Task completed",
            usage=UsageSummary(total_tokens=50),
        )
        executor.set_agent(mock_agent)

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

        edict = Edict(
            goal="test full chain",
            assigned_persona_id="bingbu",
            priority="urgent",
        )
        storage.save_edict(edict)

        started = await ingress.start(
            ManagedRunCommand(
                edict_id=edict.id,
                idempotency_key="test-full-chain",
                instruction=edict.goal,
                event_type="edict.submitted",
                event_payload={"goal": edict.goal},
            )
        )
        await dispatcher.wait_until_idle()
        outbox = OutboxDispatcher(
            OutboxRepository(storage.unit_of_work),
            event_bus,
            owner_id="test-full-chain-outbox",
        )
        while await outbox.drain_once():
            pass

        assert len(events_seen) == len(tracked_events)
        assert set(events_seen) == set(tracked_events)

        persisted_events = [
            event["event_type"]
            for event in storage.get_events(edict.id)
            if event["event_type"] in tracked_events
        ]
        assert len(persisted_events) == len(tracked_events)
        assert set(persisted_events) == set(tracked_events)

        memorials = storage.list_memorials_by_edict(edict.id)
        assert [memorial.id for memorial in memorials] == [started.memorial.id]
        assert memorials[0].status is TaskStatus.COMPLETED
        assert memorials[0].audit is not None

        await dispatcher.stop()
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
