"""Crash recovery through the actual managed production execution chain."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tianshu.application.continuation_recovery import ContinuationRecoveryService
from tianshu.application.fenced_run_completion import FencedRunCompletion
from tianshu.application.run_dispatcher import AttemptAuthority, RunDispatcher
from tianshu.application.run_execution import (
    ProductionAttemptCompleter,
    ProductionRunRunner,
)
from tianshu.application.run_reconciler import RunReconciler
from tianshu.bus.event_bus import EventBus
from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState
from tianshu.executor.agent import Agent
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.executor import Executor
from tianshu.executor.managed_tools import ManagedToolEffectExecutor
from tianshu.executor.orchestrator.loop import OrchestratorContext
from tianshu.executor.side_effects import ProviderEffectReceipt
from tianshu.governance.decision_service import DecisionService
from tianshu.kernel.ambient import get_current_tool_invocation_id
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.acceptance import AcceptanceCriteria, CriticSpec, EscalationSpec
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.decision import ResolveDecisionCommand
from tianshu.models.events import EventEnvelope
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.side_effect import SideEffectSemantics
from tianshu.planner.planner import Planner
from tianshu.skills.loader import SkillsLoader
from tianshu.storage import Storage
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ok_result

_NOW = datetime(2026, 7, 17, 8, tzinfo=UTC)


class _CrashAfterProvider(BaseException):
    """Model process loss, which application-level exception handlers cannot absorb."""


class _ReceiptProvider:
    def __init__(self) -> None:
        self.effective_count = 0
        self.lookup_count = 0
        self._receipts: dict[str, ProviderEffectReceipt] = {}

    async def invoke(self, value: str) -> ToolResult:
        assert value == "safe"
        key = get_current_tool_invocation_id()
        assert key is not None
        self.effective_count += 1
        result = ok_result("accepted")
        self._receipts[key] = ProviderEffectReceipt(
            provider_receipt_id=f"provider-{self.effective_count}",
            result_metadata={"content": result.content, "is_error": result.is_error},
            effective_at=_NOW + timedelta(seconds=1),
        )
        return result

    async def lookup(self, key: str) -> ProviderEffectReceipt | None:
        self.lookup_count += 1
        return self._receipts.get(key)


def _open(path: Path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    return storage


def _config(storage: Storage) -> ConfigManager:
    return ConfigManager(
        LLMConfigState(
            name="test-model",
            model="test-model",
            api_key="test-key",
            api_base="http://provider.invalid",
        ),
        agent_config=AgentConfigState(agent_max_iterations=2, agent_timeout_seconds=30),
        storage=storage,
    )


def _claim_initial(storage: Storage) -> AttemptAuthority:
    edict = Edict(
        id="edict-production-recovery",
        goal="send once",
        assigned_persona_id="native-test",
    )
    edict.runtime.max_iterations = 2
    storage.save_edict(edict)
    storage.save_memorial(
        Memorial(
            id="memorial-production-recovery",
            edict_id=edict.id,
            status=TaskStatus.RUNNING,
        )
    )
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="memorial-production-recovery",
            available_at=_NOW,
            max_attempts=2,
        )
        unit_of_work.commit()
    return _claim(storage, owner_id="worker-origin", now=_NOW)


def _claim(storage: Storage, *, owner_id: str, now: datetime) -> AttemptAuthority:
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-production-recovery",
        owner_id=owner_id,
        now=now,
        lease_seconds=60,
    )
    assert claimed is not None and claimed.owner_id is not None
    return AttemptAuthority(
        attempt_id=claimed.attempt_id,
        memorial_id=claimed.memorial_id,
        owner_id=claimed.owner_id,
        fencing_token=claimed.fencing_token,
    )


def _runner(
    storage: Storage,
    skills_dir: Path,
    provider: _ReceiptProvider,
    *,
    now: datetime,
    boundary_hook=None,
) -> ProductionRunRunner:
    config = _config(storage)
    bus = EventBus()
    hooks = HookRegistry()
    registry = ToolRegistry()
    registry.register(
        "managed_send",
        provider.invoke,
        ToolDefinition(
            name="managed_send",
            description="receipt-queryable provider",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            tier=3,
            side_effect=True,
            managed_effect_semantics=SideEffectSemantics.RECEIPT_LOOKUP,
        ),
    )
    registry.set_managed_receipt_lookup("managed_send", provider.lookup)
    registry.set_managed_effect_executor(
        ManagedToolEffectExecutor(
            storage,
            DecisionService(storage, clock=lambda: now),
            clock=lambda: now,
            boundary_hook=boundary_hook,
        )
    )
    agent = Agent(
        config_manager=config,
        tools=registry,
        skills=SkillsLoader(builtin_dir=skills_dir, char_budget=0),
        hook_registry=hooks,
    )
    executor = Executor(bus, storage, config, hooks)
    executor.set_agent(agent)
    planner = Planner(bus, storage, config, tool_registry=registry)
    return ProductionRunRunner(planner, executor, clock=lambda: now)


def _tool_response() -> MagicMock:
    return MagicMock(
        content="send",
        reasoning_content=None,
        tool_calls=[
            {
                "id": "stable-provider-call",
                "name": "managed_send",
                "args": '{"value":"safe"}',
            }
        ],
        usage=UsageSummary(),
        finish_reason="tool_calls",
    )


def _final_response() -> MagicMock:
    return MagicMock(
        content="done",
        reasoning_content=None,
        tool_calls=None,
        usage=UsageSummary(),
        finish_reason="stop",
    )


@pytest.mark.asyncio
async def test_actual_production_chain_recovers_receipt_after_reopen_without_duplicate_effect(
    tmp_path: Path,
) -> None:
    path = tmp_path / "production-recovery.db"
    provider = _ReceiptProvider()
    storage = _open(path)
    original = _claim_initial(storage)

    def crash(boundary: str) -> None:
        if boundary == "after_provider":
            raise _CrashAfterProvider

    original_runner = _runner(
        storage,
        tmp_path / "skills-origin",
        provider,
        now=_NOW + timedelta(seconds=2),
        boundary_hook=crash,
    )
    client = AsyncMock(chat=AsyncMock(return_value=_tool_response()))
    with (
        patch("tianshu.executor.agent.LLMClient", return_value=client),
        pytest.raises(_CrashAfterProvider),
    ):
        await original_runner(original)
    assert provider.effective_count == 1
    storage.close()

    reopened = _open(path)
    try:
        recovered = _claim(
            reopened,
            owner_id="worker-recovered",
            now=_NOW + timedelta(seconds=61),
        )
        assert recovered.attempt_id != original.attempt_id
        assert recovered.fencing_token > original.fencing_token
        recovered_runner = _runner(
            reopened,
            tmp_path / "skills-recovered",
            provider,
            now=_NOW + timedelta(seconds=62),
        )
        client = AsyncMock(chat=AsyncMock(side_effect=[_tool_response(), _final_response()]))
        with patch("tianshu.executor.agent.LLMClient", return_value=client):
            result = await recovered_runner(recovered)

        assert result.disposition is AttemptDisposition.SUCCEEDED
        assert provider.effective_count == 1
        assert provider.lookup_count == 2
        row = reopened._conn.execute(  # noqa: SLF001
            """
            SELECT attempt_id, owner_id, fencing_token,
                   receipt_attempt_id, receipt_owner_id, receipt_fencing_token, status
            FROM side_effect_journal
            """
        ).fetchone()
        assert row is not None
        assert tuple(row) == (
            original.attempt_id,
            original.owner_id,
            original.fencing_token,
            recovered.attempt_id,
            recovered.owner_id,
            recovered.fencing_token,
            "receipted",
        )
        completer = ProductionAttemptCompleter(
            FencedRunCompletion(reopened.unit_of_work, reopened.attempt_repo),
            reopened.attempt_repo,
            recovered_runner,
        )
        assert completer(
            recovered,
            AttemptOutcomeV1(
                disposition=AttemptDisposition.SUCCEEDED,
                completed_at=_NOW + timedelta(seconds=63),
            ),
        )
    finally:
        reopened.close()


def _seed_l3(storage: Storage, *, max_attempts: int) -> AttemptAuthority:
    edict = Edict(
        id="edict-production-l3",
        goal="produce a reviewed draft",
        assigned_persona_id="native-test",
        acceptance=AcceptanceCriteria(
            critic=CriticSpec(same_issue_threshold=1),
            escalation=EscalationSpec(enabled_levels=["L3"]),
            max_outer_iterations=3,
            deadline_seconds=60,
        ),
    )
    edict.runtime.max_iterations = 1
    storage.save_edict(edict)
    storage.save_memorial(
        Memorial(
            id="memorial-production-l3",
            edict_id=edict.id,
            status=TaskStatus.RUNNING,
        )
    )
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="memorial-production-l3",
            available_at=_NOW,
            max_attempts=max_attempts,
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-production-l3",
        owner_id="worker-l3-origin",
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


def _l3_runner(storage: Storage, skills_dir: Path, *, now: datetime) -> ProductionRunRunner:
    config = _config(storage)
    bus = EventBus()
    hooks = HookRegistry()
    registry = ToolRegistry()
    agent = Agent(
        config_manager=config,
        tools=registry,
        skills=SkillsLoader(builtin_dir=skills_dir, char_budget=0),
        hook_registry=hooks,
    )
    executor = Executor(bus, storage, config, hooks)
    executor.set_agent(agent)
    decisions = DecisionService(storage, clock=lambda: now)
    approvals = ApprovalManager(
        event_bus=bus,
        storage=storage,
        decision_service=decisions,
        clock=lambda: now,
    )
    critic = AsyncMock()
    critic.chat.return_value = MagicMock(
        content=(
            '{"verdict":"fail","issue_class":"factual_error","feedback":"needs human review"}'
        ),
        usage=UsageSummary(),
    )
    executor.set_orchestrator_context(
        OrchestratorContext(
            agent=agent,
            storage=storage,
            bus=bus,
            actor_llm=AsyncMock(),
            critic_llm=critic,
            approvals=approvals,
        )
    )
    planner = Planner(bus, storage, config, tool_registry=registry)
    return ProductionRunRunner(planner, executor, clock=lambda: now)


def _reviewer_auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="user:reviewer",
            kind="human",
            display_name="Reviewer",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="production-l3:test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("max_attempts", (1, 3))
async def test_actual_l3_close_reopen_resolution_and_reconciler_redispatch(
    tmp_path: Path,
    max_attempts: int,
) -> None:
    path = tmp_path / f"production-l3-{max_attempts}.db"
    storage = _open(path)
    stale = _seed_l3(storage, max_attempts=max_attempts)
    runner = _l3_runner(
        storage,
        tmp_path / f"skills-l3-{max_attempts}",
        now=_NOW + timedelta(seconds=2),
    )
    actor_client = AsyncMock(
        chat=AsyncMock(
            return_value=MagicMock(
                content="draft requiring review",
                reasoning_content=None,
                tool_calls=None,
                usage=UsageSummary(),
                finish_reason="stop",
            )
        )
    )
    with patch("tianshu.executor.agent.LLMClient", return_value=actor_client):
        suspended = await runner(stale)

    assert suspended.disposition is AttemptDisposition.SUSPENDED
    with storage.unit_of_work() as unit_of_work:
        waiting = storage.run_state_repo.load(unit_of_work.connection, stale.memorial_id)
        unit_of_work.commit()
    assert waiting is not None
    assert waiting.continuation.plan_continuation is not None
    assert len(waiting.continuation.plan_continuation.plan_revisions) == 1
    pending = storage._conn.execute(  # noqa: SLF001
        "SELECT decision_request_id FROM decision_requests WHERE status='pending'"
    ).fetchone()
    assert pending is not None
    decision_id = str(pending[0])
    storage.close()

    reopened = _open(path)
    try:
        decisions = DecisionService(reopened, clock=lambda: _NOW + timedelta(seconds=4))
        decisions.resolve(
            decision_id,
            ResolveDecisionCommand(
                action="accept_as_is",
                reason="approved durable output",
                payload={"schema_version": 1},
                expected_version=1,
            ),
            auth=_reviewer_auth(),
        )
        event = EventEnvelope(
            event_id=f"{decision_id}:resolved:test",
            event_type="decision.resolved",
            edict_id="edict-production-l3",
            memorial_id="memorial-production-l3",
            producer="test",
            timestamp=_NOW + timedelta(seconds=4),
            payload={
                "schema_version": 1,
                "decision_request_id": decision_id,
                "kind": "outer_loop",
                "action": "accept_as_is",
                "request_version": 2,
                "correlation_id": "production-l3:test",
            },
        )
        recovery = ContinuationRecoveryService(
            reopened,
            clock=lambda: _NOW + timedelta(seconds=5),
        )
        assert await recovery.handle_decision_resolved(event) is True
        assert await recovery.handle_decision_resolved(event) is False

        recovered_runner = _l3_runner(
            reopened,
            tmp_path / f"skills-l3-recovered-{max_attempts}",
            now=_NOW + timedelta(seconds=6),
        )
        completer = ProductionAttemptCompleter(
            FencedRunCompletion(reopened.unit_of_work, reopened.attempt_repo),
            reopened.attempt_repo,
            recovered_runner,
        )
        dispatcher = RunDispatcher(
            reopened.attempt_repo,
            recovered_runner,
            owner_id="worker-l3-recovered",
            completer=completer,
            exit_cleanup=recovered_runner.discard_projection,
            clock=lambda: _NOW + timedelta(seconds=6),
            lease_seconds=60,
            heartbeat_interval_seconds=10,
        )
        reconciler = RunReconciler(
            reopened.attempt_repo,
            dispatcher,
            clock=lambda: _NOW + timedelta(seconds=6),
        )

        assert await reconciler.reconcile_once() == 1
        await dispatcher.wait_until_idle()

        attempt = reopened._conn.execute(  # noqa: SLF001
            "SELECT status, owner_id, fencing_token FROM execution_attempts WHERE attempt_id=?",
            (stale.attempt_id,),
        ).fetchone()
        assert attempt is not None
        assert tuple(attempt) == ("succeeded", None, stale.fencing_token + 1)
        assert not reopened.attempt_repo.complete(
            attempt_id=stale.attempt_id,
            owner_id=stale.owner_id,
            fencing_token=stale.fencing_token,
            outcome=AttemptOutcomeV1(
                disposition=AttemptDisposition.SUCCEEDED,
                completed_at=_NOW + timedelta(seconds=7),
            ),
        )
        memorial = reopened.get_memorial("memorial-production-l3")
        assert memorial is not None and memorial.status is TaskStatus.COMPLETED
        assert memorial.final_output == "draft requiring review"
        assert (
            reopened._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM decision_requests WHERE memorial_id=?",
                (memorial.id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            reopened._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outer_loop_iterations WHERE edict_id=?",
                (memorial.edict_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        reopened.close()
