"""Strict models and transactional behavior for the execution-attempt ledger."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from tianshu.models import Edict, Memorial
from tianshu.models.attempt import (
    AttemptDisposition,
    AttemptLeaseV1,
    AttemptOutcomeV1,
    AttemptStatus,
)
from tianshu.models.canonical import RedactedError
from tianshu.storage import Storage
from tianshu.storage.attempt_ledger import (
    AttemptConflict,
    AttemptDecodeError,
    AttemptFenceLost,
)
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository

_NOW = datetime(2026, 7, 15, 8, tzinfo=UTC)


def _error(*, retryable: bool = True) -> RedactedError:
    return RedactedError(
        code="worker_failed",
        message="execution failed",
        retryable=retryable,
        details_hash="a" * 64,
    )


def _seed(storage: Storage, *, memorial_id: str = "memorial-1") -> None:
    storage.save_edict(Edict(id="edict-1", goal="test"))
    storage.save_memorial(Memorial(id=memorial_id, edict_id="edict-1", attempt=41))


def _open(path: Path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    return storage


def _claim(storage: Storage, *, now: datetime = _NOW, owner: str = "worker-1") -> AttemptLeaseV1:
    claimed = storage.attempt_repo.claim(
        memorial_id="memorial-1",
        owner_id=owner,
        now=now,
        lease_seconds=30,
    )
    assert claimed is not None
    return claimed


def _outcome(
    disposition: AttemptDisposition,
    *,
    completed_at: datetime = _NOW + timedelta(seconds=5),
    failure: RedactedError | None = None,
    retry_at: datetime | None = None,
) -> AttemptOutcomeV1:
    return AttemptOutcomeV1(
        disposition=disposition,
        completed_at=completed_at,
        failure=failure,
        retry_at=retry_at,
    )


def test_attempt_models_are_frozen_strict_utc_and_shape_checked() -> None:
    claimed = AttemptLeaseV1(
        attempt_id="attempt-1",
        memorial_id="memorial-1",
        attempt_no=1,
        status=AttemptStatus.CLAIMED,
        owner_id="worker-1",
        fencing_token=1,
        lease_expires_at=_NOW + timedelta(seconds=30),
        heartbeat_at=_NOW,
        available_at=_NOW,
        max_attempts=3,
        failure=None,
        version=2,
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert claimed.lease_expires_at == _NOW + timedelta(seconds=30)
    with pytest.raises(ValidationError, match="frozen"):
        claimed.status = AttemptStatus.SUCCEEDED  # type: ignore[misc]
    with pytest.raises(ValidationError, match="timezone-aware"):
        claimed.model_copy(update={"heartbeat_at": datetime(2026, 7, 15, 8)}).__class__(
            **claimed.model_dump() | {"heartbeat_at": datetime(2026, 7, 15, 8)}
        )
    with pytest.raises(ValidationError, match="claimed"):
        AttemptLeaseV1(**claimed.model_dump() | {"owner_id": None})
    with pytest.raises(ValidationError, match="failure"):
        AttemptLeaseV1(
            **claimed.model_dump()
            | {
                "status": AttemptStatus.FAILED,
                "owner_id": None,
                "lease_expires_at": None,
                "failure": None,
            }
        )


@pytest.mark.parametrize(
    "values",
    [
        {"disposition": AttemptDisposition.RETRY, "completed_at": _NOW},
        {
            "disposition": AttemptDisposition.RETRY,
            "completed_at": _NOW,
            "failure": _error(),
            "retry_at": _NOW - timedelta(seconds=1),
        },
        {
            "disposition": AttemptDisposition.SUCCEEDED,
            "completed_at": _NOW,
            "failure": _error(),
        },
        {
            "disposition": AttemptDisposition.FAILED,
            "completed_at": _NOW,
            "retry_at": _NOW,
            "failure": _error(),
        },
    ],
)
def test_attempt_outcome_rejects_inconsistent_shapes(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AttemptOutcomeV1(**values)


def test_enqueue_is_idempotent_rejects_unknown_memorial_and_never_mutates_legacy_attempt(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "enqueue.db")
    _seed(storage)
    try:
        with storage.unit_of_work() as uow:
            first = storage.attempt_repo.enqueue_initial(
                uow.connection,
                memorial_id="memorial-1",
                available_at=_NOW,
                max_attempts=3,
            )
            replay = storage.attempt_repo.enqueue_initial(
                uow.connection,
                memorial_id="memorial-1",
                available_at=_NOW,
                max_attempts=3,
            )
            uow.commit()
        assert replay == first
        assert first.status is AttemptStatus.CLAIMABLE
        assert storage.get_memorial("memorial-1").attempt == 41  # type: ignore[union-attr]
        with storage.unit_of_work() as uow:
            with pytest.raises(AttemptConflict, match="memorial"):
                storage.attempt_repo.enqueue_initial(
                    uow.connection,
                    memorial_id="missing",
                    available_at=_NOW,
                )
            uow.commit()
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts WHERE memorial_id='missing'"
            ).fetchone()[0]
            == 0
        )
    finally:
        storage.close()


def test_enqueue_rejects_dag_child_but_allows_root_retry_lineage(tmp_path: Path) -> None:
    storage = _open(tmp_path / "root-enqueue.db")
    storage.save_edict(Edict(id="edict-1", goal="test"))
    storage.save_memorial(
        Memorial(
            id="retry-root",
            edict_id="edict-1",
            parent_memorial_id="previous-root",
            dag_node_id=None,
        )
    )
    storage.save_memorial(
        Memorial(
            id="dag-child",
            edict_id="edict-1",
            parent_memorial_id="retry-root",
            dag_node_id="node-1",
        )
    )
    try:
        with storage.unit_of_work() as uow:
            root_attempt = storage.attempt_repo.enqueue_initial(
                uow.connection,
                memorial_id="retry-root",
                available_at=_NOW,
            )
            with pytest.raises(AttemptConflict, match="not a root") as error:
                storage.attempt_repo.enqueue_initial(
                    uow.connection,
                    memorial_id="dag-child",
                    available_at=_NOW,
                )
            uow.commit()
        assert root_attempt.memorial_id == "retry-root"
        assert "dag-child" not in str(error.value)
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts WHERE memorial_id='dag-child'"
            ).fetchone()[0]
            == 0
        )
    finally:
        storage.close()


def test_missing_future_unexpired_and_suspended_attempts_are_not_claimed(tmp_path: Path) -> None:
    storage = _open(tmp_path / "not-due.db")
    _seed(storage)
    try:
        assert (
            storage.attempt_repo.claim(
                memorial_id="missing", owner_id="worker", now=_NOW, lease_seconds=30
            )
            is None
        )
        with storage.unit_of_work() as uow:
            storage.attempt_repo.enqueue_initial(
                uow.connection,
                memorial_id="memorial-1",
                available_at=_NOW + timedelta(minutes=1),
            )
            uow.commit()
        assert (
            storage.attempt_repo.claim(
                memorial_id="memorial-1", owner_id="worker", now=_NOW, lease_seconds=30
            )
            is None
        )
        due = _claim(storage, now=_NOW + timedelta(minutes=1))
        assert (
            storage.attempt_repo.claim(
                memorial_id="memorial-1",
                owner_id="worker-2",
                now=_NOW + timedelta(minutes=1, seconds=1),
                lease_seconds=30,
            )
            is None
        )
        assert storage.attempt_repo.complete(
            attempt_id=due.attempt_id,
            owner_id="worker-1",
            fencing_token=due.fencing_token,
            outcome=_outcome(
                AttemptDisposition.SUSPENDED,
                completed_at=_NOW + timedelta(minutes=1, seconds=2),
            ),
        )
        assert (
            storage.attempt_repo.claim(
                memorial_id="memorial-1",
                owner_id="worker-2",
                now=_NOW + timedelta(minutes=2),
                lease_seconds=30,
            )
            is None
        )
    finally:
        storage.close()


def test_two_connections_produce_one_claim_winner(tmp_path: Path) -> None:
    database = tmp_path / "race.db"
    first = _open(database)
    _seed(first)
    with first.unit_of_work() as uow:
        first.attempt_repo.enqueue_initial(
            uow.connection, memorial_id="memorial-1", available_at=_NOW
        )
        uow.commit()
    second = _open(database)
    try:
        barrier = Barrier(2)

        def race(storage: Storage, owner_id: str) -> AttemptLeaseV1 | None:
            barrier.wait()
            return storage.attempt_repo.claim(
                memorial_id="memorial-1",
                owner_id=owner_id,
                now=_NOW,
                lease_seconds=30,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: race(*args),
                    ((first, "worker-a"), (second, "worker-b")),
                )
            )
        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        assert winners[0].owner_id in {"worker-a", "worker-b"}
        assert winners[0].fencing_token == 1
    finally:
        second.close()
        first.close()


def test_heartbeat_extends_original_duration_and_rejects_stale_or_rollback_clock(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "heartbeat.db")
    _seed(storage)
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection, memorial_id="memorial-1", available_at=_NOW
        )
        uow.commit()
    try:
        claimed = _claim(storage)
        assert not storage.attempt_repo.heartbeat(
            attempt_id=claimed.attempt_id,
            owner_id="other",
            fencing_token=claimed.fencing_token,
            now=_NOW + timedelta(seconds=5),
        )
        assert not storage.attempt_repo.heartbeat(
            attempt_id=claimed.attempt_id,
            owner_id="worker-1",
            fencing_token=claimed.fencing_token + 1,
            now=_NOW + timedelta(seconds=5),
        )
        assert not storage.attempt_repo.heartbeat(
            attempt_id=claimed.attempt_id,
            owner_id="worker-1",
            fencing_token=claimed.fencing_token,
            now=_NOW - timedelta(seconds=1),
        )
        assert storage.attempt_repo.heartbeat(
            attempt_id=claimed.attempt_id,
            owner_id="worker-1",
            fencing_token=claimed.fencing_token,
            now=_NOW + timedelta(seconds=5),
        )
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT heartbeat_at, lease_expires_at, fencing_token FROM execution_attempts"
        ).fetchone()
        assert datetime.fromisoformat(row[0]) == _NOW + timedelta(seconds=5)
        assert datetime.fromisoformat(row[1]) == _NOW + timedelta(seconds=35)
        assert row[2] == claimed.fencing_token
        assert not storage.attempt_repo.heartbeat(
            attempt_id=claimed.attempt_id,
            owner_id="worker-1",
            fencing_token=claimed.fencing_token,
            now=_NOW + timedelta(seconds=35),
        )
    finally:
        storage.close()


def test_expired_lease_is_failed_and_next_attempt_claimed_with_higher_fence(tmp_path: Path) -> None:
    storage = _open(tmp_path / "expired.db")
    _seed(storage)
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection, memorial_id="memorial-1", available_at=_NOW
        )
        uow.commit()
    try:
        first = _claim(storage)
        second = storage.attempt_repo.claim(
            memorial_id="memorial-1",
            owner_id="worker-2",
            now=_NOW + timedelta(seconds=31),
            lease_seconds=30,
        )
        assert second is not None
        assert (second.attempt_no, second.fencing_token, second.owner_id) == (2, 2, "worker-2")
        old = storage._conn.execute(  # noqa: SLF001
            "SELECT status, owner_id, failure_json FROM execution_attempts WHERE attempt_id=?",
            (first.attempt_id,),
        ).fetchone()
        assert old[0:2] == ("failed", None)
        assert json.loads(old[2])["code"] == "attempt_lease_expired"
        assert not storage.attempt_repo.complete(
            attempt_id=first.attempt_id,
            owner_id="worker-1",
            fencing_token=first.fencing_token,
            outcome=_outcome(
                AttemptDisposition.SUCCEEDED, completed_at=_NOW + timedelta(seconds=32)
            ),
        )
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("disposition", "failure", "expected"),
    [
        (AttemptDisposition.SUCCEEDED, None, "succeeded"),
        (AttemptDisposition.FAILED, _error(retryable=False), "failed"),
        (AttemptDisposition.SUSPENDED, None, "suspended"),
    ],
)
def test_complete_terminal_and_suspended_outcomes(
    tmp_path: Path,
    disposition: AttemptDisposition,
    failure: RedactedError | None,
    expected: str,
) -> None:
    storage = _open(tmp_path / f"{expected}.db")
    _seed(storage)
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection, memorial_id="memorial-1", available_at=_NOW
        )
        uow.commit()
    try:
        claimed = _claim(storage)
        assert storage.attempt_repo.complete(
            attempt_id=claimed.attempt_id,
            owner_id="worker-1",
            fencing_token=claimed.fencing_token,
            outcome=_outcome(disposition, failure=failure),
        )
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT status, owner_id, lease_expires_at, failure_json FROM execution_attempts"
        ).fetchone()
        assert row[0:3] == (expected, None, None)
        assert (row[3] is not None) is (failure is not None)
        assert not storage.attempt_repo.complete(
            attempt_id=claimed.attempt_id,
            owner_id="worker-1",
            fencing_token=claimed.fencing_token,
            outcome=_outcome(disposition, failure=failure),
        )
    finally:
        storage.close()


def test_retry_creates_due_attempt_atomically_and_fencing_is_monotonic(tmp_path: Path) -> None:
    storage = _open(tmp_path / "retry.db")
    _seed(storage)
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection, memorial_id="memorial-1", available_at=_NOW
        )
        uow.commit()
    try:
        first = _claim(storage)
        retry_at = _NOW + timedelta(seconds=20)
        assert storage.attempt_repo.complete(
            attempt_id=first.attempt_id,
            owner_id="worker-1",
            fencing_token=first.fencing_token,
            outcome=_outcome(
                AttemptDisposition.RETRY,
                failure=_error(),
                retry_at=retry_at,
            ),
        )
        assert (
            storage.attempt_repo.claim(
                memorial_id="memorial-1",
                owner_id="worker-2",
                now=retry_at - timedelta(seconds=1),
                lease_seconds=30,
            )
            is None
        )
        second = _claim(storage, now=retry_at, owner="worker-2")
        assert (second.attempt_no, second.fencing_token) == (2, 2)
    finally:
        storage.close()


@pytest.mark.parametrize(
    "generation_ids",
    [(), ("rg-" + "1" * 32,)],
)
def test_retry_inherits_exact_generation_marker_without_rebucketing(
    tmp_path: Path,
    generation_ids: tuple[str, ...],
) -> None:
    storage = _open(tmp_path / f"retry-generation-{len(generation_ids)}.db")
    _seed(storage)
    with storage.unit_of_work() as uow:
        first = storage.attempt_repo.enqueue_initial(
            uow.connection,
            memorial_id="memorial-1",
            available_at=_NOW,
        )
        SystemSnapshotRepository().insert_generation_binding(
            uow.connection,
            memorial_id="memorial-1",
            attempt_id=first.attempt_id,
            generation_ids=generation_ids,
        )
        uow.commit()
    try:
        claimed = _claim(storage)
        assert storage.attempt_repo.complete(
            attempt_id=claimed.attempt_id,
            owner_id="worker-1",
            fencing_token=claimed.fencing_token,
            outcome=_outcome(
                AttemptDisposition.RETRY,
                failure=_error(),
                retry_at=_NOW + timedelta(seconds=20),
            ),
        )
        row = storage._conn.execute(  # noqa: SLF001
            """
            SELECT binding.state, binding.generation_ids_json
            FROM execution_attempts AS attempt
            JOIN run_generation_bindings AS binding
              ON binding.memorial_id=attempt.memorial_id
             AND binding.attempt_id=attempt.attempt_id
            WHERE attempt.attempt_no=2
            """
        ).fetchone()
        assert row is not None
        assert tuple(row) == (
            "bound",
            json.dumps(list(generation_ids), separators=(",", ":")),
        )
    finally:
        storage.close()


def test_max_attempts_transitions_retry_and_expiry_to_dead_letter(tmp_path: Path) -> None:
    for mode in ("retry", "expiry"):
        storage = _open(tmp_path / f"dlq-{mode}.db")
        _seed(storage)
        with storage.unit_of_work() as uow:
            storage.attempt_repo.enqueue_initial(
                uow.connection,
                memorial_id="memorial-1",
                available_at=_NOW,
                max_attempts=1,
            )
            uow.commit()
        try:
            claimed = _claim(storage)
            if mode == "retry":
                assert storage.attempt_repo.complete(
                    attempt_id=claimed.attempt_id,
                    owner_id="worker-1",
                    fencing_token=claimed.fencing_token,
                    outcome=_outcome(
                        AttemptDisposition.RETRY,
                        failure=_error(),
                        retry_at=_NOW + timedelta(seconds=5),
                    ),
                )
            else:
                assert (
                    storage.attempt_repo.claim(
                        memorial_id="memorial-1",
                        owner_id="worker-2",
                        now=_NOW + timedelta(seconds=31),
                        lease_seconds=30,
                    )
                    is None
                )
            row = storage._conn.execute(  # noqa: SLF001
                "SELECT status, failure_json FROM execution_attempts"
            ).fetchone()
            assert row[0] == "dead_letter"
            assert json.loads(row[1])["message"] in {"execution failed", "execution lease expired"}
        finally:
            storage.close()


def test_retry_insert_failure_rolls_back_claimed_attempt(tmp_path: Path) -> None:
    storage = _open(tmp_path / "rollback.db")
    _seed(storage)
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection, memorial_id="memorial-1", available_at=_NOW
        )
        uow.commit()
    claimed = _claim(storage)
    storage._conn.execute(  # noqa: SLF001
        """
        CREATE TRIGGER reject_retry_attempt BEFORE INSERT ON execution_attempts
        WHEN NEW.attempt_no = 2
        BEGIN SELECT RAISE(ABORT, 'retry insert fault'); END
        """
    )
    try:
        with pytest.raises(AttemptConflict, match="retry"):
            storage.attempt_repo.complete(
                attempt_id=claimed.attempt_id,
                owner_id="worker-1",
                fencing_token=claimed.fencing_token,
                outcome=_outcome(
                    AttemptDisposition.RETRY,
                    failure=_error(),
                    retry_at=_NOW + timedelta(seconds=5),
                ),
            )
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT status, owner_id, fencing_token FROM execution_attempts"
        ).fetchone()
        assert tuple(row) == ("claimed", "worker-1", claimed.fencing_token)
    finally:
        storage.close()


def test_restart_decode_fails_closed_and_rolls_back_claim(tmp_path: Path) -> None:
    database = tmp_path / "decode.db"
    storage = _open(database)
    _seed(storage)
    with storage.unit_of_work() as uow:
        initial = storage.attempt_repo.enqueue_initial(
            uow.connection, memorial_id="memorial-1", available_at=_NOW
        )
        uow.commit()
    storage._conn.execute("PRAGMA ignore_check_constraints=ON")  # noqa: SLF001
    storage._conn.execute(  # noqa: SLF001
        "UPDATE execution_attempts SET version='broken' WHERE attempt_id=?", (initial.attempt_id,)
    )
    storage._conn.commit()  # noqa: SLF001
    storage.close()

    restarted = _open(database)
    try:
        with pytest.raises(AttemptDecodeError, match="contract"):
            restarted.attempt_repo.claim(
                memorial_id="memorial-1",
                owner_id="worker",
                now=_NOW,
                lease_seconds=30,
            )
        row = restarted._conn.execute(  # noqa: SLF001
            "SELECT status, owner_id, fencing_token FROM execution_attempts"
        ).fetchone()
        assert tuple(row) == ("claimable", None, 0)
    finally:
        restarted.close()


def test_failure_persistence_is_redacted_and_never_serializes_extra_details(tmp_path: Path) -> None:
    storage = _open(tmp_path / "redaction.db")
    _seed(storage)
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection, memorial_id="memorial-1", available_at=_NOW
        )
        uow.commit()
    try:
        claimed = _claim(storage)
        error = _error(retryable=False)
        assert storage.attempt_repo.complete(
            attempt_id=claimed.attempt_id,
            owner_id="worker-1",
            fencing_token=claimed.fencing_token,
            outcome=_outcome(AttemptDisposition.FAILED, failure=error),
        )
        raw = storage._conn.execute(  # noqa: SLF001
            "SELECT failure_json FROM execution_attempts"
        ).fetchone()[0]
        assert json.loads(raw) == error.model_dump(mode="json")
        assert "secret" not in raw
    finally:
        storage.close()


def test_list_dispatchable_memorials_is_due_read_only_and_deterministic(tmp_path: Path) -> None:
    storage = _open(tmp_path / "dispatchable.db")
    storage.save_edict(Edict(id="edict-1", goal="test"))
    memorial_ids = (
        "expired",
        "due-a",
        "due-b",
        "future",
        "unexpired",
        "suspended",
        "terminal",
    )
    for memorial_id in memorial_ids:
        storage.save_memorial(Memorial(id=memorial_id, edict_id="edict-1", attempt=1))
    with storage.unit_of_work() as uow:
        available = {
            "expired": _NOW - timedelta(seconds=4),
            "due-a": _NOW - timedelta(seconds=3),
            "due-b": _NOW - timedelta(seconds=2),
            "future": _NOW + timedelta(seconds=1),
            "unexpired": _NOW - timedelta(seconds=5),
            "suspended": _NOW - timedelta(seconds=6),
            "terminal": _NOW - timedelta(seconds=7),
        }
        for memorial_id in memorial_ids:
            storage.attempt_repo.enqueue_initial(
                uow.connection,
                memorial_id=memorial_id,
                available_at=available[memorial_id],
            )
        uow.connection.execute(
            """
            UPDATE execution_attempts
            SET status='claimed', owner_id='old-worker', fencing_token=1,
                heartbeat_at=?, lease_expires_at=?
            WHERE memorial_id='expired'
            """,
            (
                (_NOW - timedelta(seconds=31)).isoformat(),
                (_NOW - timedelta(seconds=1)).isoformat(),
            ),
        )
        uow.connection.execute(
            """
            UPDATE execution_attempts
            SET status='claimed', owner_id='live-worker', fencing_token=1,
                heartbeat_at=?, lease_expires_at=?
            WHERE memorial_id='unexpired'
            """,
            (_NOW.isoformat(), (_NOW + timedelta(seconds=30)).isoformat()),
        )
        uow.connection.execute(
            "UPDATE execution_attempts SET status='suspended' WHERE memorial_id='suspended'"
        )
        uow.connection.execute(
            "UPDATE execution_attempts SET status='succeeded' WHERE memorial_id='terminal'"
        )
        uow.commit()

    try:
        before = storage._conn.total_changes  # noqa: SLF001
        assert storage.attempt_repo.list_dispatchable_memorial_ids(now=_NOW, limit=2) == (
            "expired",
            "due-a",
        )
        assert storage.attempt_repo.list_dispatchable_memorial_ids(now=_NOW, limit=10) == (
            "expired",
            "due-a",
            "due-b",
        )
        assert storage._conn.total_changes == before  # noqa: SLF001
    finally:
        storage.close()


def test_dispatchable_scan_excludes_manually_injected_dag_child(tmp_path: Path) -> None:
    storage = _open(tmp_path / "child-scan.db")
    storage.save_edict(Edict(id="edict-1", goal="test"))
    storage.save_memorial(Memorial(id="dag-child", edict_id="edict-1", dag_node_id="node-1"))
    with storage.unit_of_work() as uow:
        uow.connection.execute(
            """
            INSERT INTO execution_attempts (
                attempt_id, schema_version, memorial_id, attempt_no, status,
                owner_id, fencing_token, lease_expires_at, heartbeat_at,
                available_at, max_attempts, failure_json, version, created_at, updated_at
            ) VALUES (
                'child-attempt', 1, 'dag-child', 1, 'claimable',
                NULL, 0, NULL, NULL, ?, 3, NULL, 1, ?, ?
            )
            """,
            (_NOW.isoformat(), _NOW.isoformat(), _NOW.isoformat()),
        )
        uow.commit()
    try:
        assert storage.attempt_repo.list_dispatchable_memorial_ids(now=_NOW, limit=10) == ()
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT status, owner_id, fencing_token FROM execution_attempts"
        ).fetchone()
        assert tuple(row) == ("claimable", None, 0)
    finally:
        storage.close()


def test_require_current_fails_closed_for_stale_expired_and_rollback_authority(
    tmp_path: Path,
) -> None:
    storage = _open(tmp_path / "require-current.db")
    _seed(storage)
    with storage.unit_of_work() as uow:
        storage.attempt_repo.enqueue_initial(
            uow.connection,
            memorial_id="memorial-1",
            available_at=_NOW,
        )
        uow.commit()
    claimed = _claim(storage)
    try:
        with storage.unit_of_work() as uow:
            storage.attempt_repo.require_current(
                uow.connection,
                attempt_id=claimed.attempt_id,
                owner_id="worker-1",
                fencing_token=claimed.fencing_token,
                now=_NOW,
            )
            for values in (
                {"attempt_id": "missing"},
                {"owner_id": "stale-worker"},
                {"fencing_token": claimed.fencing_token + 1},
                {"now": _NOW - timedelta(microseconds=1)},
                {"now": claimed.lease_expires_at},
            ):
                arguments = {
                    "attempt_id": claimed.attempt_id,
                    "owner_id": "worker-1",
                    "fencing_token": claimed.fencing_token,
                    "now": _NOW,
                }
                arguments.update(values)
                with pytest.raises(AttemptFenceLost, match="no longer current") as error:
                    storage.attempt_repo.require_current(uow.connection, **arguments)  # type: ignore[arg-type]
                assert claimed.attempt_id not in str(error.value)
                assert "worker-1" not in str(error.value)
            uow.commit()
    finally:
        storage.close()
