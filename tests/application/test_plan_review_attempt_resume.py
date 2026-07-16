"""Plan-review decisions converge suspended execution attempts durably."""

from __future__ import annotations

from datetime import UTC, datetime

from tianshu.application.plan_review_lifecycle import PlanReviewAttemptCoordinator
from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial, Plan, PlanTask, TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.decision import ResolveDecisionCommand
from tianshu.models.principal import AuthContext, Principal
from tianshu.planner.planner import Planner

_NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="user:reviewer",
            kind="human",
            display_name="Reviewer",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="review-1",
    )


def _suspended_review(storage):
    edict = Edict(id="edict-1", goal="work", plan_review=True)
    root = Memorial(
        id="root-1",
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.PLANNING,
    )
    storage.save_edict(edict)
    storage.save_memorial(root)
    service = DecisionService(storage, clock=lambda: _NOW)
    manager = ApprovalManager(EventBus(), storage, decision_service=service, clock=lambda: _NOW)
    request = manager.request_plan_review_decision(
        edict=edict,
        memorial=root,
        plan=Plan(
            tasks=[PlanTask(task_id="main", description="work")],
            priority_order=["main"],
        ),
        revision=1,
    )
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id=root.id,
            available_at=_NOW,
            attempt_id="attempt-1",
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id=root.id,
        owner_id="worker-1",
        now=_NOW,
        lease_seconds=30,
    )
    assert claimed is not None
    assert storage.attempt_repo.complete(
        attempt_id=claimed.attempt_id,
        owner_id="worker-1",
        fencing_token=claimed.fencing_token,
        outcome=AttemptOutcomeV1(
            disposition=AttemptDisposition.SUSPENDED,
            completed_at=_NOW,
        ),
    )
    return service, request


def test_approved_review_resumes_exact_suspended_attempt_once(storage) -> None:
    service, request = _suspended_review(storage)
    service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="good",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=_auth(),
    )
    coordinator = PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW)

    assert coordinator.reconcile_once() == 1
    assert coordinator.reconcile_once() == 0
    row = storage._conn.execute(  # noqa: SLF001
        "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
    ).fetchone()
    assert row[0] == "claimable"
    state = storage.run_state_repo.load(storage._conn, "root-1")  # noqa: SLF001
    assert state is not None
    assert state.continuation.pending_decision_id is None
    assert state.continuation.resolved_decision_id == request.decision_request_id


def test_rejected_review_terminalizes_root_attempt_and_outbox(storage) -> None:
    service, request = _suspended_review(storage)
    service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action="reject",
            reason="insufficient",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=_auth(),
    )
    assert PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW).reconcile_once() == 1
    assert storage.get_memorial("root-1").status is TaskStatus.FAILED
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
        ).fetchone()[0]
        == "failed"
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='execution.failed' "
            "AND memorial_id='root-1'"
        ).fetchone()[0]
        == 1
    )


async def test_approved_attempt_reuses_exact_durable_plan_without_second_review(
    storage, config_manager
) -> None:
    service, request = _suspended_review(storage)
    service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="good",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=_auth(),
    )
    PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW).reconcile_once()
    claimed = storage.attempt_repo.claim(
        memorial_id="root-1",
        owner_id="worker-2",
        now=_NOW,
        lease_seconds=30,
    )
    assert claimed is not None
    planner = Planner(
        EventBus(),
        storage,
        config_manager,
        approval_manager=ApprovalManager(
            EventBus(), storage, decision_service=service, clock=lambda: _NOW
        ),
    )

    planned = await planner.plan_attempt(
        AttemptAuthority(
            attempt_id=claimed.attempt_id,
            memorial_id=claimed.memorial_id,
            owner_id="worker-2",
            fencing_token=claimed.fencing_token,
        )
    )

    assert planned.suspended is False
    assert planned.plan.tasks[0].description == "work"
