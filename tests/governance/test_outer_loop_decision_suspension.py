"""Durable outer-loop L3 decision suspension contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import tianshu.executor.approvals as approvals_module
from tianshu.application.continuation_recovery import ContinuationRecoveryService
from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.managed_tools import ManagedRunSuspended
from tianshu.executor.orchestrator.human_decision import HumanDecision
from tianshu.executor.orchestrator.loop import OrchestratorContext, _escalate_to_human
from tianshu.executor.orchestrator.state import (
    ChecksResult,
    CriticResult,
    IterationRecord,
    OuterLoopState,
)
from tianshu.governance.decision_service import DecisionConflict, DecisionService
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.decision import DecisionKind, ResolveDecisionCommand
from tianshu.models.events import EventEnvelope
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.run_state import OuterLoopContinuationV1, RunPhase
from tianshu.storage import Storage

_NOW = datetime(2026, 7, 15, 14, tzinfo=UTC)


def _seed(storage) -> tuple[Edict, Memorial]:
    edict = Edict(id="edict-outer-durable", goal="recover outer loop")
    memorial = Memorial(
        id="memorial-outer-durable",
        edict_id=edict.id,
        status=TaskStatus.RUNNING,
        usage=UsageSummary(
            prompt_tokens=13,
            completion_tokens=5,
            total_tokens=18,
            cache_read_tokens=3,
            cost_cny=0.42,
            actual_model="model-a",
            upstream_provider="provider-a",
        ),
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    return edict, memorial


def _state(edict_id: str) -> OuterLoopState:
    first = IterationRecord(
        iteration=0,
        level="L0",
        actor_output="draft one",
        checks_result=ChecksResult(all_passed=False),
        critic_result=CriticResult(
            verdict="fail",
            issue_class="factual_error",
            feedback="verify the source",
            usage=UsageSummary(total_tokens=4, cost_cny=0.1),
        ),
        started_at=_NOW,
        finished_at=_NOW,
        cost_cny=0.15,
    )
    second = IterationRecord(
        iteration=1,
        level="L2",
        actor_output="best output",
        checks_result=ChecksResult(all_passed=False),
        critic_result=CriticResult(
            verdict="fail",
            issue_class="factual_error",
            feedback="Bearer sk-outer-secret must not persist",
            usage=UsageSummary(total_tokens=6, cost_cny=0.2),
        ),
        started_at=_NOW,
        finished_at=_NOW,
        cost_cny=0.27,
    )
    return OuterLoopState(
        edict_id=edict_id,
        iteration=2,
        current_level="L3",
        same_issue_streak=2,
        last_critic_issue_class="factual_error",
        l1_rounds_used=1,
        l2_rounds_used=1,
        consultation_advice="consult the evidence",
        steer_note="prefer primary sources",
        history=(first, second),
        total_cost_cny=0.42,
    )


def _manager(storage) -> tuple[ApprovalManager, DecisionService]:
    service = DecisionService(storage, clock=lambda: _NOW)
    manager = ApprovalManager(
        event_bus=EventBus(),
        storage=storage,
        decision_service=service,
        clock=lambda: _NOW,
    )
    return manager, service


def _claim(storage, memorial: Memorial, *, max_attempts: int = 3) -> AttemptAuthority:
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id=memorial.id,
            available_at=_NOW,
            max_attempts=max_attempts,
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id=memorial.id,
        owner_id="worker-outer",
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


def _auth(identity: str = "user:reviewer") -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=identity,
            kind="human",
            display_name=identity.rsplit(":", 1)[-1],
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id=f"outer:{identity}",
    )


def _resolve_continue(*, feedback: str = "continue with evidence") -> ResolveDecisionCommand:
    return ResolveDecisionCommand(
        action="continue",
        reason="reviewed",
        payload={"schema_version": 1, "feedback": feedback},
        expected_version=1,
    )


def _request(manager, edict, memorial, state: OuterLoopState):
    return manager.request_outer_loop_decision(
        edict=edict,
        memorial=memorial,
        state=state,
        checkpoint_ref=f"outer-loop:{edict.id}",
        side_effect_cursor=4,
        timeout_seconds=60,
    )


def test_outer_loop_request_persists_complete_reconstruction_state_atomically(storage) -> None:
    edict, memorial = _seed(storage)
    manager, _ = _manager(storage)

    request = _request(manager, edict, memorial, _state(edict.id))

    assert request.kind is DecisionKind.OUTER_LOOP
    assert request.request_key == "outer-loop:L3:2"
    assert request.payload["iteration"] == 2
    assert request.payload["level"] == "L3"
    assert request.payload["best_output"] == "best output"
    assert request.payload["steer"] == "prefer primary sources"
    assert len(request.payload["history"]) == 2
    with storage.unit_of_work() as unit_of_work:
        saved = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert saved is not None and saved.phase is RunPhase.WAITING_DECISION
    assert isinstance(saved.continuation, OuterLoopContinuationV1)
    continuation = saved.continuation
    assert continuation.pending_decision_id == request.decision_request_id
    assert continuation.iteration == 2
    assert continuation.level == "L3"
    assert continuation.best_output == "best output"
    assert continuation.feedback == "[REDACTED]"
    assert continuation.steer == "prefer primary sources"
    assert len(continuation.history) == 2
    assert continuation.same_issue_streak == 2
    assert continuation.l1_rounds_used == 1
    assert continuation.l2_rounds_used == 1
    assert continuation.consultation_advice == "consult the evidence"
    assert continuation.usage.total_tokens == 18
    assert float(continuation.total_cost_cny) == 0.42
    assert continuation.checkpoint_ref == f"outer-loop:{edict.id}"
    assert continuation.side_effect_cursor == 4
    durable_json = request.model_dump_json() + saved.model_dump_json()
    assert "sk-outer-secret" not in durable_json


def test_outer_loop_durable_decision_is_l3_only(storage) -> None:
    edict, memorial = _seed(storage)
    manager, _ = _manager(storage)

    with pytest.raises(ValueError, match="invalid outer-loop state"):
        _request(
            manager,
            edict,
            memorial,
            replace(_state(edict.id), current_level="L2"),
        )

    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 0  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM run_states").fetchone()[0] == 0  # noqa: SLF001


async def test_outer_loop_pending_survives_restart_and_returns_human_decision(
    tmp_path: Path,
) -> None:
    path = tmp_path / "outer-loop-restart.db"
    first = Storage(str(path))
    first.init_db()
    edict, memorial = _seed(first)
    manager, _ = _manager(first)
    requested = _request(manager, edict, memorial, _state(edict.id))
    first.close()

    restarted = Storage(str(path))
    restarted.init_db()
    try:
        restarted_manager, service = _manager(restarted)
        pending = restarted_manager.list_pending_outer_loop()
        assert pending == [
            {
                "decision_request_id": requested.decision_request_id,
                "edict_id": edict.id,
                **requested.payload,
            }
        ]
        service.resolve(
            requested.decision_request_id,
            _resolve_continue(),
            auth=_auth(),
        )

        decision = await restarted_manager.wait_for_outer_loop_decision(
            requested.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )

        assert decision == HumanDecision(
            action="continue",
            feedback="continue with evidence",
        )
        with restarted.unit_of_work() as unit_of_work:
            resumed = restarted.run_state_repo.load(unit_of_work.connection, memorial.id)
            unit_of_work.commit()
        assert resumed is not None and resumed.phase is RunPhase.EXECUTING
        assert resumed.continuation.pending_decision_id is None
        assert resumed.continuation.resolved_decision_id == requested.decision_request_id
    finally:
        restarted.close()


async def test_outer_resolution_event_records_resume_intent_without_live_wait(storage) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    requested = _request(manager, edict, memorial, _state(edict.id))
    service.resolve(requested.decision_request_id, _resolve_continue(), auth=_auth())

    await manager.handle_decision_resolved(
        EventEnvelope(
            event_type="decision.resolved",
            edict_id=edict.id,
            memorial_id=memorial.id,
            payload={
                "decision_request_id": requested.decision_request_id,
                "kind": DecisionKind.OUTER_LOOP.value,
                "action": "continue",
            },
        )
    )

    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.EXECUTING
    assert state.continuation.resolved_decision_id == requested.decision_request_id


async def test_same_outer_escalation_replays_and_later_escalation_gets_new_identity(
    storage,
) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    state = _state(edict.id)
    first = _request(manager, edict, memorial, state)

    assert _request(manager, edict, memorial, state) == first
    service.resolve(first.decision_request_id, _resolve_continue(), auth=_auth())
    assert (
        await manager.wait_for_outer_loop_decision(
            first.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )
        is not None
    )

    later_state = replace(state, iteration=3, same_issue_streak=3)
    later = _request(manager, edict, memorial, later_state)

    assert later.decision_request_id != first.decision_request_id
    assert later.request_key == "outer-loop:L3:3"
    assert _request(manager, edict, memorial, later_state) == later
    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 2  # noqa: SLF001
    with storage.unit_of_work() as unit_of_work:
        waiting = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert waiting is not None and waiting.phase is RunPhase.WAITING_DECISION
    assert waiting.version == 3
    assert waiting.continuation.pending_decision_id == later.decision_request_id


@pytest.mark.parametrize("fault_side", ("decision", "run_state"))
def test_outer_loop_decision_and_run_state_rollback_together(
    storage,
    monkeypatch,
    fault_side: str,
) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)

    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(f"{fault_side} fault")

    repository = service._repository if fault_side == "decision" else service._run_states
    method = "add_or_get" if fault_side == "decision" else "create"
    monkeypatch.setattr(repository, method, fail)

    with pytest.raises(RuntimeError, match=f"{fault_side} fault"):
        _request(manager, edict, memorial, _state(edict.id))

    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 0  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM run_states").fetchone()[0] == 0  # noqa: SLF001


@pytest.mark.parametrize("max_attempts", (1, 3))
async def test_managed_escalation_suspends_before_unwind_without_live_poll(
    storage,
    monkeypatch,
    max_attempts: int,
) -> None:
    edict, memorial = _seed(storage)
    edict = edict.model_copy(update={"acceptance": AcceptanceCriteria(deadline_seconds=30)})
    authority = _claim(storage, memorial, max_attempts=max_attempts)
    manager, _ = _manager(storage)
    wait = AsyncMock(side_effect=AssertionError("managed L3 must not live-poll"))
    monkeypatch.setattr(manager, "wait_for_outer_loop_decision", wait)
    ctx = OrchestratorContext(
        agent=MagicMock(),
        storage=storage,
        bus=EventBus(),
        actor_llm=MagicMock(),
        critic_llm=MagicMock(),
        approvals=manager,
        attempt_authority=authority,
    )

    with pytest.raises(ManagedRunSuspended):
        await _escalate_to_human(_state(edict.id), edict, ctx, memorial)

    wait.assert_not_awaited()
    row = storage._conn.execute(  # noqa: SLF001
        "SELECT status, fencing_token FROM execution_attempts WHERE attempt_id=?",
        (authority.attempt_id,),
    ).fetchone()
    assert row is not None and tuple(row) == ("suspended", authority.fencing_token)
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM decision_requests WHERE status='pending'"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='decision.requested'"
        ).fetchone()[0]
        == 1
    )
    with storage.unit_of_work() as unit_of_work:
        run_state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert run_state is not None and run_state.phase is RunPhase.WAITING_DECISION
    assert storage.attempt_repo.list_dispatchable_memorial_ids(now=_NOW, limit=10) == ()


@pytest.mark.parametrize(
    "boundary",
    ("after_decision", "after_run_state", "after_attempt_suspend", "after_outbox"),
)
def test_managed_outer_suspension_fault_rolls_back_whole_uow(
    storage,
    boundary: str,
) -> None:
    edict, memorial = _seed(storage)
    authority = _claim(storage, memorial)

    def fail(current: str) -> None:
        if current == boundary:
            raise RuntimeError(f"injected {boundary}")

    service = DecisionService(storage, clock=lambda: _NOW)
    manager = ApprovalManager(
        event_bus=EventBus(),
        storage=storage,
        decision_service=service,
        clock=lambda: _NOW,
        boundary_hook=fail,
    )

    with pytest.raises(RuntimeError, match=boundary):
        manager.request_outer_loop_decision(
            edict=edict,
            memorial=memorial,
            state=_state(edict.id),
            checkpoint_ref=f"outer-loop:{edict.id}",
            side_effect_cursor=4,
            timeout_seconds=60,
            authority=authority,
        )

    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 0  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM run_states").fetchone()[0] == 0  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == 0  # noqa: SLF001
    attempt = storage._conn.execute(  # noqa: SLF001
        "SELECT status, owner_id, fencing_token FROM execution_attempts WHERE attempt_id=?",
        (authority.attempt_id,),
    ).fetchone()
    assert attempt is not None and tuple(attempt) == (
        "claimed",
        authority.owner_id,
        authority.fencing_token,
    )


@pytest.mark.parametrize("max_attempts", (1, 3))
async def test_real_l3_suspension_survives_restart_and_resumes_once(
    tmp_path: Path,
    max_attempts: int,
) -> None:
    path = tmp_path / f"real-l3-{max_attempts}.db"
    first = Storage(str(path))
    first.init_db()
    edict, memorial = _seed(first)
    authority = _claim(first, memorial, max_attempts=max_attempts)
    manager, _ = _manager(first)
    request = manager.request_outer_loop_decision(
        edict=edict,
        memorial=memorial,
        state=_state(edict.id),
        checkpoint_ref=f"outer-loop:{edict.id}",
        side_effect_cursor=4,
        timeout_seconds=60,
        authority=authority,
    )
    first.close()

    restarted = Storage(str(path))
    restarted.init_db()
    try:
        service = DecisionService(restarted, clock=lambda: _NOW)
        service.resolve(
            request.decision_request_id,
            _resolve_continue(),
            auth=_auth(),
        )
        event = EventEnvelope(
            event_id=f"{request.decision_request_id}:test-resolved",
            event_type="decision.resolved",
            edict_id=edict.id,
            memorial_id=memorial.id,
            producer="test",
            timestamp=_NOW,
            payload={
                "schema_version": 1,
                "decision_request_id": request.decision_request_id,
                "kind": DecisionKind.OUTER_LOOP.value,
                "action": "continue",
            },
        )
        recovery = ContinuationRecoveryService(restarted, clock=lambda: _NOW)
        assert await recovery.handle_decision_resolved(event) is True
        assert await recovery.handle_decision_resolved(event) is False
        resumed = restarted.attempt_repo.claim(
            memorial_id=memorial.id,
            owner_id="worker-restarted",
            now=_NOW,
            lease_seconds=60,
        )
        assert resumed is not None and resumed.fencing_token > authority.fencing_token
        outcome = AttemptOutcomeV1(
            disposition=AttemptDisposition.SUCCEEDED,
            completed_at=_NOW,
        )
        assert not restarted.attempt_repo.complete(
            attempt_id=authority.attempt_id,
            owner_id=authority.owner_id,
            fencing_token=authority.fencing_token,
            outcome=outcome,
        )
        assert (
            restarted._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='execution.resume.requested'"
            ).fetchone()[0]
            == 1
        )
    finally:
        restarted.close()


async def test_escalation_persists_real_state_before_notification_and_wait(storage) -> None:
    edict, memorial = _seed(storage)
    edict = edict.model_copy(update={"acceptance": AcceptanceCriteria(deadline_seconds=30)})
    state = _state(edict.id)
    calls: list[tuple[str, object]] = []

    class CapturingApprovals:
        def request_outer_loop_decision(self, **kwargs):
            calls.append(("request", kwargs))
            return SimpleNamespace(decision_request_id="decision-outer-captured")

        async def wait_for_outer_loop_decision(self, decision_request_id, **kwargs):
            calls.append(("wait", (decision_request_id, kwargs)))
            return HumanDecision(action="continue", feedback="approved")

    notifier = MagicMock()

    async def notify(**kwargs):
        calls.append(("notify", kwargs))

    notifier.notify = AsyncMock(side_effect=notify)
    ctx = OrchestratorContext(
        agent=MagicMock(),
        storage=storage,
        bus=EventBus(),
        actor_llm=MagicMock(),
        critic_llm=MagicMock(),
        notifier=notifier,
        approvals=CapturingApprovals(),
    )

    decision = await _escalate_to_human(state, edict, ctx, memorial)

    assert decision == HumanDecision(action="continue", feedback="approved")
    assert [kind for kind, _ in calls] == ["request", "notify", "wait"]
    request_kwargs = calls[0][1]
    assert isinstance(request_kwargs, dict)
    assert request_kwargs["state"] is state
    assert request_kwargs["edict"] is edict
    assert request_kwargs["memorial"] is memorial
    assert request_kwargs["checkpoint_ref"] == f"outer-loop:{edict.id}"
    assert storage.get_outer_loop_checkpoint(edict.id) is not None


async def test_post_request_audit_and_notifier_failures_do_not_bypass_durable_wait(
    storage,
    monkeypatch,
) -> None:
    edict, memorial = _seed(storage)
    edict = edict.model_copy(update={"acceptance": AcceptanceCriteria(deadline_seconds=30)})
    calls: list[str] = []

    class Approvals:
        def request_outer_loop_decision(self, **kwargs):
            del kwargs
            calls.append("request")
            return SimpleNamespace(decision_request_id="decision-durable-first")

        async def wait_for_outer_loop_decision(self, decision_request_id, **kwargs):
            del kwargs
            assert decision_request_id == "decision-durable-first"
            calls.append("wait")
            return HumanDecision(action="continue", feedback="durable authority won")

    audit_calls = 0

    async def flaky_audit(*args, **kwargs):
        nonlocal audit_calls
        del args, kwargs
        audit_calls += 1
        if audit_calls == 1:
            raise RuntimeError("audit projection failed")

    monkeypatch.setattr("tianshu.executor.orchestrator.loop.emit_audit", flaky_audit)
    notifier = MagicMock()
    notifier.notify = AsyncMock(side_effect=RuntimeError("notification failed"))
    ctx = OrchestratorContext(
        agent=MagicMock(),
        storage=storage,
        bus=EventBus(),
        actor_llm=MagicMock(),
        critic_llm=MagicMock(),
        notifier=notifier,
        approvals=Approvals(),
    )

    decision = await _escalate_to_human(_state(edict.id), edict, ctx, memorial)

    assert decision == HumanDecision(action="continue", feedback="durable authority won")
    assert calls == ["request", "wait"]


async def test_outer_request_failure_aborts_even_when_timeout_policy_is_best_effort(
    storage,
) -> None:
    edict, memorial = _seed(storage)
    edict = edict.model_copy(
        update={
            "acceptance": AcceptanceCriteria(
                deadline_seconds=30,
                on_approval_timeout="best_effort",
            )
        }
    )

    class FailingApprovals:
        def request_outer_loop_decision(self, **kwargs):
            del kwargs
            raise RuntimeError("durable request failed")

        async def wait_for_outer_loop_decision(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("must not wait without a durable request")

    notifier = MagicMock()
    notifier.notify = AsyncMock()
    ctx = OrchestratorContext(
        agent=MagicMock(),
        storage=storage,
        bus=EventBus(),
        actor_llm=MagicMock(),
        critic_llm=MagicMock(),
        notifier=notifier,
        approvals=FailingApprovals(),
    )

    decision = await _escalate_to_human(_state(edict.id), edict, ctx, memorial)

    assert decision.action == "abort"
    notifier.notify.assert_not_awaited()


def test_outer_loop_two_resolvers_have_one_durable_winner(tmp_path: Path) -> None:
    path = tmp_path / "outer-loop-resolver-race.db"
    setup = Storage(str(path))
    setup.init_db()
    edict, memorial = _seed(setup)
    manager, _ = _manager(setup)
    requested = _request(manager, edict, memorial, _state(edict.id))
    setup.close()
    barrier = Barrier(2)

    def resolve(actor: str) -> tuple[str, str]:
        storage = Storage(str(path))
        storage.init_db()
        try:
            service = DecisionService(storage, clock=lambda: _NOW)
            barrier.wait()
            try:
                resolution = service.resolve(
                    requested.decision_request_id,
                    _resolve_continue(),
                    auth=_auth(actor),
                )
            except DecisionConflict as exc:
                return "conflict", exc.code
            return "resolved", resolution.actor_principal_id
        finally:
            storage.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(resolve, ("user:first", "user:second")))

    assert len([result for result in results if result[0] == "resolved"]) == 1
    assert [result[1] for result in results if result[0] == "conflict"] == [
        "decision_already_resolved"
    ]
    verify = Storage(str(path))
    verify.init_db()
    try:
        assert (
            verify._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM decision_resolutions"
            ).fetchone()[0]
            == 1
        )
        assert (
            verify._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'decision.resolved'"
            ).fetchone()[0]
            == 1
        )
    finally:
        verify.close()


async def test_outer_loop_timeout_race_observes_concurrent_resolution(
    storage,
    monkeypatch,
) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    requested = _request(manager, edict, memorial, _state(edict.id))

    async def resolve_at_timeout(awaitable, *, timeout):
        del timeout
        awaitable.close()
        service.resolve(
            requested.decision_request_id,
            _resolve_continue(feedback="won timeout race"),
            auth=_auth(),
        )
        raise TimeoutError

    monkeypatch.setattr(approvals_module.asyncio, "wait_for", resolve_at_timeout)

    decision = await manager.wait_for_outer_loop_decision(requested.decision_request_id)

    assert decision == HumanDecision(action="continue", feedback="won timeout race")
    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.EXECUTING
    assert state.continuation.resolved_decision_id == requested.decision_request_id
