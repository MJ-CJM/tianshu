"""Atomic durable suspension contracts for policy-governed tool calls."""

from datetime import UTC, datetime

import pytest

import tianshu.executor.approvals as approvals_module
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.policy_hook import PolicyHook
from tianshu.executor.silijian import Silijian
from tianshu.governance.decision_service import DecisionConflict, DecisionService
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.decision import DecisionKind, ResolveDecisionCommand
from tianshu.models.decree import Decree
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.run_state import PersistedChatMessageV1, RunPhase
from tianshu.storage import Storage
from tianshu.tools.policy import PolicyContext, PolicyDecision, PolicyEngine
from tianshu.tools.types import ToolTier

_NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def _seed(storage: Storage) -> tuple[Edict, Memorial]:
    edict = Edict(id="edict-tool-suspension", goal="write a governed file")
    memorial = Memorial(
        id="memorial-tool-suspension",
        edict_id=edict.id,
        status=TaskStatus.RUNNING,
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    return edict, memorial


def _manager(storage: Storage) -> tuple[ApprovalManager, DecisionService]:
    service = DecisionService(storage, clock=lambda: _NOW)
    manager = ApprovalManager(
        event_bus=EventBus(),
        storage=storage,
        decision_service=service,
        clock=lambda: _NOW,
    )
    return manager, service


def _request_tool_decision(
    manager: ApprovalManager,
    edict: Edict,
    memorial: Memorial,
    *,
    invocation_id: str = "tool-call-stable-7",
    iteration: int = 3,
    tool_name: str = "write_file",
    tool_args: dict[str, object] | None = None,
    messages: list[dict] | None = None,
    usage: UsageSummary | None = None,
):
    secret = "sk-tool-suspension-secret"
    effective_arguments = tool_args or {
        "path": "README.md",
        "api_key": secret,
        "nested": {"authorization": f"Bearer {secret}"},
    }
    effective_messages = messages or [
        {"role": "system", "content": "system prompt"},
        {
            "role": "assistant",
            "content": "calling tool",
            "reasoning_content": "reasoning that must survive restart",
            "tool_calls": [
                {
                    "id": invocation_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": effective_arguments,
                    },
                }
            ],
        },
    ]
    return manager.request_tool_decision(
        edict=edict,
        memorial=memorial,
        invocation_id=invocation_id,
        tool_name=tool_name,
        tool_args=effective_arguments,
        tool_tier="T1_WORKSPACE",
        policy_rule_id="approval_required",
        messages=effective_messages,
        iteration=iteration,
        usage=usage
        or UsageSummary(
            prompt_tokens=7,
            completion_tokens=3,
            total_tokens=10,
            cache_read_tokens=2,
            cost_cny=0.1,
            actual_model="reasoner-v1",
            upstream_provider="test-provider",
        ),
    )


def test_persisted_assistant_message_round_trips_tool_calls_and_reasoning() -> None:
    message = PersistedChatMessageV1(
        role="assistant",
        content="calling tool",
        name=None,
        tool_call_id=None,
        tool_calls=(
            {
                "id": "tool-call-stable-7",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": {"path": "README.md"},
                },
            },
        ),
        reasoning_content="reasoning that must survive restart",
    )

    restored = PersistedChatMessageV1.model_validate_json(message.model_dump_json())

    assert restored == message
    assert restored.tool_calls == message.tool_calls
    assert restored.reasoning_content == message.reasoning_content


def test_tool_decision_and_waiting_run_state_commit_with_shared_redacted_arguments(storage) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)

    request = _request_tool_decision(manager, edict, memorial)

    assert request.kind is DecisionKind.TOOL
    assert request.request_key == "tool-call-stable-7"
    assert request.requested_by == "system:policy-hook"
    record = service.get(request.decision_request_id)
    assert record is not None and record.request == request
    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None
    assert state.phase.value == "waiting_decision"
    continuation = state.continuation
    assert continuation.kind == "agent"
    assert continuation.pending_tool is not None
    assert continuation.pending_decision_id == request.decision_request_id
    assert continuation.pending_tool.tool_call_id == "tool-call-stable-7"
    assert continuation.pending_tool.arguments == request.payload["arguments"]
    assert (
        PolicyHook._summarize_args(
            {
                "path": "README.md",
                "api_key": "sk-tool-suspension-secret",
                "nested": {
                    "authorization": "Bearer sk-tool-suspension-secret",
                },
            }
        )
        == request.payload["arguments"]
    )
    assert continuation.iteration == 3
    assert continuation.usage.total_tokens == 10
    assistant = continuation.messages[-1]
    assert assistant.reasoning_content == "reasoning that must survive restart"
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0]["id"] == "tool-call-stable-7"
    durable_json = request.model_dump_json() + state.model_dump_json()
    assert "sk-tool-suspension-secret" not in durable_json
    assert durable_json.count("[REDACTED]") >= 3


def test_same_tool_suspension_is_idempotent_but_mismatched_continuation_fails_closed(
    storage,
) -> None:
    edict, memorial = _seed(storage)
    manager, _ = _manager(storage)

    first = _request_tool_decision(manager, edict, memorial)
    repeated = _request_tool_decision(manager, edict, memorial)

    assert repeated == first
    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 1  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM run_states").fetchone()[0] == 1  # noqa: SLF001
    with pytest.raises(DecisionConflict) as caught:
        _request_tool_decision(manager, edict, memorial, iteration=4)
    assert caught.value.code == "decision_run_state_conflict"


async def test_resolved_live_wait_returns_state_to_executing_before_next_tool_suspension(
    storage,
) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    first = _request_tool_decision(manager, edict, memorial)
    reviewer = AuthContext(
        principal=Principal(
            id="user:reviewer",
            kind="human",
            display_name="Reviewer",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="resolve:first-tool",
    )
    service.resolve(
        first.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="approved",
            payload={"schema_version": 1, "grant_scope": "once", "grant_reason": None},
            expected_version=1,
        ),
        auth=reviewer,
    )

    assert (
        await manager.wait_for_tool_decision(
            first.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )
        is not None
    )
    with storage.unit_of_work() as unit_of_work:
        executing = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert executing is not None
    assert executing.phase.value == "executing"
    assert executing.version == 2
    assert executing.continuation.pending_decision_id is None
    assert executing.continuation.resolved_decision_id == first.decision_request_id
    assert executing.continuation.pending_tool is not None
    assert executing.continuation.pending_tool.tool_call_id == "tool-call-stable-7"

    repeated_resolved = _request_tool_decision(manager, edict, memorial)
    with storage.unit_of_work() as unit_of_work:
        unchanged = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert repeated_resolved.status.value == "resolved"
    assert repeated_resolved.decision_request_id == first.decision_request_id
    assert unchanged == executing
    assert (
        await manager.wait_for_tool_decision(
            first.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )
        is not None
    )

    second = _request_tool_decision(
        manager,
        edict,
        memorial,
        invocation_id="tool-call-stable-8",
        iteration=4,
    )
    with storage.unit_of_work() as unit_of_work:
        waiting_again = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert waiting_again is not None
    assert waiting_again.phase.value == "waiting_decision"
    assert waiting_again.version == 3
    assert waiting_again.continuation.pending_decision_id == second.decision_request_id
    assert waiting_again.continuation.pending_tool is not None
    assert waiting_again.continuation.pending_tool.tool_call_id == "tool-call-stable-8"


def test_terminal_exact_replay_before_live_mark_preserves_waiting_state(storage) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    first = _request_tool_decision(manager, edict, memorial)
    service.resolve(
        first.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="approved before waiter resumed",
            payload={"schema_version": 1, "grant_scope": "once", "grant_reason": None},
            expected_version=1,
        ),
        auth=AuthContext(
            principal=Principal(
                id="user:reviewer",
                kind="human",
                display_name="Reviewer",
                scopes=frozenset({"api"}),
            ),
            source="bearer",
            client_kind="api",
            correlation_id="replay-before-mark",
        ),
    )
    with storage.unit_of_work() as unit_of_work:
        before = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert before is not None and before.phase is RunPhase.WAITING_DECISION

    replayed = _request_tool_decision(manager, edict, memorial)

    assert replayed.decision_request_id == first.decision_request_id
    with storage.unit_of_work() as unit_of_work:
        after = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert after == before
    with pytest.raises(DecisionConflict) as caught:
        _request_tool_decision(manager, edict, memorial, iteration=4)
    assert caught.value.code == "decision_run_state_conflict"


async def test_expired_wait_releases_run_state_for_next_tool_suspension(storage) -> None:
    edict, memorial = _seed(storage)
    now = [_NOW]
    service = DecisionService(storage, clock=lambda: now[0])
    manager = ApprovalManager(
        event_bus=EventBus(),
        storage=storage,
        decision_service=service,
        clock=lambda: now[0],
    )
    first = _request_tool_decision(manager, edict, memorial)
    now[0] = first.expires_at

    assert (
        await manager.wait_for_tool_decision(
            first.decision_request_id,
            timeout_seconds=0.01,
            poll_interval_seconds=0,
        )
        is None
    )
    expired = service.get(first.decision_request_id)
    assert expired is not None and expired.request.status.value == "expired"
    with storage.unit_of_work() as unit_of_work:
        executing = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert executing is not None
    assert executing.phase.value == "executing"
    assert executing.continuation.pending_decision_id is None
    assert executing.continuation.resolved_decision_id == first.decision_request_id

    now[0] = first.expires_at + (first.expires_at - first.created_at)
    second = _request_tool_decision(
        manager,
        edict,
        memorial,
        invocation_id="tool-call-after-expiry",
        iteration=4,
    )
    assert second.status.value == "pending"


async def test_short_wait_timeout_does_not_expire_future_decision(storage) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    request = _request_tool_decision(manager, edict, memorial)

    assert (
        await manager.wait_for_tool_decision(
            request.decision_request_id,
            timeout_seconds=0.001,
            poll_interval_seconds=0,
        )
        is None
    )
    record = service.get(request.decision_request_id)
    assert record is not None and record.request.status.value == "pending"
    with storage.unit_of_work() as unit_of_work:
        waiting = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert waiting is not None and waiting.phase.value == "waiting_decision"


async def test_timeout_race_reclassifies_concurrently_resolved_decision(
    storage,
    monkeypatch,
) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    requested = _request_tool_decision(manager, edict, memorial)

    async def resolve_at_timeout(awaitable, *, timeout):
        del timeout
        awaitable.close()
        service.resolve(
            requested.decision_request_id,
            ResolveDecisionCommand(
                action="approve",
                reason="won timeout race",
                payload={"schema_version": 1, "grant_scope": "once", "grant_reason": None},
                expected_version=1,
            ),
            auth=AuthContext(
                principal=Principal(
                    id="user:reviewer",
                    kind="human",
                    display_name="Reviewer",
                    scopes=frozenset({"api"}),
                ),
                source="bearer",
                client_kind="api",
                correlation_id="timeout-race",
            ),
        )
        raise TimeoutError

    monkeypatch.setattr(approvals_module.asyncio, "wait_for", resolve_at_timeout)

    resolution = await manager.wait_for_tool_decision(requested.decision_request_id)

    assert resolution is not None and resolution.action == "approve"
    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.EXECUTING
    assert state.continuation.resolved_decision_id == requested.decision_request_id


async def test_silijian_proposal_resolves_generic_authority_before_decree_projection(
    storage,
    config_manager,
    tmp_path,
) -> None:
    edict, memorial = _seed(storage)
    for index in range(12):
        historical = Memorial(
            id=f"silijian-history-{index}",
            edict_id=edict.id,
            status=TaskStatus.COMPLETED,
        )
        storage.save_memorial(historical)
        storage.save_decree(Decree(memorial_id=historical.id, action="approve", actor="human"))
    config_manager.update_agent_config(
        silijian_enabled=True,
        silijian_max_tier=1,
        silijian_min_approval_rate=0.9,
        silijian_min_samples=10,
    )
    manager, service = _manager(storage)
    hook = PolicyHook(
        engine=PolicyEngine([]),
        workspace_root=tmp_path,
        storage=storage,
        tool_registry=None,
        approval_manager=manager,
        silijian=Silijian(storage, config_manager),
    )
    decision = PolicyDecision(
        verdict="require_approval",
        rule_id="workspace_write",
        reason="human review required",
    )

    result = await hook._request_approval(
        PolicyContext(
            tool_name="write_file",
            tool_tier=ToolTier.T1_WORKSPACE,
            args={"path": "README.md"},
            edict=edict,
            memorial=memorial,
            workspace_root=tmp_path,
            iteration=1,
        ),
        decision,
        invocation_id="silijian-tool-call",
        messages=[{"role": "user", "content": "write it"}],
        usage=UsageSummary(),
    )

    assert result is not None and result.authorization_source == "policy-engine"
    decision_request_id = storage._conn.execute(  # noqa: SLF001 - authority assertion
        "SELECT decision_request_id FROM decision_requests"
    ).fetchone()[0]
    record = service.get(decision_request_id)
    assert record is not None and record.resolution is not None
    assert record.resolution.actor_principal_id == "system:silijian"
    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 1  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM decision_resolutions").fetchone()[0] == 1  # noqa: SLF001
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'decision.resolved'"
        ).fetchone()[0]
        == 1
    )
    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.EXECUTING
    assert state.continuation.resolved_decision_id == decision_request_id
    [decree] = storage.list_decrees_by_memorial(memorial.id)
    assert decree.id == decision_request_id
    assert decree.actor == "system:silijian"


async def test_cancelled_wait_releases_run_state_for_next_tool_suspension(storage) -> None:
    edict, memorial = _seed(storage)
    manager, _ = _manager(storage)
    first = _request_tool_decision(manager, edict, memorial)
    storage._conn.execute(  # noqa: SLF001 - cancelled authority fixture
        "UPDATE decision_requests SET status = 'cancelled', version = 2 "
        "WHERE decision_request_id = ?",
        (first.decision_request_id,),
    )
    storage._conn.commit()  # noqa: SLF001 - cancelled authority fixture

    assert (
        await manager.wait_for_tool_decision(
            first.decision_request_id,
            timeout_seconds=0.1,
            poll_interval_seconds=0,
        )
        is None
    )
    second = _request_tool_decision(
        manager,
        edict,
        memorial,
        invocation_id="tool-call-after-cancel",
        iteration=4,
    )
    assert second.status.value == "pending"


@pytest.mark.parametrize("terminal_status", ("resolved", "expired", "cancelled"))
@pytest.mark.parametrize("mutation", ("messages", "iteration", "usage", "tool_proposal"))
async def test_terminal_replay_requires_exact_historical_continuation(
    storage,
    terminal_status: str,
    mutation: str,
) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    first = _request_tool_decision(manager, edict, memorial)
    if terminal_status == "resolved":
        service.resolve(
            first.decision_request_id,
            ResolveDecisionCommand(
                action="approve",
                reason="approved",
                payload={"schema_version": 1, "grant_scope": "once", "grant_reason": None},
                expected_version=1,
            ),
            auth=AuthContext(
                principal=Principal(
                    id="user:reviewer",
                    kind="human",
                    display_name="Reviewer",
                    scopes=frozenset({"api"}),
                ),
                source="bearer",
                client_kind="api",
                correlation_id="terminal-replay",
            ),
        )
    else:
        storage._conn.execute(  # noqa: SLF001 - terminal authority fixture
            "UPDATE decision_requests SET status = ?, version = 2 WHERE decision_request_id = ?",
            (terminal_status, first.decision_request_id),
        )
        storage._conn.commit()  # noqa: SLF001 - terminal authority fixture
    await manager.wait_for_tool_decision(
        first.decision_request_id,
        timeout_seconds=0.1,
        poll_interval_seconds=0,
    )
    with storage.unit_of_work() as unit_of_work:
        before = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert before is not None and before.phase is RunPhase.EXECUTING

    replayed = _request_tool_decision(manager, edict, memorial)
    assert replayed.decision_request_id == first.decision_request_id
    mutation_kwargs: dict[str, object] = {}
    if mutation == "messages":
        mutation_kwargs["messages"] = [{"role": "system", "content": "changed history"}]
    elif mutation == "iteration":
        mutation_kwargs["iteration"] = 4
    elif mutation == "usage":
        mutation_kwargs["usage"] = UsageSummary(
            prompt_tokens=8,
            completion_tokens=3,
            total_tokens=11,
        )
    else:
        mutation_kwargs["tool_name"] = "shell"
    counts_before = {
        table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
        for table in (
            "decision_requests",
            "decision_resolutions",
            "run_states",
            "outbox_events",
            "system_audit_events",
        )
    }

    with pytest.raises(DecisionConflict) as caught:
        _request_tool_decision(manager, edict, memorial, **mutation_kwargs)

    expected_code = (
        "decision_identity_conflict"
        if mutation == "tool_proposal"
        else "decision_run_state_conflict"
    )
    assert caught.value.code == expected_code
    with storage.unit_of_work() as unit_of_work:
        after = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert after == before
    counts_after = {
        table: storage._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608, SLF001
        for table in counts_before
    }
    expected_counts = dict(counts_before)
    if mutation == "tool_proposal":
        expected_counts["system_audit_events"] += 1
        denial = storage.list_system_audit()[-1]
        assert denial.reason_code == "decision_identity_conflict"
        assert "sk-tool-suspension-secret" not in repr(denial)
    assert counts_after == expected_counts


@pytest.mark.parametrize("fault_side", ["decision", "run_state"])
def test_atomic_tool_suspension_rolls_back_both_sides(
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
        _request_tool_decision(manager, edict, memorial)

    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 0  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM run_states").fetchone()[0] == 0  # noqa: SLF001
