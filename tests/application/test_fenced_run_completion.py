"""Same-transaction terminal projections guarded by attempt fencing."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.application.fenced_run_completion import (
    FencedRunCompletion,
    FencedRunCompletionCommand,
)
from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.models import ArtifactRef, Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.canonical import RedactedError
from tianshu.models.run_state import (
    AgentContinuationV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
)
from tianshu.storage import Storage
from tianshu.storage.attempt_ledger import AttemptFenceLost

_NOW = datetime(2026, 7, 16, 9, tzinfo=UTC)


def _claimed(path: Path) -> tuple[Storage, AttemptAuthority]:
    storage = Storage(str(path))
    storage.init_db()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_memorial(Memorial(id="root-1", edict_id="edict-1"))
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="root-1",
            available_at=_NOW,
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id="root-1",
        owner_id="worker-1",
        now=_NOW,
        lease_seconds=30,
    )
    assert claimed is not None
    return storage, AttemptAuthority(
        attempt_id=claimed.attempt_id,
        memorial_id=claimed.memorial_id,
        owner_id="worker-1",
        fencing_token=claimed.fencing_token,
    )


def _claimed_with_budget(path: Path, *, max_attempts: int) -> tuple[Storage, AttemptAuthority]:
    storage = Storage(str(path))
    storage.init_db()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_memorial(Memorial(id="root-1", edict_id="edict-1"))
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="root-1",
            available_at=_NOW,
            max_attempts=max_attempts,
        )
        unit_of_work.commit()
    claimed = storage.attempt_repo.claim(
        memorial_id="root-1", owner_id="worker-1", now=_NOW, lease_seconds=30
    )
    assert claimed is not None
    return storage, AttemptAuthority(
        attempt_id=claimed.attempt_id,
        memorial_id=claimed.memorial_id,
        owner_id="worker-1",
        fencing_token=claimed.fencing_token,
    )


def _command(
    authority: AttemptAuthority,
    *,
    completed_at: datetime = _NOW,
) -> FencedRunCompletionCommand:
    return FencedRunCompletionCommand(
        authority=authority,
        outcome=AttemptOutcomeV1(
            disposition=AttemptDisposition.SUCCEEDED,
            completed_at=completed_at,
        ),
        memorial_status=TaskStatus.COMPLETED,
        summary="finished",
        result="result",
    )


def _run_state(*, phase: RunPhase = RunPhase.EXECUTING) -> RunStateV1:
    created_at = _NOW - timedelta(seconds=1)
    return RunStateV1(
        memorial_id="root-1",
        edict_id="edict-1",
        phase=phase,
        continuation=AgentContinuationV1(
            messages=(),
            pending_tool=None,
            iteration=0,
            usage=PersistedUsageSummaryV1(
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                cache_read_tokens=0,
                cost_cny=0,
                actual_model=None,
                upstream_provider=None,
            ),
            checkpoint_ref=None,
            pending_decision_id=None,
            resolved_decision_id=None,
            side_effect_cursor=0,
        ),
        checkpoint_ref=None,
        side_effect_cursor=0,
        version=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _save_run_state(storage: Storage) -> None:
    with storage.unit_of_work() as unit_of_work:
        storage.run_state_repo.create(unit_of_work.connection, _run_state())
        unit_of_work.commit()


def _snapshot(storage: Storage) -> tuple[object, ...]:
    memorial = storage.get_memorial("root-1")
    assert memorial is not None
    attempt = storage._conn.execute(  # noqa: SLF001
        "SELECT status, owner_id, fencing_token, version FROM execution_attempts"
    ).fetchone()
    return (
        memorial.status,
        memorial.summary,
        memorial.result,
        memorial.completed_at,
        tuple(attempt),
        storage._conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0],  # noqa: SLF001
    )


def test_current_fence_commits_memorial_outbox_and_attempt_atomically(tmp_path: Path) -> None:
    storage, authority = _claimed(tmp_path / "complete.db")
    try:
        event_id = FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(
            _command(authority)
        )

        memorial = storage.get_memorial("root-1")
        assert memorial is not None
        assert memorial.status is TaskStatus.COMPLETED
        assert memorial.summary == "finished"
        assert memorial.result == "result"
        assert memorial.completed_at == _NOW
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts WHERE attempt_id=?",
                (authority.attempt_id,),
            ).fetchone()[0]
            == "succeeded"
        )
        event = storage._conn.execute(  # noqa: SLF001
            "SELECT event_type, memorial_id, status FROM outbox_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        assert tuple(event) == ("execution.completed", "root-1", "pending")
    finally:
        storage.close()


def test_completion_preserves_full_terminal_evidence_and_existing_references(
    tmp_path: Path,
) -> None:
    storage, authority = _claimed(tmp_path / "terminal-evidence.db")
    root = storage.get_memorial("root-1")
    assert root is not None
    root.artifacts = [ArtifactRef(name="report", path="reports/final.md")]
    storage.update_memorial(root)
    command = replace(
        _command(authority),
        final_output="deliverable",
        usage=UsageSummary(
            prompt_tokens=2,
            completion_tokens=3,
            total_tokens=5,
            cache_read_tokens=1,
            cost_cny=0.5,
            actual_model="model-1",
            upstream_provider="provider-1",
        ),
        reasoning_content="private reasoning reference",
        failure_reason=None,
    )
    try:
        event_id = FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(command)

        memorial = storage.get_memorial("root-1")
        assert memorial is not None
        assert memorial.final_output == "deliverable"
        assert memorial.usage == command.usage
        assert memorial.reasoning_content == "private reasoning reference"
        assert memorial.artifacts == root.artifacts
        event = storage._conn.execute(  # noqa: SLF001
            "SELECT payload_json FROM outbox_events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        payload = __import__("json").loads(event[0])
        assert payload["final_output"] == "deliverable"
        assert payload["usage"]["total_tokens"] == 5
        assert "reasoning_content" not in payload
    finally:
        storage.close()


def test_terminal_root_cannot_be_overwritten_by_stale_success(tmp_path: Path) -> None:
    storage, authority = _claimed(tmp_path / "cancelled-root.db")
    cancelled = storage.get_memorial("root-1")
    assert cancelled is not None
    cancelled.status = TaskStatus.CANCELLED
    cancelled.error = "cancelled by operator"
    cancelled.completed_at = _NOW
    storage.update_memorial(cancelled)
    before = _snapshot(storage)
    try:
        from tianshu.storage.attempt_ledger import AttemptConflict

        with pytest.raises(AttemptConflict, match="root Memorial"):
            FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(
                _command(authority)
            )
        assert _snapshot(storage) == before
    finally:
        storage.close()


def test_cancellation_revokes_current_authority_with_root_projection(tmp_path: Path) -> None:
    storage, authority = _claimed(tmp_path / "cancel-authority.db")
    _save_run_state(storage)
    completion = FencedRunCompletion(storage.unit_of_work, storage.attempt_repo)
    try:
        assert completion.cancel_root(
            "root-1",
            reason="operator request",
            completed_at=_NOW,
        )
        memorial = storage.get_memorial("root-1")
        assert memorial is not None
        assert memorial.status is TaskStatus.CANCELLED
        assert memorial.failure_reason == "execution_cancelled"
        attempt = storage._conn.execute(  # noqa: SLF001
            "SELECT status, owner_id, lease_expires_at FROM execution_attempts WHERE attempt_id=?",
            (authority.attempt_id,),
        ).fetchone()
        assert tuple(attempt) == ("failed", None, None)
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events "
                "WHERE event_type='execution.cancelled' AND memorial_id='root-1'"
            ).fetchone()[0]
            == 1
        )
        with pytest.raises(AttemptFenceLost):
            completion.complete(_command(authority))
        state = storage.run_state_repo.load(storage._conn, "root-1")  # noqa: SLF001
        assert state is not None
        assert state.phase is RunPhase.FAILED
        assert state.version == 2
    finally:
        storage.close()


@pytest.mark.parametrize(
    "boundary",
    ["after_attempt", "after_memorial", "after_run_state", "after_outbox"],
)
def test_cancellation_failure_rolls_back_attempt_root_run_state_and_outbox(
    tmp_path: Path,
    boundary: str,
) -> None:
    storage, authority = _claimed(tmp_path / f"cancel-rollback-{boundary}.db")
    _save_run_state(storage)
    before = (
        _snapshot(storage),
        storage.run_state_repo.load(storage._conn, "root-1"),  # noqa: SLF001
    )

    def fail_at(observed: str) -> None:
        if observed == boundary:
            raise RuntimeError("injected cancellation failure")

    try:
        with pytest.raises(RuntimeError, match="injected cancellation failure"):
            FencedRunCompletion(
                storage.unit_of_work,
                storage.attempt_repo,
                boundary_hook=fail_at,
            ).cancel_root("root-1", reason="operator request", completed_at=_NOW)
        assert _snapshot(storage) == before[0]
        assert storage.run_state_repo.load(storage._conn, "root-1") == before[1]  # noqa: SLF001
        with pytest.raises(AttemptFenceLost):
            FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(
                _command(
                    AttemptAuthority(
                        attempt_id=authority.attempt_id,
                        memorial_id=authority.memorial_id,
                        owner_id="wrong-owner",
                        fencing_token=authority.fencing_token,
                    )
                )
            )
    finally:
        storage.close()


def test_pre_running_cancellation_terminalizes_claimable_attempt_atomically(
    tmp_path: Path,
) -> None:
    storage = Storage(str(tmp_path / "cancel-before-claim.db"))
    storage.init_db()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_memorial(Memorial(id="root-1", edict_id="edict-1"))
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="root-1",
            available_at=_NOW,
        )
        unit_of_work.commit()
    try:
        assert FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).cancel_root(
            "root-1", reason="operator request", completed_at=_NOW
        )
        assert storage.get_memorial("root-1").status is TaskStatus.CANCELLED
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT status FROM execution_attempts WHERE memorial_id='root-1'"
            ).fetchone()[0]
            == "failed"
        )
        assert (
            storage.attempt_repo.claim(
                memorial_id="root-1",
                owner_id="worker-1",
                now=_NOW,
                lease_seconds=30,
            )
            is None
        )
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("max_attempts", "expected_statuses", "expected_root", "failed_events"),
    [
        (2, ("failed", "claimable"), TaskStatus.SUBMITTED, 0),
        (1, ("dead_letter",), TaskStatus.FAILED, 1),
    ],
)
def test_retry_creates_next_attempt_or_one_fenced_dead_letter_terminal(
    tmp_path: Path,
    max_attempts: int,
    expected_statuses: tuple[str, ...],
    expected_root: TaskStatus,
    failed_events: int,
) -> None:
    storage, authority = _claimed_with_budget(
        tmp_path / f"retry-{max_attempts}.db",
        max_attempts=max_attempts,
    )
    failure = RedactedError(
        code="provider_unavailable",
        message="Provider temporarily unavailable",
        retryable=True,
        details_hash=None,
    )
    outcome = AttemptOutcomeV1(
        disposition=AttemptDisposition.RETRY,
        completed_at=_NOW,
        failure=failure,
        retry_at=_NOW + timedelta(seconds=1),
    )
    try:
        assert FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).retry_or_dead_letter(
            authority, outcome
        )
        statuses = storage._conn.execute(  # noqa: SLF001
            "SELECT status FROM execution_attempts ORDER BY attempt_no"
        ).fetchall()
        assert tuple(row[0] for row in statuses) == expected_statuses
        memorial = storage.get_memorial("root-1")
        assert memorial is not None and memorial.status is expected_root
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM outbox_events "
                "WHERE event_type='execution.failed' AND memorial_id='root-1'"
            ).fetchone()[0]
            == failed_events
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM memorials WHERE edict_id='edict-1'"
            ).fetchone()[0]
            == 1
        )
    finally:
        storage.close()


def test_failed_completion_emits_redacted_error_and_failure_reason(tmp_path: Path) -> None:
    storage, authority = _claimed(tmp_path / "failed-evidence.db")
    failure = RedactedError(
        code="provider_unavailable",
        message="Provider temporarily unavailable",
        retryable=False,
        details_hash="a" * 64,
    )
    command = FencedRunCompletionCommand(
        authority=authority,
        outcome=AttemptOutcomeV1(
            disposition=AttemptDisposition.FAILED,
            completed_at=_NOW,
            failure=failure,
        ),
        memorial_status=TaskStatus.FAILED,
        error=failure.message,
        failure_reason="provider_error",
    )
    try:
        event_id = FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(command)
        payload = __import__("json").loads(
            storage._conn.execute(  # noqa: SLF001
                "SELECT payload_json FROM outbox_events WHERE event_id=?",
                (event_id,),
            ).fetchone()[0]
        )
        assert payload["error"] == failure.message
        assert payload["failure_reason"] == "provider_error"
        memorial = storage.get_memorial("root-1")
        assert memorial is not None and memorial.failure_reason == "provider_error"
    finally:
        storage.close()


def test_current_fence_projects_optional_run_state_in_same_transaction(tmp_path: Path) -> None:
    storage, authority = _claimed(tmp_path / "run-state.db")
    usage = PersistedUsageSummaryV1(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cache_read_tokens=0,
        cost_cny=0,
        actual_model=None,
        upstream_provider=None,
    )
    created_at = _NOW - timedelta(seconds=1)
    initial = RunStateV1(
        memorial_id="root-1",
        edict_id="edict-1",
        phase=RunPhase.EXECUTING,
        continuation=AgentContinuationV1(
            messages=(),
            pending_tool=None,
            iteration=0,
            usage=usage,
            checkpoint_ref=None,
            pending_decision_id=None,
            resolved_decision_id=None,
            side_effect_cursor=0,
        ),
        checkpoint_ref=None,
        side_effect_cursor=0,
        version=1,
        created_at=created_at,
        updated_at=created_at,
    )
    with storage.unit_of_work() as unit_of_work:
        storage.run_state_repo.create(unit_of_work.connection, initial)
        unit_of_work.commit()
    terminal = initial.model_copy(update={"phase": RunPhase.COMPLETED, "updated_at": _NOW})
    command = replace(
        _command(authority),
        run_state=terminal,
        expected_run_state_version=1,
    )
    try:
        FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(command)
        with storage.unit_of_work() as unit_of_work:
            durable = storage.run_state_repo.load(unit_of_work.connection, "root-1")
            unit_of_work.commit()
        assert durable is not None
        assert durable.phase is RunPhase.COMPLETED
        assert durable.version == 2
    finally:
        storage.close()


def test_completion_loads_and_cas_terminal_run_state_without_caller_snapshot(
    tmp_path: Path,
) -> None:
    storage, authority = _claimed(tmp_path / "automatic-run-state.db")
    _save_run_state(storage)
    try:
        FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(
            _command(authority)
        )
        durable = storage.run_state_repo.load(storage._conn, "root-1")  # noqa: SLF001
        assert durable is not None
        assert durable.phase is RunPhase.COMPLETED
        assert durable.version == 2
    finally:
        storage.close()


@pytest.mark.parametrize("kind", ["wrong_owner", "wrong_token", "exact_expiry"])
def test_stale_or_expired_fence_changes_no_projection(tmp_path: Path, kind: str) -> None:
    storage, authority = _claimed(tmp_path / f"stale-{kind}.db")
    before = _snapshot(storage)
    if kind == "wrong_owner":
        authority = AttemptAuthority(
            attempt_id=authority.attempt_id,
            memorial_id=authority.memorial_id,
            owner_id="other-worker",
            fencing_token=authority.fencing_token,
        )
        completed_at = _NOW
    elif kind == "wrong_token":
        authority = AttemptAuthority(
            attempt_id=authority.attempt_id,
            memorial_id=authority.memorial_id,
            owner_id=authority.owner_id,
            fencing_token=authority.fencing_token + 1,
        )
        completed_at = _NOW
    else:
        completed_at = _NOW + timedelta(seconds=30)
    try:
        with pytest.raises(AttemptFenceLost, match="current"):
            FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(
                _command(authority, completed_at=completed_at)
            )
        assert _snapshot(storage) == before
    finally:
        storage.close()


def test_attempt_authority_cannot_be_rebound_to_another_root(tmp_path: Path) -> None:
    storage, authority = _claimed(tmp_path / "wrong-root.db")
    storage.save_memorial(Memorial(id="root-2", edict_id="edict-1"))
    rebound = AttemptAuthority(
        attempt_id=authority.attempt_id,
        memorial_id="root-2",
        owner_id=authority.owner_id,
        fencing_token=authority.fencing_token,
    )
    before = _snapshot(storage)
    try:
        with pytest.raises(AttemptFenceLost, match="Memorial"):
            FencedRunCompletion(storage.unit_of_work, storage.attempt_repo).complete(
                _command(rebound)
            )
        assert _snapshot(storage) == before
        other = storage.get_memorial("root-2")
        assert other is not None and other.status is TaskStatus.SUBMITTED
        assert other.completed_at is None
    finally:
        storage.close()


@pytest.mark.parametrize("boundary", ["after_memorial", "after_outbox", "before_attempt"])
def test_completion_failure_rolls_back_every_projection(tmp_path: Path, boundary: str) -> None:
    storage, authority = _claimed(tmp_path / f"rollback-{boundary}.db")
    before = _snapshot(storage)

    def fail_at(observed: str) -> None:
        if observed == boundary:
            raise RuntimeError("injected completion failure")

    try:
        with pytest.raises(RuntimeError, match="injected completion failure"):
            FencedRunCompletion(
                storage.unit_of_work,
                storage.attempt_repo,
                boundary_hook=fail_at,
            ).complete(_command(authority))
        assert _snapshot(storage) == before
    finally:
        storage.close()
