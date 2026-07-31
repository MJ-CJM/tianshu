"""Atomic preparation of stable scheduled execution fires."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

import pytest

from tianshu.application.scheduled_runs import (
    ScheduledFireConflict,
    ScheduledRunPreparer,
)
from tianshu.models import Edict, EdictRuntime, EdictSchedule, Memorial, TaskStatus
from tianshu.models.events import EventEnvelope
from tianshu.storage import EdictArchiveConflict, Storage
from tianshu.storage.outbox_repo import OutboxRepository

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


def _bind_submission(
    storage: Storage,
    *,
    memorial_id: str,
    event_id: str = "submitted-event-1",
) -> str:
    with storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="edict.submitted",
                edict_id="edict-1",
                memorial_id=memorial_id,
                timestamp=_NOW - timedelta(minutes=1),
                producer="test",
                payload={"goal": "scheduled work"},
            ),
        )
        unit_of_work.commit()
    digest = hashlib.sha256(event_id.encode()).hexdigest()
    return f"submitted-{digest}"


def test_initial_once_fire_reuses_submitted_root_and_replays_one_attempt(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "initial.db",
        schedule=EdictSchedule(type="once", at=_NOW),
    )
    root = Memorial(id="submitted-root", edict_id="edict-1", instruction="scheduled work")
    storage.save_memorial(root)
    job_id = _bind_submission(storage, memorial_id=root.id)
    storage.save_scheduler_job(
        job_id,
        "edict-1",
        "once",
        next_run=_NOW,
    )
    try:
        first = _preparer(storage).prepare(
            job_id=job_id,
            scheduled_at=_NOW,
            initial_memorial_id=root.id,
        )
        replay = _preparer(storage).prepare(
            job_id=job_id,
            scheduled_at=_NOW,
            initial_memorial_id=root.id,
        )

        assert first.memorial_id == root.id
        assert replay == first.model_copy(update={"deduplicated": True})
        assert storage.get_memorial(root.id).status is TaskStatus.SUBMITTED  # type: ignore[union-attr]
        assert storage.get_scheduler_job(job_id)["status"] == "completed"  # type: ignore[index]
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


def test_cron_cursor_preserves_local_time_across_dst_start(tmp_path: Path) -> None:
    """纽约进入夏令时后仍在当地 09:00 触发，而不是固定 UTC 小时。"""
    scheduled_at = datetime(2026, 3, 7, 14, 0, tzinfo=UTC)  # 09:00 EST
    schedule = EdictSchedule(
        type="cron",
        cron="0 9 * * *",
        timezone="America/New_York",
        concurrency_policy="allow",
    )
    storage = _open(tmp_path / "dst.db", schedule=schedule)
    storage.save_scheduler_job(
        "job-dst",
        "edict-1",
        "cron",
        cron_expr=schedule.cron,
        next_run=scheduled_at,
    )
    try:
        prepared = _preparer(storage).prepare(
            job_id="job-dst",
            scheduled_at=scheduled_at,
        )
        assert prepared.next_run == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)  # 09:00 EDT
    finally:
        storage.close()


def test_attempt_budget_comes_from_edict_retry_limit(tmp_path: Path) -> None:
    storage = Storage(str(tmp_path / "attempt-budget.db"))
    storage.init_db()
    storage.save_edict(
        Edict(
            id="edict-1",
            goal="scheduled work",
            schedule=EdictSchedule(type="once", at=_NOW),
            runtime=EdictRuntime(retry_limit=4),
        )
    )
    root = Memorial(id="submitted-root", edict_id="edict-1", instruction="scheduled work")
    storage.save_memorial(root)
    job_id = _bind_submission(storage, memorial_id=root.id)
    storage.save_scheduler_job(job_id, "edict-1", "once", next_run=_NOW)
    try:
        fire = _preparer(storage).prepare(
            job_id=job_id,
            scheduled_at=_NOW,
            initial_memorial_id=root.id,
        )
        row = storage._conn.execute(  # noqa: SLF001
            "SELECT max_attempts FROM execution_attempts WHERE attempt_id=?",
            (fire.attempt_id,),
        ).fetchone()
        assert row[0] == 5
    finally:
        storage.close()


def test_fire_rejects_cancelled_edict_before_creating_work(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "cancelled-edict.db",
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
    storage.update_edict_status("edict-1", "cancelled")
    try:
        with pytest.raises(ScheduledFireConflict, match="no longer active"):
            _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)
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
    finally:
        storage.close()


def test_manual_fire_is_idempotent_and_does_not_advance_periodic_cursor(
    tmp_path: Path,
) -> None:
    storage = _open(
        tmp_path / "manual.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="allow",
        ),
    )
    next_run = _NOW + timedelta(minutes=5)
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=next_run,
    )
    try:
        first = _preparer(storage).prepare_manual(
            job_id="job-1",
            idempotency_key="button-click-1",
            scheduled_at=_NOW,
        )
        replay = _preparer(storage).prepare_manual(
            job_id="job-1",
            idempotency_key="button-click-1",
            scheduled_at=_NOW,
        )

        assert replay == first.model_copy(update={"deduplicated": True})
        assert storage.get_scheduler_job("job-1")["next_run"] == next_run.isoformat()
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM execution_attempts WHERE memorial_id=?",
                (first.memorial_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        storage.close()


def test_manual_fire_replay_returns_first_envelope_when_retry_clock_differs(
    tmp_path: Path,
) -> None:
    storage = _open(
        tmp_path / "manual-retry-clock.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=3600,
            concurrency_policy="allow",
        ),
    )
    cursor = _NOW + timedelta(hours=1)
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=3600,
        next_run=cursor,
    )
    try:
        first = _preparer(storage).prepare_manual(
            job_id="job-1",
            idempotency_key="request-1",
            scheduled_at=_NOW,
        )
        replay = _preparer(storage).prepare_manual(
            job_id="job-1",
            idempotency_key="request-1",
            scheduled_at=_NOW + timedelta(minutes=5),
        )

        assert replay == first.model_copy(update={"deduplicated": True})
        assert storage.get_scheduler_job("job-1")["next_run"] == cursor.isoformat()
    finally:
        storage.close()


@pytest.mark.parametrize("closed_by", ["status", "archive"])
def test_manual_fire_rejects_non_active_edict_before_creating_work(
    tmp_path: Path,
    closed_by: str,
) -> None:
    storage = _open(
        tmp_path / f"manual-{closed_by}.db",
        schedule=EdictSchedule(type="interval", interval_seconds=60),
    )
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW + timedelta(minutes=1),
    )
    if closed_by == "status":
        storage.update_edict_status("edict-1", "cancelled")
    else:
        storage._conn.execute(  # noqa: SLF001
            "UPDATE edicts SET metadata_json = ? WHERE id = 'edict-1'",
            ('{"archived_at":"2026-07-16T08:00:00+00:00"}',),
        )
        storage._conn.commit()  # noqa: SLF001
    try:
        with pytest.raises(ScheduledFireConflict, match="no longer active"):
            _preparer(storage).prepare_manual(
                job_id="job-1",
                idempotency_key="closed-edict",
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
    finally:
        storage.close()


def test_manual_fire_and_archive_race_has_one_serialized_winner(tmp_path: Path) -> None:
    database = tmp_path / "manual-archive-race.db"
    storage = _open(
        database,
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
        next_run=_NOW + timedelta(minutes=1),
    )
    archiver = Storage(str(database))
    archiver.init_db()
    validated = Event()
    release = Event()
    results: dict[str, object] = {}

    def pause_after_validation(boundary: str) -> None:
        if boundary == "after_manual_edict_validation":
            validated.set()
            assert release.wait(timeout=5)

    def prepare() -> None:
        results["fire"] = _preparer(
            storage,
            boundary_hook=pause_after_validation,
        ).prepare_manual(
            job_id="job-1",
            idempotency_key="race-1",
            scheduled_at=_NOW,
        )

    def archive() -> None:
        try:
            archiver.tombstone_edict("edict-1")
        except EdictArchiveConflict:
            results["archive"] = "conflict"

    prepare_thread = Thread(target=prepare)
    archive_thread = Thread(target=archive)
    try:
        prepare_thread.start()
        assert validated.wait(timeout=5)
        archive_thread.start()
        release.set()
        prepare_thread.join(timeout=5)
        archive_thread.join(timeout=5)

        assert not prepare_thread.is_alive()
        assert not archive_thread.is_alive()
        assert results.get("fire") is not None
        assert results.get("archive") == "conflict"
        assert storage.get_edict("edict-1").status.value == "open"  # type: ignore[union-attr]
        assert (
            storage._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM memorials WHERE edict_id='edict-1'"
            ).fetchone()[0]
            == 1
        )
    finally:
        release.set()
        prepare_thread.join(timeout=5)
        archive_thread.join(timeout=5)
        archiver.close()
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
    storage.save_memorial(Memorial(id="root-1", edict_id="edict-1", instruction="scheduled work"))
    storage.save_memorial(Memorial(id="root-2", edict_id="edict-1"))
    job_id = _bind_submission(storage, memorial_id="root-1", event_id="conflict-event")
    with storage.unit_of_work() as unit_of_work:
        storage.attempt_repo.enqueue_initial(
            unit_of_work.connection,
            memorial_id="root-2",
            available_at=_NOW,
        )
        unit_of_work.commit()
    storage.save_scheduler_job(job_id, "edict-1", "once", next_run=_NOW)
    try:
        _preparer(storage).prepare(
            job_id=job_id,
            scheduled_at=_NOW,
            initial_memorial_id="root-1",
        )
        with pytest.raises(ScheduledFireConflict, match="envelope"):
            _preparer(storage).prepare(
                job_id=job_id,
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
    job_id = _bind_submission(
        storage,
        memorial_id="submitted-root",
        event_id="once-conflict-event",
    )
    storage.save_scheduler_job(job_id, "edict-1", "once", next_run=_NOW)
    try:
        with pytest.raises(ScheduledFireConflict, match="terminal scheduler envelope"):
            _preparer(storage).prepare(
                job_id=job_id,
                scheduled_at=_NOW,
                initial_memorial_id="submitted-root",
            )
    finally:
        storage.close()


def test_replay_rejects_edict_schedule_config_drift_and_persists_fingerprint(
    tmp_path: Path,
) -> None:
    storage = _open(
        tmp_path / "fingerprint.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            timezone="UTC",
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
        fingerprint = storage._conn.execute(  # noqa: SLF001
            "SELECT error FROM schedule_run WHERE id = ?",
            (prepared.schedule_run_id,),
        ).fetchone()[0]
        assert isinstance(fingerprint, str)
        assert fingerprint.startswith("fire-envelope-sha256:")
        assert len(fingerprint.removeprefix("fire-envelope-sha256:")) == 64

        drifted = EdictSchedule(
            type="interval",
            interval_seconds=60,
            timezone="Asia/Shanghai",
            concurrency_policy="allow",
        )
        storage._conn.execute(  # noqa: SLF001
            "UPDATE edicts SET schedule_json = ? WHERE id = 'edict-1'",
            (drifted.model_dump_json(),),
        )
        storage._conn.commit()  # noqa: SLF001
        with pytest.raises(ScheduledFireConflict, match="envelope"):
            _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)

        original = EdictSchedule(
            type="interval",
            interval_seconds=60,
            timezone="UTC",
            concurrency_policy="allow",
        )
        storage._conn.execute(  # noqa: SLF001
            "UPDATE edicts SET schedule_json = ? WHERE id = 'edict-1'",
            (original.model_dump_json(),),
        )
        storage._conn.execute(  # noqa: SLF001
            "UPDATE scheduler_jobs SET cron_expr = 'tampered' WHERE job_id = 'job-1'"
        )
        storage._conn.commit()  # noqa: SLF001
        with pytest.raises(ScheduledFireConflict, match="envelope"):
            _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)
    finally:
        storage.close()


def test_skipped_replay_rejects_changed_initial_memorial_envelope(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "skipped-envelope.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="skip",
        ),
    )
    storage.save_memorial(Memorial(id="active-root", edict_id="edict-1", status=TaskStatus.RUNNING))
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )
    try:
        skipped = _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)
        assert skipped.status == "skipped"
        with pytest.raises(ScheduledFireConflict, match="envelope"):
            _preparer(storage).prepare(
                job_id="job-1",
                scheduled_at=_NOW,
                initial_memorial_id="attacker-root",
            )
    finally:
        storage.close()


def test_skipped_first_fire_still_rejects_unbound_initial_memorial(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "skipped-unbound-initial.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="skip",
        ),
    )
    storage.save_memorial(Memorial(id="active-root", edict_id="edict-1", status=TaskStatus.RUNNING))
    storage.save_memorial(
        Memorial(id="attacker-root", edict_id="edict-1", instruction="scheduled work")
    )
    storage.save_scheduler_job(
        "job-1",
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )
    try:
        with pytest.raises(ScheduledFireConflict, match="initial Memorial"):
            _preparer(storage).prepare(
                job_id="job-1",
                scheduled_at=_NOW,
                initial_memorial_id="attacker-root",
            )
        assert storage._conn.execute("SELECT COUNT(*) FROM schedule_run").fetchone()[0] == 0  # noqa: SLF001
    finally:
        storage.close()


def test_equivalent_offset_cursor_is_compared_canonically_but_cas_uses_raw_text(
    tmp_path: Path,
) -> None:
    storage = _open(
        tmp_path / "offset-cursor.db",
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
    raw_cursor = _NOW.astimezone(timezone(timedelta(hours=8))).isoformat()
    storage._conn.execute(  # noqa: SLF001
        "UPDATE scheduler_jobs SET next_run = ? WHERE job_id = 'job-1'",
        (raw_cursor,),
    )
    storage._conn.commit()  # noqa: SLF001
    try:
        prepared = _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)
        assert prepared.next_run == _NOW + timedelta(seconds=60)
        durable = storage.get_scheduler_job("job-1")
        assert durable is not None
        assert durable["next_run"] == (_NOW + timedelta(seconds=60)).isoformat()
    finally:
        storage.close()


@pytest.mark.parametrize(
    ("status", "instruction", "event_memorial"),
    [
        (TaskStatus.RUNNING, "scheduled work", "submitted-root"),
        (TaskStatus.SUBMITTED, "tampered work", "submitted-root"),
        (TaskStatus.SUBMITTED, "scheduled work", "another-root"),
    ],
)
def test_initial_root_requires_exact_submitted_identity_and_envelope(
    tmp_path: Path,
    status: TaskStatus,
    instruction: str,
    event_memorial: str,
) -> None:
    storage = _open(
        tmp_path / f"initial-root-{status.value}-{event_memorial}.db",
        schedule=EdictSchedule(type="once", at=_NOW),
    )
    storage.save_memorial(
        Memorial(
            id="submitted-root",
            edict_id="edict-1",
            instruction=instruction,
            status=status,
        )
    )
    if event_memorial != "submitted-root":
        storage.save_memorial(
            Memorial(
                id=event_memorial,
                edict_id="edict-1",
                instruction="scheduled work",
            )
        )
    job_id = _bind_submission(
        storage,
        memorial_id=event_memorial,
        event_id=f"event-{status.value}-{event_memorial}",
    )
    storage.save_scheduler_job(job_id, "edict-1", "once", next_run=_NOW)
    try:
        with pytest.raises(ScheduledFireConflict, match="initial Memorial"):
            _preparer(storage).prepare(
                job_id=job_id,
                scheduled_at=_NOW,
                initial_memorial_id="submitted-root",
            )
    finally:
        storage.close()


def test_initial_memorial_is_rejected_for_later_periodic_fire(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "late-initial.db",
        schedule=EdictSchedule(
            type="interval",
            interval_seconds=60,
            concurrency_policy="allow",
        ),
    )
    storage.save_memorial(
        Memorial(id="submitted-root", edict_id="edict-1", instruction="scheduled work")
    )
    job_id = _bind_submission(storage, memorial_id="submitted-root", event_id="periodic-event")
    storage.save_scheduler_job(
        job_id,
        "edict-1",
        "interval",
        interval_seconds=60,
        next_run=_NOW,
    )
    try:
        _preparer(storage).prepare(
            job_id=job_id,
            scheduled_at=_NOW,
            initial_memorial_id="submitted-root",
        )
        storage.save_memorial(
            Memorial(id="attacker-root", edict_id="edict-1", instruction="scheduled work")
        )
        with pytest.raises(ScheduledFireConflict, match="first fire"):
            _preparer(storage).prepare(
                job_id=job_id,
                scheduled_at=_NOW + timedelta(seconds=60),
                initial_memorial_id="attacker-root",
            )
    finally:
        storage.close()


def test_preexisting_hash_derived_periodic_root_is_not_adopted(tmp_path: Path) -> None:
    storage = _open(
        tmp_path / "preexisting-periodic-root.db",
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
    digest = hashlib.sha256(f"job-1\0{_NOW.isoformat()}".encode()).hexdigest()
    storage.save_memorial(
        Memorial(
            id=f"memorial-{digest}",
            edict_id="edict-1",
            instruction="scheduled work",
        )
    )
    try:
        with pytest.raises(ScheduledFireConflict, match="already exists"):
            _preparer(storage).prepare(job_id="job-1", scheduled_at=_NOW)
    finally:
        storage.close()
