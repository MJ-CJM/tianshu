"""Plan-review decisions converge suspended execution attempts durably."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tianshu.application.plan_review_lifecycle import PlanReviewAttemptCoordinator
from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial, Plan, PlanTask, TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import canonical_sha256
from tianshu.models.decision import ResolveDecisionCommand
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.run_state import RunPhase
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


def _review(storage, *, suffix: str = "", suspend: bool = True):
    edict = Edict(id=f"edict-1{suffix}", goal="work", plan_review=True)
    root = Memorial(
        id=f"root-1{suffix}",
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
            attempt_id=f"attempt-1{suffix}",
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id=root.id,
        owner_id=f"worker-1{suffix}",
        now=_NOW,
        lease_seconds=30,
    )
    assert claimed is not None
    if suspend:
        assert storage.attempt_repo.complete(
            attempt_id=claimed.attempt_id,
            owner_id=f"worker-1{suffix}",
            fencing_token=claimed.fencing_token,
            outcome=AttemptOutcomeV1(
                disposition=AttemptDisposition.SUSPENDED,
                completed_at=_NOW,
            ),
        )
    return service, request, claimed


def _suspended_review(storage, *, suffix: str = ""):
    service, request, _claimed = _review(storage, suffix=suffix)
    return service, request


def _forge_cross_edict_binding(storage, service, request) -> None:
    storage.save_edict(Edict(id="edict-forged", goal="forged"))
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
    storage._conn.execute(  # noqa: SLF001
        "UPDATE run_states SET edict_id='edict-forged' WHERE memorial_id='root-1'"
    )
    storage._conn.execute(  # noqa: SLF001
        "UPDATE decision_requests SET edict_id='edict-forged' WHERE decision_request_id=?",
        (request.decision_request_id,),
    )
    storage._conn.commit()  # noqa: SLF001


def _terminalization_snapshot(storage) -> tuple[object, ...]:
    return (
        tuple(
            storage._conn.execute(  # noqa: SLF001
                "SELECT status, failure_json, version FROM execution_attempts "
                "WHERE attempt_id='attempt-1'"
            ).fetchone()
        ),
        tuple(
            storage._conn.execute(  # noqa: SLF001
                "SELECT status, error, failure_reason, completed_at FROM memorials "
                "WHERE id='root-1'"
            ).fetchone()
        ),
        tuple(
            storage._conn.execute(  # noqa: SLF001
                "SELECT edict_id, phase, continuation_json, version, updated_at "
                "FROM run_states WHERE memorial_id='root-1'"
            ).fetchone()
        ),
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events "
            "WHERE event_type='execution.failed' AND memorial_id='root-1'"
        ).fetchone()[0],
    )


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


def test_resolution_before_suspension_converges_after_attempt_suspends(storage) -> None:
    service, request, claimed = _review(storage, suspend=False)
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
    assert storage.attempt_repo.complete(
        attempt_id=claimed.attempt_id,
        owner_id="worker-1",
        fencing_token=claimed.fencing_token,
        outcome=AttemptOutcomeV1(
            disposition=AttemptDisposition.SUSPENDED,
            completed_at=_NOW,
        ),
    )

    coordinator = PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW)
    assert coordinator.reconcile_once() == 1
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
        ).fetchone()[0]
        == "claimable"
    )


def test_expired_review_terminalizes_suspended_attempt(storage) -> None:
    _service, request = _suspended_review(storage)
    expiry_service = DecisionService(storage, clock=lambda: request.expires_at)
    assert expiry_service.expire_due() == 1

    assert (
        PlanReviewAttemptCoordinator(storage, clock=lambda: request.expires_at).reconcile_once()
        == 1
    )
    assert storage.get_memorial("root-1").failure_reason == "plan_review_rejected"
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
        ).fetchone()[0]
        == "failed"
    )


def test_wrong_decision_root_and_edict_binding_fails_closed(storage) -> None:
    _service, _request = _suspended_review(storage)
    _other_service, other_request = _suspended_review(storage, suffix="-other")
    row = storage._conn.execute(  # noqa: SLF001
        "SELECT continuation_json FROM run_states WHERE memorial_id='root-1'"
    ).fetchone()
    continuation = json.loads(row[0])
    continuation["pending_decision_id"] = other_request.decision_request_id
    storage._conn.execute(  # noqa: SLF001
        "UPDATE run_states SET continuation_json=? WHERE memorial_id='root-1'",
        (json.dumps(continuation),),
    )
    storage._conn.commit()  # noqa: SLF001

    assert PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW).reconcile_once() == 1
    assert storage.get_memorial("root-1").failure_reason == "plan_review_binding_invalid"


def test_cross_edict_self_consistent_forged_binding_fails_closed(storage) -> None:
    service, request = _suspended_review(storage)
    _forge_cross_edict_binding(storage, service, request)

    assert PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW).reconcile_once() == 1
    root = storage.get_memorial("root-1")
    assert root is not None
    assert root.failure_reason == "plan_review_binding_invalid"
    state = storage.run_state_repo.load(storage._conn, "root-1")  # noqa: SLF001
    assert state is not None
    assert state.phase is RunPhase.FAILED
    assert state.version == 2
    outbox = storage._conn.execute(  # noqa: SLF001
        "SELECT edict_id FROM outbox_events "
        "WHERE event_type='execution.failed' AND memorial_id='root-1'"
    ).fetchone()
    assert outbox is not None
    assert root.edict_id == state.edict_id == outbox["edict_id"] == "edict-1"
    failure_json = storage._conn.execute(  # noqa: SLF001
        "SELECT failure_json FROM execution_attempts WHERE attempt_id='attempt-1'"
    ).fetchone()[0]
    assert json.loads(failure_json)["details_hash"] == canonical_sha256(
        {"detected_run_state_edict_id": "edict-forged"}
    )
    assert "edict-forged" not in failure_json


@pytest.mark.parametrize("fault", ("cas", "outbox"))
def test_forged_binding_terminalization_failure_rolls_back_all_projections(
    storage,
    monkeypatch,
    fault: str,
) -> None:
    service, request = _suspended_review(storage)
    _forge_cross_edict_binding(storage, service, request)
    coordinator = PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW)
    before = _terminalization_snapshot(storage)

    if fault == "cas":

        def fail_recovery(*_args, **_kwargs) -> None:
            raise RuntimeError("injected RunState CAS failure")

        monkeypatch.setattr(
            storage.run_state_repo,
            "recover_terminal_identity",
            fail_recovery,
            raising=False,
        )
    else:
        original_add = coordinator._outbox.add  # noqa: SLF001

        def add_then_fail(*args, **kwargs) -> None:
            original_add(*args, **kwargs)
            raise RuntimeError("injected outbox failure")

        monkeypatch.setattr(coordinator._outbox, "add", add_then_fail)  # noqa: SLF001

    with pytest.raises(RuntimeError, match="injected"):
        coordinator.reconcile_once()

    assert _terminalization_snapshot(storage) == before


def test_missing_decision_binding_fails_closed(storage) -> None:
    _service, _request = _suspended_review(storage)
    row = storage._conn.execute(  # noqa: SLF001
        "SELECT continuation_json FROM run_states WHERE memorial_id='root-1'"
    ).fetchone()
    continuation = json.loads(row[0])
    continuation["pending_decision_id"] = "decision:missing"
    storage._conn.execute(  # noqa: SLF001
        "UPDATE run_states SET continuation_json=? WHERE memorial_id='root-1'",
        (json.dumps(continuation),),
    )
    storage._conn.commit()  # noqa: SLF001

    assert PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW).reconcile_once() == 1
    assert storage.get_memorial("root-1").failure_reason == "plan_review_binding_invalid"


@pytest.mark.parametrize(
    ("action", "expected_attempt", "expected_memorial"),
    [
        ("approve", "claimable", TaskStatus.PLANNING),
        ("reject", "failed", TaskStatus.FAILED),
    ],
)
def test_already_projected_decision_converges_suspended_attempt(
    storage,
    action: str,
    expected_attempt: str,
    expected_memorial: TaskStatus,
) -> None:
    service, request = _suspended_review(storage)
    service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action=action,
            reason="reviewed",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=_auth(),
    )
    if action == "approve":
        service.mark_run_state_resolved(request.decision_request_id)
    else:
        service.mark_run_state_terminal(request.decision_request_id)

    coordinator = PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW)
    assert coordinator.reconcile_once() == 1
    assert coordinator.reconcile_once() == 0
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
        ).fetchone()[0]
        == expected_attempt
    )
    assert storage.get_memorial("root-1").status is expected_memorial


def test_canonical_plan_binding_mismatch_fails_closed_and_is_auditable(storage) -> None:
    service, request = _suspended_review(storage)
    row = storage._conn.execute(  # noqa: SLF001
        "SELECT continuation_json FROM run_states WHERE memorial_id='root-1'"
    ).fetchone()
    continuation = json.loads(row[0])
    continuation["plan_hash"] = "0" * 64
    storage._conn.execute(  # noqa: SLF001
        "UPDATE run_states SET continuation_json=? WHERE memorial_id='root-1'",
        (json.dumps(continuation),),
    )
    storage._conn.commit()  # noqa: SLF001
    service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="reviewed",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=_auth(),
    )

    assert PlanReviewAttemptCoordinator(storage, clock=lambda: _NOW).reconcile_once() == 1
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
        ).fetchone()[0]
        == "failed"
    )
    event = storage._conn.execute(  # noqa: SLF001
        "SELECT payload_json FROM outbox_events "
        "WHERE event_type='execution.failed' AND memorial_id='root-1'"
    ).fetchone()
    assert json.loads(event[0])["failure_reason"] == "plan_review_binding_invalid"


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
