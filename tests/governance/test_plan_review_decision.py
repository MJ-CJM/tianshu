"""Durable plan-review decision and compatibility projection contracts."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tianshu.application.outbox import OutboxDispatcher
from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.governance.decision_service import (
    DecisionConflict,
    DecisionService,
    DecisionValidationError,
)
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.canonical import canonical_sha256
from tianshu.models.decision import DecisionKind, ResolveDecisionCommand
from tianshu.models.events import make_event
from tianshu.models.plan import Plan, PlanTask
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.run_state import AgentContinuationV1, RunPhase
from tianshu.planner.planner import Planner
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_NOW = datetime(2026, 7, 15, 15, tzinfo=UTC)


def _seed(storage: Storage, *, status: TaskStatus = TaskStatus.PLANNING) -> tuple[Edict, Memorial]:
    edict = Edict(id="edict-plan-durable", goal="review this plan", plan_review=True)
    memorial = Memorial(
        id="memorial-plan-durable",
        edict_id=edict.id,
        status=status,
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    return edict, memorial


def _plan(description: str = "collect primary evidence") -> Plan:
    return Plan(
        tasks=[
            PlanTask(
                task_id="research",
                description=description,
                assigned_official="official-researcher",
            )
        ],
        priority_order=["research"],
    )


def _manager(
    storage: Storage, bus: EventBus | None = None
) -> tuple[ApprovalManager, DecisionService]:
    service = DecisionService(storage, clock=lambda: _NOW)
    return (
        ApprovalManager(
            event_bus=bus or EventBus(),
            storage=storage,
            decision_service=service,
            clock=lambda: _NOW,
        ),
        service,
    )


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="user:plan-reviewer",
            kind="human",
            display_name="Plan Reviewer",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="plan-review:resolve",
    )


def _request(
    manager: ApprovalManager,
    edict: Edict,
    memorial: Memorial,
    plan: Plan | None = None,
):
    return manager.request_plan_review_decision(
        edict=edict,
        memorial=memorial,
        plan=plan or _plan(),
        revision=1,
        timeout_seconds=60,
    )


def test_plan_review_request_persists_canonical_plan_without_fake_tool(storage) -> None:
    edict, memorial = _seed(storage)
    manager, _ = _manager(storage)
    plan = _plan()

    requested = _request(manager, edict, memorial, plan)

    assert requested.kind is DecisionKind.PLAN_REVIEW
    assert requested.request_key == "plan-review:1"
    assert requested.payload["revision"] == 1
    assert requested.payload["plan"] == plan.model_dump(mode="json")
    assert requested.payload["plan_hash"] == canonical_sha256(requested.payload["plan"])
    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.WAITING_DECISION
    assert isinstance(state.continuation, AgentContinuationV1)
    assert state.continuation.pending_tool is None
    assert state.continuation.plan_ref == f"plan:{edict.id}:1"
    assert state.continuation.plan_hash == requested.payload["plan_hash"]
    assert state.continuation.pending_decision_id == requested.decision_request_id

    continuation = state.continuation.model_dump(mode="python")
    with pytest.raises(ValidationError):
        AgentContinuationV1.model_validate({**continuation, "plan_hash": None})
    with pytest.raises(ValidationError):
        AgentContinuationV1.model_validate({**continuation, "plan_hash": "not-a-hash"})


@pytest.mark.parametrize("fault_side", ("decision", "run_state"))
def test_plan_review_decision_and_run_state_rollback_together(
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
    monkeypatch.setattr(repository, "add_or_get" if fault_side == "decision" else "create", fail)

    with pytest.raises(RuntimeError, match=f"{fault_side} fault"):
        _request(manager, edict, memorial)

    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 0  # noqa: SLF001
    assert storage._conn.execute("SELECT COUNT(*) FROM run_states").fetchone()[0] == 0  # noqa: SLF001


def test_plan_review_two_resolvers_have_one_durable_winner(tmp_path: Path) -> None:
    path = tmp_path / "plan-review-resolver-race.db"
    setup = Storage(str(path))
    setup.init_db()
    edict, memorial = _seed(setup)
    manager, _ = _manager(setup)
    requested = _request(manager, edict, memorial)
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
                    ResolveDecisionCommand(
                        action="approve",
                        reason="reviewed",
                        payload={"schema_version": 1},
                        expected_version=1,
                    ),
                    auth=_auth().model_copy(
                        update={
                            "principal": _auth().principal.model_copy(
                                update={"id": actor, "display_name": actor}
                            )
                        }
                    ),
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


async def test_plan_review_restart_resolution_precedes_approve_projection(tmp_path: Path) -> None:
    path = tmp_path / "plan-review-restart.db"
    first = Storage(str(path))
    first.init_db()
    edict, memorial = _seed(first, status=TaskStatus.NEEDS_REVIEW)
    manager, _ = _manager(first)
    requested = _request(manager, edict, memorial)
    first.close()

    restarted = Storage(str(path))
    restarted.init_db()
    bus = EventBus()
    completed = AsyncMock()
    bus.on("plan.completed", completed, consumer_name="test.plan_completed.v1")
    try:
        restarted_manager, service = _manager(restarted, bus)
        assert restarted_manager.list_pending_plan_reviews() == [requested]

        resolution = restarted_manager.submit_plan_review_decision(
            edict.id,
            action="approve",
            auth=_auth(),
        )

        assert resolution.actor_principal_id == "user:plan-reviewer"
        assert not any(
            event["event_type"] in {"plan.approved", "plan.completed"}
            for event in restarted.get_events(edict.id)
        )
        await restarted_manager.handle_decision_resolved(
            make_event(
                "decision.resolved",
                edict_id=edict.id,
                memorial_id=memorial.id,
                payload={
                    "decision_request_id": requested.decision_request_id,
                    "kind": DecisionKind.PLAN_REVIEW.value,
                    "action": "approve",
                },
            )
        )

        record = service.get(requested.decision_request_id)
        assert record is not None and record.resolution == resolution
        with restarted.unit_of_work() as unit_of_work:
            state = restarted.run_state_repo.load(unit_of_work.connection, memorial.id)
            unit_of_work.commit()
        assert state is not None and state.phase is RunPhase.PLANNING
        assert state.continuation.resolved_decision_id == requested.decision_request_id
        replayed = _request(restarted_manager, edict, memorial)
        assert replayed.status.value == "resolved"
        approved = next(
            event
            for event in restarted.get_events(edict.id)
            if event["event_type"] == "plan.approved"
        )
        assert approved["payload"]["actor"] == "user:plan-reviewer"
        completed.assert_not_awaited()
        [work] = restarted._conn.execute(  # noqa: SLF001
            "SELECT event_id, status FROM outbox_events WHERE event_type = 'plan.completed'"
        ).fetchall()
        assert work["event_id"] == f"{requested.decision_request_id}:plan.completed"
        assert work["status"] == "pending"
    finally:
        restarted.close()


async def test_plan_completed_retry_uses_one_durable_work_item_and_one_executor_effect(
    storage,
) -> None:
    edict, memorial = _seed(storage, status=TaskStatus.NEEDS_REVIEW)
    bus = EventBus()
    executor_calls = 0
    later_calls = 0

    async def executor(event) -> None:
        nonlocal executor_calls
        assert event.event_type == "plan.completed"
        executor_calls += 1

    async def flaky_later_consumer(event) -> None:
        nonlocal later_calls
        assert event.event_type == "plan.completed"
        later_calls += 1
        if later_calls == 1:
            raise RuntimeError("later projection failed")

    bus.on("plan.completed", executor, consumer_name="executor.plan_completed.v1")
    bus.on(
        "plan.completed",
        flaky_later_consumer,
        consumer_name="test.flaky_plan_projection.v1",
        priority=200,
    )
    manager, _ = _manager(storage, bus)
    requested = _request(manager, edict, memorial)
    manager.submit_plan_review_decision(edict.id, action="approve", auth=_auth())
    resolved_event = make_event(
        "decision.resolved",
        edict_id=edict.id,
        memorial_id=memorial.id,
        payload={
            "decision_request_id": requested.decision_request_id,
            "kind": DecisionKind.PLAN_REVIEW.value,
            "action": "approve",
        },
    )

    await manager.handle_decision_resolved(resolved_event)
    await manager.handle_decision_resolved(resolved_event)

    assert executor_calls == later_calls == 0
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type = 'plan.completed'"
        ).fetchone()[0]
        == 1
    )
    clock = [_NOW]
    dispatcher = OutboxDispatcher(
        OutboxRepository(storage.unit_of_work),
        bus,
        owner_id="plan-completed-retry",
        clock=lambda: clock[0],
        base_backoff_seconds=1,
        max_backoff_seconds=1,
    )

    assert await dispatcher.drain_once() == 2
    assert executor_calls == later_calls == 1
    clock[0] += timedelta(seconds=2)
    assert await dispatcher.drain_once() == 1
    assert await dispatcher.drain_once() == 0
    assert executor_calls == 1
    assert later_calls == 2
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM outbox_events WHERE event_type = 'plan.completed'"
        ).fetchone()[0]
        == "published"
    )


async def test_plan_review_reject_projection_fails_memorial_without_execution(storage) -> None:
    edict, memorial = _seed(storage, status=TaskStatus.NEEDS_REVIEW)
    bus = EventBus()
    completed = AsyncMock()
    bus.on("plan.completed", completed, consumer_name="test.plan_completed.v1")
    manager, _ = _manager(storage, bus)
    requested = _request(manager, edict, memorial)

    manager.submit_plan_review_decision(edict.id, action="reject", auth=_auth())
    await manager.handle_decision_resolved(
        make_event(
            "decision.resolved",
            edict_id=edict.id,
            memorial_id=memorial.id,
            payload={
                "decision_request_id": requested.decision_request_id,
                "kind": DecisionKind.PLAN_REVIEW.value,
                "action": "reject",
            },
        )
    )

    saved = storage.get_memorial(memorial.id)
    assert saved is not None and saved.status is TaskStatus.FAILED
    assert saved.error == "规划方案被驳回"
    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.FAILED
    replayed = _request(manager, edict, memorial)
    assert replayed.status.value == "resolved"
    rejected = next(
        event for event in storage.get_events(edict.id) if event["event_type"] == "plan.rejected"
    )
    assert rejected["payload"]["actor"] == "user:plan-reviewer"
    completed.assert_not_awaited()


async def test_expired_plan_review_releases_waiting_state_and_fails_closed(storage) -> None:
    edict, memorial = _seed(storage, status=TaskStatus.NEEDS_REVIEW)
    now = [_NOW]
    service = DecisionService(storage, clock=lambda: now[0])
    manager = ApprovalManager(
        event_bus=EventBus(),
        storage=storage,
        decision_service=service,
        clock=lambda: now[0],
    )
    requested = manager.request_plan_review_decision(
        edict=edict,
        memorial=memorial,
        plan=_plan(),
        revision=1,
        timeout_seconds=1,
    )
    now[0] = requested.expires_at
    assert service.expire_due() == 1

    await manager.handle_decision_expired(
        make_event(
            "decision.expired",
            edict_id=edict.id,
            memorial_id=memorial.id,
            payload={
                "decision_request_id": requested.decision_request_id,
                "kind": DecisionKind.PLAN_REVIEW.value,
            },
        )
    )

    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.FAILED
    assert state.continuation.resolved_decision_id == requested.decision_request_id
    saved = storage.get_memorial(memorial.id)
    assert saved is not None and saved.status is TaskStatus.FAILED
    assert saved.error == "规划审批已超时"
    replayed = _request(manager, edict, memorial)
    assert replayed.status.value == "expired"


def test_plan_review_amend_fails_before_resolution_or_outbox(storage) -> None:
    edict, memorial = _seed(storage)
    manager, service = _manager(storage)
    requested = _request(manager, edict, memorial)

    with pytest.raises(DecisionValidationError) as caught:
        service.resolve(
            requested.decision_request_id,
            ResolveDecisionCommand(
                action="amend",
                reason="change it",
                payload={"schema_version": 1, "amendment": "split research"},
                expected_version=1,
            ),
            auth=_auth(),
        )

    assert caught.value.code == "invalid_decision_resolution"
    record = service.get(requested.decision_request_id)
    assert record is not None and record.request.status.value == "pending"
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM decision_resolutions"
        ).fetchone()[0]
        == 0
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events"
        ).fetchone()[0]
        == 0
    )


def test_plan_review_exact_replay_and_revision_payload_conflict(storage) -> None:
    edict, memorial = _seed(storage)
    manager, _ = _manager(storage)
    first = _request(manager, edict, memorial)
    manager.submit_plan_review_decision(edict.id, action="approve", auth=_auth())

    replayed = _request(manager, edict, memorial)
    assert replayed.decision_request_id == first.decision_request_id
    assert replayed.status.value == "resolved"
    with pytest.raises(DecisionConflict) as caught:
        _request(manager, edict, memorial, _plan("changed plan"))

    assert caught.value.code == "decision_identity_conflict"
    assert storage._conn.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 1  # noqa: SLF001


async def test_planner_persists_generic_authority_before_pending_projection(
    storage,
    config_manager,
) -> None:
    edict, memorial = _seed(storage, status=TaskStatus.SCHEDULED)
    bus = EventBus()
    manager, service = _manager(storage, bus)
    observed: list[str] = []

    async def capture_pending(event) -> None:
        pending = service.list_pending(kind=DecisionKind.PLAN_REVIEW)
        assert len(pending) == 1
        assert pending[0].decision_request_id == event.payload["decision_request_id"]
        observed.append(event.event_type)

    bus.on("plan.pending_review", capture_pending, consumer_name="test.plan_pending.v1")
    planner = Planner(
        event_bus=bus,
        storage=storage,
        config_manager=config_manager,
        approval_manager=manager,
    )

    await planner.handle_scheduled(
        make_event("edict.scheduled", edict_id=edict.id, memorial_id=memorial.id)
    )

    assert observed == ["plan.pending_review"]
    assert storage.get_memorial(memorial.id).status is TaskStatus.NEEDS_REVIEW


@pytest.mark.parametrize(
    "authority_state",
    ["pending", "missing", "mismatched", "rejected", "unreferenced"],
)
async def test_plan_attempt_requires_exact_approved_plan_review_authority(
    storage,
    config_manager,
    authority_state: str,
) -> None:
    edict, memorial = _seed(storage)
    manager, _service = _manager(storage)
    plan = _plan()
    request = _request(manager, edict, memorial, plan)
    planner = Planner(
        event_bus=EventBus(),
        storage=storage,
        config_manager=config_manager,
        approval_manager=manager,
    )
    planner.plan = AsyncMock(return_value=plan)

    if authority_state in {"missing", "rejected"}:
        action = "approve" if authority_state == "missing" else "reject"
        manager.submit_plan_review_decision(edict.id, action=action, auth=_auth())
        await manager.handle_decision_resolved(
            make_event(
                "decision.resolved",
                edict_id=edict.id,
                memorial_id=memorial.id,
                payload={
                    "decision_request_id": request.decision_request_id,
                    "kind": DecisionKind.PLAN_REVIEW.value,
                    "action": action,
                },
            )
        )

    mismatched_decision_id: str | None = None
    if authority_state == "mismatched":
        other_edict = Edict(id="edict-plan-other", goal="other review", plan_review=True)
        other_memorial = Memorial(
            id="memorial-plan-other",
            edict_id=other_edict.id,
            status=TaskStatus.PLANNING,
        )
        storage.save_edict(other_edict)
        storage.save_memorial(other_memorial)
        mismatched_decision_id = _request(
            manager,
            other_edict,
            other_memorial,
        ).decision_request_id

    if authority_state != "pending":
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT continuation_json FROM run_states WHERE memorial_id=?",
            (memorial.id,),
        ).fetchone()
        continuation = json.loads(row[0])
        continuation["pending_decision_id"] = None
        continuation["resolved_decision_id"] = {
            "missing": "decision:missing",
            "mismatched": mismatched_decision_id,
            "rejected": request.decision_request_id,
            "unreferenced": None,
        }[authority_state]
        storage._conn.execute(  # noqa: SLF001
            "UPDATE run_states SET phase='planning', continuation_json=? WHERE memorial_id=?",
            (json.dumps(continuation), memorial.id),
        )
        storage._conn.commit()  # noqa: SLF001

    with pytest.raises(RuntimeError, match="plan review approval authority"):
        await planner.plan_attempt(
            AttemptAuthority(
                attempt_id=f"attempt-{authority_state}",
                memorial_id=memorial.id,
                owner_id="worker",
                fencing_token=1,
            )
        )
