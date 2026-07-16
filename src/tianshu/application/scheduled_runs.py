"""Atomic durable preparation for stable scheduled execution fires."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, ConfigDict

from tianshu.models import EdictSchedule, Memorial, TaskStatus
from tianshu.storage.attempt_ledger import AttemptLeaseRepository
from tianshu.storage.memorial_repo import insert_memorial
from tianshu.storage.scheduler_repo import (
    compare_and_set_scheduler_cursor,
    insert_schedule_run,
    load_scheduler_job,
)
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class ScheduledFireConflict(RuntimeError):
    """A fire identity or job cursor conflicts with durable scheduler truth."""


class PreparedFire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    fire_id: str
    job_id: str
    edict_id: str
    scheduled_at: datetime
    next_run: datetime | None
    status: str
    memorial_id: str | None
    attempt_id: str | None
    schedule_run_id: str
    deduplicated: bool


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("scheduled timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _identity(prefix: str, job_id: str, scheduled_at: datetime) -> str:
    digest = hashlib.sha256(f"{job_id}\0{scheduled_at.isoformat()}".encode()).hexdigest()
    return f"{prefix}-{digest}"


class ScheduledRunPreparer:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], SqliteUnitOfWork],
        attempt_repository: AttemptLeaseRepository,
        *,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._attempt_repository = attempt_repository
        self._boundary_hook = boundary_hook

    def prepare(
        self,
        *,
        job_id: str,
        scheduled_at: datetime,
        initial_memorial_id: str | None = None,
        next_scheduled_at: datetime | None = None,
    ) -> PreparedFire:
        if not job_id.strip():
            raise ValueError("job_id must be non-blank")
        scheduled_at = _utc(scheduled_at)
        if next_scheduled_at is not None:
            next_scheduled_at = _utc(next_scheduled_at)
        fire_id = _identity("fire", job_id, scheduled_at)
        schedule_run_id = _identity("schedule-run", job_id, scheduled_at)
        attempt_id = _identity("attempt", job_id, scheduled_at)

        try:
            with self._unit_of_work_factory() as unit_of_work:
                connection = unit_of_work.connection
                job = load_scheduler_job(connection, job_id)
                if job is None:
                    raise ScheduledFireConflict("scheduler job does not exist")
                edict = connection.execute(
                    "SELECT goal, schedule_json FROM edicts WHERE id = ?",
                    (job["edict_id"],),
                ).fetchone()
                if edict is None:
                    raise ScheduledFireConflict("scheduler edict does not exist")
                try:
                    schedule = EdictSchedule.model_validate(json.loads(edict["schedule_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ScheduledFireConflict("scheduler envelope is invalid") from exc
                if schedule.type != job["schedule_type"]:
                    raise ScheduledFireConflict("scheduler envelope conflicts with durable job")
                next_run = self._next_cursor(
                    job=job,
                    schedule=schedule,
                    scheduled_at=scheduled_at,
                    requested=next_scheduled_at,
                )
                existing = connection.execute(
                    "SELECT * FROM schedule_run WHERE id = ?",
                    (schedule_run_id,),
                ).fetchone()
                if existing is not None:
                    result = self._resolve_replay(
                        connection,
                        row=existing,
                        fire_id=fire_id,
                        schedule_run_id=schedule_run_id,
                        expected_attempt_id=attempt_id,
                        job_id=job_id,
                        edict_id=str(job["edict_id"]),
                        scheduled_at=scheduled_at,
                        next_run=next_run,
                        initial_memorial_id=initial_memorial_id,
                        schedule=schedule,
                    )
                    unit_of_work.commit()
                    return result

                persisted_cursor = job["next_run"]
                if (
                    job["status"] != "active"
                    or persisted_cursor is None
                    or _utc(datetime.fromisoformat(str(persisted_cursor))) != scheduled_at
                ):
                    raise ScheduledFireConflict("scheduler cursor is no longer current")

                skip = (
                    schedule.type in {"cron", "interval"}
                    and schedule.concurrency_policy == "skip"
                    and self._has_active_root(
                        connection,
                        edict_id=str(job["edict_id"]),
                        excluded_memorial_id=initial_memorial_id,
                    )
                )
                memorial_id: str | None = None
                prepared_attempt_id: str | None = None
                status = "skipped" if skip else "prepared"
                if not skip:
                    memorial_id = self._prepare_memorial(
                        connection,
                        job_id=job_id,
                        edict_id=str(job["edict_id"]),
                        goal=str(edict["goal"]),
                        scheduled_at=scheduled_at,
                        initial_memorial_id=initial_memorial_id,
                        schedule=schedule,
                    )
                    self._observe_boundary("after_memorial")
                    attempt = self._attempt_repository.enqueue_initial(
                        connection,
                        memorial_id=memorial_id,
                        available_at=scheduled_at,
                        attempt_id=attempt_id,
                    )
                    prepared_attempt_id = attempt.attempt_id
                    self._observe_boundary("after_attempt")

                insert_schedule_run(
                    connection,
                    run_id=schedule_run_id,
                    source=job_id,
                    kind=str(job["schedule_type"]),
                    status=status,
                    edict_id=str(job["edict_id"]),
                    started_at=scheduled_at,
                )
                self._observe_boundary("after_schedule_run")
                self._observe_boundary("before_cursor_cas")
                terminal = schedule.type in {"immediate", "once"}
                if not compare_and_set_scheduler_cursor(
                    connection,
                    job_id=job_id,
                    expected_next_run=scheduled_at,
                    next_run=None if terminal else next_run,
                    status="cancelled" if terminal else "active",
                ):
                    raise ScheduledFireConflict("scheduler cursor changed during preparation")
                unit_of_work.commit()
                return PreparedFire(
                    fire_id=fire_id,
                    job_id=job_id,
                    edict_id=str(job["edict_id"]),
                    scheduled_at=scheduled_at,
                    next_run=None if terminal else next_run,
                    status=status,
                    memorial_id=memorial_id,
                    attempt_id=prepared_attempt_id,
                    schedule_run_id=schedule_run_id,
                    deduplicated=False,
                )
        except sqlite3.IntegrityError as exc:
            raise ScheduledFireConflict("scheduled fire identity conflict") from exc

    def _next_cursor(
        self,
        *,
        job: sqlite3.Row,
        schedule: EdictSchedule,
        scheduled_at: datetime,
        requested: datetime | None,
    ) -> datetime | None:
        if schedule.type in {"immediate", "once"}:
            if requested is not None:
                raise ScheduledFireConflict("terminal fire cannot have a next cursor")
            if schedule.type == "once" and (
                schedule.at is None or _utc(schedule.at) != scheduled_at
            ):
                raise ScheduledFireConflict("terminal scheduler envelope is invalid")
            return None
        if schedule.type == "interval":
            seconds = job["interval_seconds"]
            if type(seconds) is not int or seconds <= 0 or seconds != schedule.interval_seconds:
                raise ScheduledFireConflict("interval scheduler envelope is invalid")
            derived = scheduled_at + timedelta(seconds=seconds)
            if requested is not None and requested != derived:
                raise ScheduledFireConflict("next cursor conflicts with interval envelope")
            return derived
        expression = job["cron_expr"]
        if not isinstance(expression, str) or expression != schedule.cron:
            raise ScheduledFireConflict("cron scheduler envelope is invalid")
        try:
            timezone = ZoneInfo(schedule.timezone) if schedule.timezone != "UTC" else UTC
            derived = (
                croniter(
                    expression,
                    scheduled_at.astimezone(timezone),
                )
                .get_next(datetime)
                .astimezone(UTC)
            )
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
            raise ScheduledFireConflict("cron scheduler envelope is invalid") from exc
        if requested is not None and requested != derived:
            raise ScheduledFireConflict("next cursor conflicts with cron envelope")
        return derived

    def _resolve_replay(
        self,
        connection: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        fire_id: str,
        schedule_run_id: str,
        expected_attempt_id: str,
        job_id: str,
        edict_id: str,
        scheduled_at: datetime,
        next_run: datetime | None,
        initial_memorial_id: str | None,
        schedule: EdictSchedule,
    ) -> PreparedFire:
        if (
            row["source"] != job_id
            or row["kind"] != schedule.type
            or row["edict_id"] != edict_id
            or _utc(datetime.fromisoformat(str(row["started_at"]))) != scheduled_at
            or row["status"] not in {"prepared", "skipped"}
        ):
            raise ScheduledFireConflict("stored fire conflicts with durable envelope")
        if row["status"] == "skipped":
            memorial_id = None
            attempt_id = None
        else:
            memorial_id = self._expected_memorial_id(
                job_id=job_id,
                scheduled_at=scheduled_at,
                initial_memorial_id=initial_memorial_id,
                schedule=schedule,
            )
            attempt = connection.execute(
                "SELECT attempt_id FROM execution_attempts "
                "WHERE attempt_id = ? AND memorial_id = ? AND attempt_no = 1",
                (expected_attempt_id, memorial_id),
            ).fetchone()
            if attempt is None:
                raise ScheduledFireConflict("stored fire conflicts with durable envelope")
            attempt_id = str(attempt["attempt_id"])
        return PreparedFire(
            fire_id=fire_id,
            job_id=job_id,
            edict_id=edict_id,
            scheduled_at=scheduled_at,
            next_run=next_run,
            status=str(row["status"]),
            memorial_id=memorial_id,
            attempt_id=attempt_id,
            schedule_run_id=schedule_run_id,
            deduplicated=True,
        )

    def _prepare_memorial(
        self,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        edict_id: str,
        goal: str,
        scheduled_at: datetime,
        initial_memorial_id: str | None,
        schedule: EdictSchedule,
    ) -> str:
        memorial_id = self._expected_memorial_id(
            job_id=job_id,
            scheduled_at=scheduled_at,
            initial_memorial_id=initial_memorial_id,
            schedule=schedule,
        )
        existing = connection.execute(
            "SELECT edict_id, dag_node_id FROM memorials WHERE id = ?",
            (memorial_id,),
        ).fetchone()
        if existing is not None:
            if existing["edict_id"] != edict_id or existing["dag_node_id"] is not None:
                raise ScheduledFireConflict("initial Memorial conflicts with durable envelope")
            return memorial_id
        if initial_memorial_id is not None:
            raise ScheduledFireConflict("initial Memorial does not exist")
        insert_memorial(
            connection,
            Memorial(
                id=memorial_id,
                edict_id=edict_id,
                instruction=goal,
                status=TaskStatus.SUBMITTED,
                created_at=scheduled_at,
            ),
        )
        return memorial_id

    @staticmethod
    def _expected_memorial_id(
        *,
        job_id: str,
        scheduled_at: datetime,
        initial_memorial_id: str | None,
        schedule: EdictSchedule,
    ) -> str:
        if initial_memorial_id is not None:
            return initial_memorial_id
        if schedule.type in {"immediate", "once"}:
            raise ScheduledFireConflict("initial fire requires its submitted Memorial")
        return _identity("memorial", job_id, scheduled_at)

    @staticmethod
    def _has_active_root(
        connection: sqlite3.Connection,
        *,
        edict_id: str,
        excluded_memorial_id: str | None,
    ) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM memorials WHERE edict_id = ? AND dag_node_id IS NULL "
                "AND status NOT IN ('completed', 'failed', 'cancelled') "
                "AND (? IS NULL OR id != ?) LIMIT 1",
                (edict_id, excluded_memorial_id, excluded_memorial_id),
            ).fetchone()
            is not None
        )

    def _observe_boundary(self, boundary: str) -> None:
        if self._boundary_hook is not None:
            self._boundary_hook(boundary)


__all__ = ["PreparedFire", "ScheduledFireConflict", "ScheduledRunPreparer"]
