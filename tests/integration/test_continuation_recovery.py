"""Restart matrix for durable outer-loop continuations and suspended attempts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tianshu.application.continuation_recovery import ContinuationRecoveryService
from tianshu.executor.continuation import reconstruct_outer_loop
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.decision import (
    DecisionKind,
    RequestDecisionCommand,
    ResolveDecisionCommand,
)
from tianshu.models.events import EventEnvelope
from tianshu.models.principal import AuthContext, Principal
from tianshu.models.run_state import (
    IterationSummaryV1,
    OuterLoopContinuationV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
)
from tianshu.storage import Storage

_NOW = datetime(2026, 7, 16, 10, tzinfo=UTC)
_RECONSTRUCTION_COMPAT_LEVELS = ("L0", "L1", "L2", "L3")


def _open(path: Path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    return storage


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
        correlation_id="continuation:test",
    )


def _usage() -> PersistedUsageSummaryV1:
    return PersistedUsageSummaryV1(
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
        cache_read_tokens=3,
        cost_cny=0.42,
        actual_model="model-a",
        upstream_provider="provider-a",
    )


def _continuation(level: str) -> OuterLoopContinuationV1:
    return OuterLoopContinuationV1(
        level=level,
        iteration=2,
        best_output="best durable output",
        feedback="verify evidence",
        steer="prefer primary sources",
        history=(
            IterationSummaryV1(
                iteration=0,
                level="L0",
                output_artifact_ref=None,
                critic_verdict="fail",
                critic_issue_class="factual_error",
                feedback="verify evidence",
                usage=_usage(),
                completed_at=_NOW,
            ),
            IterationSummaryV1(
                iteration=1,
                level=level,
                output_artifact_ref=None,
                critic_verdict="fail",
                critic_issue_class="factual_error",
                feedback="use source",
                usage=_usage(),
                completed_at=_NOW + timedelta(seconds=1),
            ),
        ),
        same_issue_streak=2,
        last_critic_issue_class="factual_error",
        l1_rounds_used=1,
        l2_rounds_used=1,
        consultation_advice="consult the evidence",
        usage=_usage(),
        total_cost_cny=Decimal("0.42"),
        checkpoint_ref="outer-loop:edict-1",
        resolved_decision_id=None,
        side_effect_cursor=4,
    )


def _seed_suspension(storage: Storage, *, level: str = "L0"):
    storage.save_edict(Edict(id="edict-1", goal="recover"))
    storage.save_memorial(Memorial(id="memorial-1", edict_id="edict-1", status=TaskStatus.RUNNING))
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="memorial-1",
            attempt_id="attempt-1",
            available_at=_NOW,
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-1",
        owner_id="worker-old",
        now=_NOW + timedelta(seconds=1),
        lease_seconds=60,
    )
    assert claimed is not None
    continuation = _continuation(level)
    state = RunStateV1(
        memorial_id="memorial-1",
        edict_id="edict-1",
        phase=RunPhase.WAITING_DECISION,
        continuation=continuation,
        checkpoint_ref=continuation.checkpoint_ref,
        side_effect_cursor=continuation.side_effect_cursor,
        version=1,
        created_at=_NOW + timedelta(seconds=1),
        updated_at=_NOW + timedelta(seconds=1),
    )
    service = DecisionService(storage, clock=lambda: _NOW + timedelta(seconds=2))
    request = service.request_with_run_state(
        RequestDecisionCommand(
            kind=DecisionKind.OUTER_LOOP,
            edict_id="edict-1",
            memorial_id="memorial-1",
            request_key=f"outer-loop:{level}:2",
            payload={"schema_version": 1, "level": level},
            expires_at=_NOW + timedelta(minutes=10),
        ),
        state,
        auth=_auth(),
    )
    assert storage.attempt_repo.complete(
        attempt_id=claimed.attempt_id,
        owner_id="worker-old",
        fencing_token=claimed.fencing_token,
        outcome=AttemptOutcomeV1(
            disposition=AttemptDisposition.SUSPENDED,
            completed_at=_NOW + timedelta(seconds=3),
        ),
    )
    return service, request, claimed


def _resolved_event(request, *, action: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"{request.decision_request_id}:test-resolution",
        event_type="decision.resolved",
        edict_id=request.edict_id,
        memorial_id=request.memorial_id,
        producer="test",
        timestamp=_NOW + timedelta(seconds=4),
        payload={
            "schema_version": 1,
            "decision_request_id": request.decision_request_id,
            "kind": request.kind.value,
            "action": action,
            "request_version": 2,
            "correlation_id": "continuation:test",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("level", _RECONSTRUCTION_COMPAT_LEVELS)
async def test_generic_recovery_reconstructs_compatible_l0_through_l3_snapshots_once(
    tmp_path: Path,
    level: str,
) -> None:
    path = tmp_path / f"restart-{level}.db"
    storage = _open(path)
    service, request, old_claim = _seed_suspension(storage, level=level)
    service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action="continue",
            reason="reviewed",
            payload={"schema_version": 1, "feedback": "continue carefully"},
            expected_version=1,
        ),
        auth=_auth(),
    )
    storage.close()

    restarted = _open(path)
    try:
        recovery = ContinuationRecoveryService(
            restarted,
            clock=lambda: _NOW + timedelta(seconds=5),
        )
        event = _resolved_event(request, action="continue")
        assert await recovery.handle_decision_resolved(event) is True
        assert await recovery.handle_decision_resolved(event) is False

        with restarted.unit_of_work() as unit_of_work:
            durable = restarted.run_state_repo.load(unit_of_work.connection, "memorial-1")
            resume_events = unit_of_work.connection.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='execution.resume.requested'"
            ).fetchone()[0]
            attempt = unit_of_work.connection.execute(
                "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
            ).fetchone()
            unit_of_work.commit()
        assert durable is not None and durable.phase is RunPhase.EXECUTING
        assert durable.continuation.pending_decision_id is None
        assert durable.continuation.resolved_decision_id == request.decision_request_id
        assert attempt[0] == "claimable"
        assert resume_events == 1

        reconstructed = reconstruct_outer_loop(restarted, "memorial-1", "edict-1")
        assert reconstructed is not None
        assert reconstructed.current_level == level
        assert reconstructed.iteration == 2
        assert reconstructed.same_issue_streak == 2
        assert reconstructed.l1_rounds_used == 1
        assert reconstructed.l2_rounds_used == 1
        assert reconstructed.total_cost_cny == pytest.approx(0.42)
        assert len(reconstructed.history) == 2
        assert reconstructed.history[-1].actor_output == "best durable output"

        new_claim = restarted.attempt_repo.claim(
            memorial_id="memorial-1",
            owner_id="worker-new",
            now=_NOW + timedelta(seconds=6),
            lease_seconds=60,
        )
        assert new_claim is not None
        assert new_claim.fencing_token > old_claim.fencing_token
        outcome = AttemptOutcomeV1(
            disposition=AttemptDisposition.SUCCEEDED,
            completed_at=_NOW + timedelta(seconds=7),
        )
        assert not restarted.attempt_repo.complete(
            attempt_id=old_claim.attempt_id,
            owner_id="worker-old",
            fencing_token=old_claim.fencing_token,
            outcome=outcome,
        )
        assert restarted.attempt_repo.complete(
            attempt_id=new_claim.attempt_id,
            owner_id="worker-new",
            fencing_token=new_claim.fencing_token,
            outcome=outcome,
        )
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_abort_cancels_atomically_and_replay_never_resurrects(tmp_path: Path) -> None:
    storage = _open(tmp_path / "cancel.db")
    service, request, _ = _seed_suspension(storage, level="L3")
    service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action="abort",
            reason="stop",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=_auth(),
    )
    recovery = ContinuationRecoveryService(storage, clock=lambda: _NOW + timedelta(seconds=5))
    event = _resolved_event(request, action="abort")
    try:
        assert await recovery.handle_decision_resolved(event) is True
        assert await recovery.handle_decision_resolved(event) is False
        memorial = storage.get_memorial("memorial-1")
        assert memorial is not None and memorial.status is TaskStatus.CANCELLED
        with storage.unit_of_work() as unit_of_work:
            state = storage.run_state_repo.load(unit_of_work.connection, "memorial-1")
            attempt = unit_of_work.connection.execute(
                "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
            ).fetchone()
            unit_of_work.commit()
        assert state is not None and state.phase is RunPhase.FAILED
        assert attempt[0] == "failed"
        assert (
            storage.attempt_repo.claim(
                memorial_id="memorial-1",
                owner_id="worker-new",
                now=_NOW + timedelta(seconds=6),
                lease_seconds=60,
            )
            is None
        )
    finally:
        storage.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ("after_run_state", "before_resume_outbox"))
async def test_resume_boundary_failure_rolls_back_whole_uow(
    tmp_path: Path,
    boundary: str,
) -> None:
    storage = _open(tmp_path / f"rollback-{boundary}.db")
    service, request, _ = _seed_suspension(storage)
    service.resolve(
        request.decision_request_id,
        ResolveDecisionCommand(
            action="continue",
            reason="continue",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=_auth(),
    )

    def fail(current: str) -> None:
        if current == boundary:
            raise RuntimeError(f"injected {boundary}")

    recovery = ContinuationRecoveryService(
        storage,
        clock=lambda: _NOW + timedelta(seconds=5),
        boundary_hook=fail,
    )
    try:
        with pytest.raises(RuntimeError, match=boundary):
            await recovery.handle_decision_resolved(_resolved_event(request, action="continue"))
        with storage.unit_of_work() as unit_of_work:
            state = storage.run_state_repo.load(unit_of_work.connection, "memorial-1")
            attempt = unit_of_work.connection.execute(
                "SELECT status FROM execution_attempts WHERE attempt_id='attempt-1'"
            ).fetchone()
            events = unit_of_work.connection.execute(
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='execution.resume.requested'"
            ).fetchone()[0]
            unit_of_work.commit()
        assert state is not None and state.phase is RunPhase.WAITING_DECISION
        assert attempt[0] == "suspended"
        assert events == 0
    finally:
        storage.close()
