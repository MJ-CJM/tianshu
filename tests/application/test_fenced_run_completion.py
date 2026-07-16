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
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
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
        assert tuple(event) == ("run.completed", "root-1", "pending")
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
