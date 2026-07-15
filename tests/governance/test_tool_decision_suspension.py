"""Atomic durable suspension contracts for policy-governed tool calls."""

from datetime import UTC, datetime

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.executor.policy_hook import PolicyHook
from tianshu.governance.decision_service import DecisionConflict, DecisionService
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.decision import DecisionKind, ResolveDecisionCommand
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.run_state import PersistedChatMessageV1
from tianshu.storage import Storage

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
):
    secret = "sk-tool-suspension-secret"
    return manager.request_tool_decision(
        edict=edict,
        memorial=memorial,
        invocation_id=invocation_id,
        tool_name="write_file",
        tool_args={
            "path": "README.md",
            "api_key": secret,
            "nested": {"authorization": f"Bearer {secret}"},
        },
        tool_tier="T1_WORKSPACE",
        policy_rule_id="approval_required",
        messages=[
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
                            "name": "write_file",
                            "arguments": {
                                "path": "README.md",
                                "api_key": secret,
                            },
                        },
                    }
                ],
            },
        ],
        iteration=iteration,
        usage=UsageSummary(
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
