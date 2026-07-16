"""Atomic preparation of stable scheduled execution fires."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.application.scheduled_runs import (
    ScheduledFireConflict,
    ScheduledRunPreparer,
)
from tianshu.models import Edict, EdictSchedule, Memorial, TaskStatus
from tianshu.storage import Storage

_NOW = datetime(2026, 7, 16, 8, tzinfo=UTC)


def _open(path: Path, *, schedule: EdictSchedule) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    storage.save_edict(Edict(id="edict-1", goal="scheduled work", schedule=schedule))
    return storage


def _preparer(
    storage: Storage,
    *,
    boundary_hook: Callable[[str], None] | None = None,
) -> ScheduledRunPreparer:
    return ScheduledRunPreparer(
        storage.unit_of_work,
        storage.attempt_repo,
        boundary_hook=boundary_hook,
    )


def test_initial_once_fire_reuses_submitted_root_and_replays_one_attempt(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "initial.db",
        schedule=EdictSchedule(type="once", at=_NOW),
    )
    root = Memorial(id="submitted-root", edict_id="edict-1", instruction="scheduled work")
    storage.save_memorial(root)
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "once",
        next_run=_NOW,
    )
    try:
        first = _preparer(storage).prepare(
            job_id="job-1",
            scheduled_at=_NOW,
            initial_memorial_id=root.id,
        )
        replay = _preparer(storage).prepare(
            job_id="job-1",
            scheduled_at=_NOW,
            initial_memorial_id=root.id,
        )

        assert first.memorial_id == root.id
        assert replay == first.model_copy(update={"deduplicated": True})
        assert storage.get_memorial(root.id).status is TaskStatus.SUBMITTED  # type: ignore[union-attr]
        assert storage.get_scheduler_job("job-1")["status"] == "cancelled"  # type: ignore[index]
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM memorials WHERE edict_id='edict-1'"
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts WHERE memorial_id=?", (root.id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run WHERE id=?", (first.schedule_run_id,)
            ).fetchone()[0]
            == 1
        )
    finally:
        storage.close()


def test_periodic_fire_identity_is_stable_and_interval_cursor_uses_scheduled_time(
    tmp_path: Path,
) -> None:
    storage = _open(
        tmp_path / "periodic.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="allow",
        ),
    )
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )
    try:
        first = _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)
        second_time = _NOW + timedelta(seconds=60)
        second = _preparer(storage).prepare(job_id="job-1", scheduled_at=second_time)
        replay = _preparer(storage).prepare(job_id="job-1", scheduled_at=second_time)

        assert first.fire_id != second.fire_id
        assert first.memorial_id != second.memorial_id
        assert first.attempt_id != second.attempt_id
        assert replay == second.model_copy(update={"deduplicated": True})
        assert second.next_run == _NOW + timedelta(seconds=120)
        assert datetime.fromisoformat(storage.get_scheduler_job("job-1")["next_run"]) == (  # type: ignore[index]
            _NOW + timedelta(seconds=120)
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run WHERE source='job-1'"
            ).fetchone()[0]
            == 2
        )
    finally:
        storage.close()


@pytest.mark.parametrize(
    "boundary",
    ["after_memorial", "after_attempt", "after_schedule_run", "before_cursor_cas"],
)
def test_failure_at_each_preparation_boundary_rolls_back_every_projection(
    tmp_path: Path,
    boundary: str,
) -> None:
    storage = _open(
        tmp_path / f"rollback-{boundary}.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="allow",
        ),
    )
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )

    def fail_at(observed: str) -> None:
        if observed == boundary:
            raise RuntimeError("injected preparation failure")

    try:
        with pytest.raises(RuntimeError, match="injected preparation failure"):
            _preparer(storage, boundary_hook=fail_at).prepare(
                job_id="job-1",
                scheduled_at=_NOW,
            )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM memorials WHERE edict_id='edict-1'"
            ).fetchone()[0]
            == 0
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts"
            ).fetchone()[0]
            == 0
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run"
            ).fetchone()[0]
            == 0
        )
        assert datetime.fromisoformat(storage.get_scheduler_job("job-1")["next_run"]) == _NOW  # type: ignore[index]
    finally:
        storage.close()


def test_skip_is_atomic_advances_cursor_and_creates_no_root_or_attempt(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "skip.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="skip",
        ),
    )
    storage.save_memorial(
        Memorial(
            id="existing-root",
            edict_id="edict-1",
            status=TaskStatus.RUNNING,
        )
    )
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )
    try:
        skipped = _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)
        replay = _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)

        assert skipped.status == "skipped"
        assert skipped.memorial_id is None
        assert skipped.attempt_id is None
        assert replay == skipped.model_copy(update={"deduplicated": True})
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM memorials WHERE edict_id='edict-1'"
            ).fetchone()[0]
            == 1
        )
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts"
            ).fetchone()[0]
            == 0
        )
    finally:
        storage.close()


def test_same_identity_with_different_initial_root_conflicts(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "conflict.db",
        schedule=EdictSchedule(type="once", at=_NOW),
    )
    storage.save_memorial(Memorial(id="root-1", edict_id="edict-1"))
    storage.save_memorial(Memorial(id="root-2", edict_id="edict-1"))
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="root-2",
            available_at=_NOW,
        )
        unit_of_work.commit()
    storage.save_scheduler_job("job-1", "edict-1", "once", next_run=_NOW)
    try:
        _preparer(storage).prepare(
            job_id="job-1",
            scheduled_at=_NOW,
            initial_memorial_id="root-1",
        )
        with pytest.raises(ScheduledFireConflict, match="envelope"):
            _preparer(storage).prepare(
                job_id="job-1",
                scheduled_at=_NOW,
                initial_memorial_id="root-2",
            )
    finally:
        storage.close()


def test_two_connections_racing_one_cursor_reuse_one_durable_fire(tmp_path: Path) -> None:
    database = tmp_path / "race.db"
    first_storage = _open(
        database,
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="allow",
        ),
    )
    first_storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )
    second_storage = Storage(str(database))
    second_storage.init_db()
    try:
        first = _preparer(first_storage).prepare(job_id="job-1", scheduled_at=_NOW)
        replay = _preparer(second_storage).prepare(job_id="job-1", scheduled_at=_NOW)

        assert replay == first.model_copy(update={"deduplicated": True})
        assert (
            first_storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM schedule_run WHERE source='job-1'"
            ).fetchone()[0]
            == 1
        )
        assert (
            first_storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts"
            ).fetchone()[0]
            == 1
        )
    finally:
        second_storage.close()
        first_storage.close()


def test_committed_fire_is_visible_to_attempt_recovery(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "visible.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="allow",
        ),
    )
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )
    try:
        prepared = _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)

        assert prepared.memorial_id is not None
        assert storage.attempt_repo.list_dispatchable_memorial_ids(now=_NOW, limit=10) == (
            prepared.memorial_id,
        )
    finally:
        storage.close()


def test_cron_next_cursor_is_derived_from_persisted_fire_not_wall_clock(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "cron.db",
        schedule=EdictSchedule(
            type="cron",
            cron="0 * * * *",
            timezone="UTC",
            concurrency_policy="allow",
        ),
    )
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "cron",
        cron_expr="0 * * * *",
        next_run=_NOW,
    )
    expected_next = _NOW + timedelta(hours=1)
    try:
        prepared = _preparer(storage).prepare(
            job_id="job-1",
            scheduled_at=_NOW,
            next_scheduled_at=expected_next,
        )
        assert prepared.next_run == expected_next
        with pytest.raises(ScheduledFireConflict, match="next cursor"):
            _preparer(storage).prepare(
                job_id="job-1",
                scheduled_at=_NOW,
                next_scheduled_at=expected_next + timedelta(hours=1),
            )
    finally:
        storage.close()


def test_interval_job_must_match_persisted_edict_envelope(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "interval-conflict.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="allow",
        ),
    )
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=120,
        next_run=_NOW,
    )
    try:
        with pytest.raises(ScheduledFireConflict, match="interval scheduler envelope"):
            _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)
    finally:
        storage.close()


def test_once_job_cursor_must_match_persisted_edict_time(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "once-conflict.db",
        schedule=EdictSchedule(type="once", at=_NOW + timedelta(minutes=1)),
    )
    storage.save_memorial(Memorial(id="submitted-root", edict_id="edict-1"))
    storage.save_scheduler_job("job-1", "edict-1", "once", next_run=_NOW)
    try:
        with pytest.raises(ScheduledFireConflict, match="terminal scheduler envelope"):
            _preparer(storage).prepare(
                job_id="job-1",
                scheduled_at=_NOW,
                initial_memorial_id="submitted-root",
            )
    finally:
        storage.close()
