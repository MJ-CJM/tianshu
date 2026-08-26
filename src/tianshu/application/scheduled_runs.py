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

from tianshu.models import EdictRuntime, EdictSchedule, Memorial, TaskStatus
from tianshu.models.attempt import AttemptStatus
from tianshu.models.canonical import canonical_sha256
from tianshu.storage.attempt_ledger import AttemptLeaseRepository
from tianshu.storage.memorial_repo import insert_memorial
from tianshu.storage.scheduler_repo import (
    compare_and_set_scheduler_cursor,
    insert_schedule_run,
    load_scheduler_job,
)
from tianshu.storage.unit_of_work import SqliteUnitOfWork
from tianshu.universe.router import (
    ChallengerRouter,
    FrozenContentViewUnavailable,
    GenerationBindingUnavailable,
)


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


class ScheduledFireBindingUnavailable(FrozenContentViewUnavailable):
    """A fire committed durably, but its enforced frozen binding was unavailable."""

    def __init__(self, prepared: PreparedFire) -> None:
        super().__init__("skills_view_unavailable")
        self.prepared = prepared


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
        challenger_router: ChallengerRouter,
        *,
        boundary_hook: Callable[[str], None] | None = None,
        require_runtime_binding: bool = False,
    ) -> None:
        if type(require_runtime_binding) is not bool:
            raise TypeError("require_runtime_binding must be a bool")
        self._unit_of_work_factory = unit_of_work_factory
        self._attempt_repository = attempt_repository
        self._challenger_router = challenger_router
        self._boundary_hook = boundary_hook
        self._require_runtime_binding = require_runtime_binding

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
                    """
                    SELECT goal, status, schedule_json, runtime_json, metadata_json
                    FROM edicts WHERE id = ?
                    """,
                    (job["edict_id"],),
                ).fetchone()
                if edict is None:
                    raise ScheduledFireConflict("scheduler edict does not exist")
                metadata = json.loads(edict["metadata_json"] or "{}")
                if edict["status"] != "open" or metadata.get("archived_at"):
                    raise ScheduledFireConflict("scheduler edict is no longer active")
                try:
                    schedule = EdictSchedule.model_validate(json.loads(edict["schedule_json"]))
                    runtime = EdictRuntime.model_validate(json.loads(edict["runtime_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ScheduledFireConflict("scheduler envelope is invalid") from exc
                max_attempts = runtime.retry_limit + 1
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
                    replay_fingerprint = self._fingerprint(
                        job=job,
                        schedule=schedule,
                        scheduled_at=scheduled_at,
                        next_run=next_run,
                        initial_memorial_id=initial_memorial_id,
                        status=str(existing["status"]),
                        max_attempts=max_attempts,
                    )
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
                        expected_fingerprint=replay_fingerprint,
                        expected_max_attempts=max_attempts,
                    )
                    if (
                        result.memorial_id is not None
                        and result.attempt_id is not None
                        and self._claimable_replay_needs_binding(
                            connection,
                            memorial_id=result.memorial_id,
                            attempt_id=result.attempt_id,
                        )
                    ):
                        self._challenger_router.assign_current(
                            unit_of_work,
                            memorial_id=result.memorial_id,
                            created_at=scheduled_at,
                        )
                        self._prebind_runtime_required(
                            unit_of_work,
                            memorial_id=result.memorial_id,
                            attempt_id=result.attempt_id,
                            runtime=runtime,
                        )
                    return self._commit_prepared(unit_of_work, result)

                persisted_cursor = job["next_run"]
                try:
                    cursor_matches = (
                        persisted_cursor is not None
                        and _utc(datetime.fromisoformat(str(persisted_cursor))) == scheduled_at
                    )
                except (TypeError, ValueError):
                    cursor_matches = False
                if job["status"] != "active" or not cursor_matches:
                    raise ScheduledFireConflict("scheduler cursor is no longer current")
                persisted_cursor_raw = str(persisted_cursor)

                if (
                    initial_memorial_id is not None
                    and connection.execute(
                        "SELECT 1 FROM schedule_run WHERE source = ? LIMIT 1",
                        (job_id,),
                    ).fetchone()
                    is not None
                ):
                    raise ScheduledFireConflict(
                        "initial Memorial is allowed only for the job's first fire"
                    )
                if initial_memorial_id is not None:
                    self._validate_initial_memorial(
                        connection,
                        job_id=job_id,
                        edict_id=str(job["edict_id"]),
                        memorial_id=initial_memorial_id,
                        goal=str(edict["goal"]),
                    )

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
                envelope_fingerprint = self._fingerprint(
                    job=job,
                    schedule=schedule,
                    scheduled_at=scheduled_at,
                    next_run=next_run,
                    initial_memorial_id=initial_memorial_id,
                    status=status,
                    max_attempts=max_attempts,
                )
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
                    self._challenger_router.assign_current(
                        unit_of_work,
                        memorial_id=memorial_id,
                        created_at=scheduled_at,
                    )
                    self._observe_boundary("after_memorial")
                    attempt = self._attempt_repository.enqueue_initial(
                        connection,
                        memorial_id=memorial_id,
                        available_at=scheduled_at,
                        max_attempts=max_attempts,
                        attempt_id=attempt_id,
                    )
                    prepared_attempt_id = attempt.attempt_id
                    self._prebind_runtime_required(
                        unit_of_work,
                        memorial_id=memorial_id,
                        attempt_id=prepared_attempt_id,
                        runtime=runtime,
                    )
                    self._observe_boundary("after_attempt")

                insert_schedule_run(
                    connection,
                    run_id=schedule_run_id,
                    source=job_id,
                    kind=str(job["schedule_type"]),
                    status=status,
                    edict_id=str(job["edict_id"]),
                    started_at=scheduled_at,
                    envelope_fingerprint=envelope_fingerprint,
                )
                self._observe_boundary("after_schedule_run")
                self._observe_boundary("before_cursor_cas")
                terminal = schedule.type in {"immediate", "once"}
                if not compare_and_set_scheduler_cursor(
                    connection,
                    job_id=job_id,
                    expected_next_run_raw=persisted_cursor_raw,
                    next_run=None if terminal else next_run,
                    status="completed" if terminal else "active",
                ):
                    raise ScheduledFireConflict("scheduler cursor changed during preparation")
                result = PreparedFire(
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
                return self._commit_prepared(unit_of_work, result)
        except sqlite3.IntegrityError as exc:
            raise ScheduledFireConflict("scheduled fire identity conflict") from exc

    def prepare_manual(
        self,
        *,
        job_id: str,
        idempotency_key: str,
        scheduled_at: datetime,
    ) -> PreparedFire:
        """Prepare an explicit run-now fire without moving the timer cursor."""
        if not job_id.strip() or not idempotency_key.strip():
            raise ValueError("manual fire identity must be non-blank")
        scheduled_at = _utc(scheduled_at)
        seed = f"{job_id}\0run-now\0{idempotency_key}"

        def identity(prefix: str) -> str:
            return f"{prefix}-{hashlib.sha256(seed.encode()).hexdigest()}"

        fire_id = identity("fire")
        schedule_run_id = identity("schedule-run")
        memorial_id = identity("memorial")
        attempt_id = identity("attempt")
        try:
            with self._unit_of_work_factory() as unit_of_work:
                connection = unit_of_work.connection
                job = load_scheduler_job(connection, job_id)
                if job is None or job["status"] not in {"active", "paused"}:
                    raise ScheduledFireConflict("manual scheduler job is unavailable")
                edict = connection.execute(
                    "SELECT goal, status, runtime_json, metadata_json FROM edicts WHERE id=?",
                    (job["edict_id"],),
                ).fetchone()
                if edict is None:
                    raise ScheduledFireConflict("manual scheduler edict is unavailable")
                try:
                    metadata = json.loads(edict["metadata_json"] or "{}")
                    runtime = EdictRuntime.model_validate(json.loads(edict["runtime_json"]))
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ScheduledFireConflict("manual scheduler envelope is invalid") from exc
                if edict["status"] != "open" or metadata.get("archived_at"):
                    raise ScheduledFireConflict("manual scheduler edict is no longer active")
                self._observe_boundary("after_manual_edict_validation")
                max_attempts = runtime.retry_limit + 1
                next_run = (
                    _utc(datetime.fromisoformat(str(job["next_run"])))
                    if job["next_run"] is not None
                    else None
                )
                identity_fingerprint = canonical_sha256(
                    {
                        "schema_version": 1,
                        "job_id": job_id,
                        "edict_id": str(job["edict_id"]),
                        "idempotency_key": idempotency_key,
                        "max_attempts": max_attempts,
                    }
                )
                existing = connection.execute(
                    "SELECT * FROM schedule_run WHERE id=?",
                    (schedule_run_id,),
                ).fetchone()
                if existing is not None:
                    try:
                        stored_envelope = json.loads(str(existing["error"]))
                        first_scheduled_at = _utc(
                            datetime.fromisoformat(str(stored_envelope["scheduled_at"]))
                        )
                        first_next_run = (
                            _utc(datetime.fromisoformat(str(stored_envelope["next_run"])))
                            if stored_envelope["next_run"] is not None
                            else None
                        )
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise ScheduledFireConflict(
                            "stored manual fire conflicts with envelope"
                        ) from exc
                    attempt = connection.execute(
                        "SELECT max_attempts FROM execution_attempts "
                        "WHERE attempt_id=? AND memorial_id=?",
                        (attempt_id, memorial_id),
                    ).fetchone()
                    if (
                        existing["source"] != job_id
                        or existing["kind"] != "run_now"
                        or existing["edict_id"] != job["edict_id"]
                        or stored_envelope.get("fingerprint") != identity_fingerprint
                        or _utc(datetime.fromisoformat(str(existing["started_at"])))
                        != first_scheduled_at
                        or attempt is None
                        or attempt["max_attempts"] != max_attempts
                    ):
                        raise ScheduledFireConflict("stored manual fire conflicts with envelope")
                    if self._claimable_replay_needs_binding(
                        connection,
                        memorial_id=memorial_id,
                        attempt_id=attempt_id,
                    ):
                        self._challenger_router.assign_current(
                            unit_of_work,
                            memorial_id=memorial_id,
                            created_at=first_scheduled_at,
                        )
                        self._prebind_runtime_required(
                            unit_of_work,
                            memorial_id=memorial_id,
                            attempt_id=attempt_id,
                            runtime=runtime,
                        )
                    result = PreparedFire(
                        fire_id=fire_id,
                        job_id=job_id,
                        edict_id=str(job["edict_id"]),
                        scheduled_at=first_scheduled_at,
                        next_run=first_next_run,
                        status="prepared",
                        memorial_id=memorial_id,
                        attempt_id=attempt_id,
                        schedule_run_id=schedule_run_id,
                        deduplicated=True,
                    )
                    return self._commit_prepared(unit_of_work, result)
                insert_memorial(
                    connection,
                    Memorial(
                        id=memorial_id,
                        edict_id=str(job["edict_id"]),
                        instruction=str(edict["goal"]),
                        status=TaskStatus.SUBMITTED,
                        created_at=scheduled_at,
                    ),
                )
                self._challenger_router.assign_current(
                    unit_of_work,
                    memorial_id=memorial_id,
                    created_at=scheduled_at,
                )
                self._attempt_repository.enqueue_initial(
                    connection,
                    memorial_id=memorial_id,
                    available_at=scheduled_at,
                    max_attempts=max_attempts,
                    attempt_id=attempt_id,
                )
                self._prebind_runtime_required(
                    unit_of_work,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    runtime=runtime,
                )
                insert_schedule_run(
                    connection,
                    run_id=schedule_run_id,
                    source=job_id,
                    kind="run_now",
                    status="prepared",
                    edict_id=str(job["edict_id"]),
                    started_at=scheduled_at,
                    envelope_fingerprint=json.dumps(
                        {
                            "fingerprint": identity_fingerprint,
                            "scheduled_at": scheduled_at.isoformat(),
                            "next_run": next_run.isoformat() if next_run is not None else None,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
                result = PreparedFire(
                    fire_id=fire_id,
                    job_id=job_id,
                    edict_id=str(job["edict_id"]),
                    scheduled_at=scheduled_at,
                    next_run=next_run,
                    status="prepared",
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                    schedule_run_id=schedule_run_id,
                    deduplicated=False,
                )
                return self._commit_prepared(unit_of_work, result)
        except sqlite3.IntegrityError as exc:
            raise ScheduledFireConflict("manual fire identity conflict") from exc

    def _claimable_replay_needs_binding(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str,
    ) -> bool:
        """Only complete missing truth for an attempt that has never started."""
        attempt = connection.execute(
            "SELECT status FROM execution_attempts WHERE attempt_id=? AND memorial_id=?",
            (attempt_id, memorial_id),
        ).fetchone()
        if attempt is None or attempt["status"] != AttemptStatus.CLAIMABLE.value:
            return False
        if self._challenger_router.requires_frozen_prebind_retry(
            connection,
            memorial_id=memorial_id,
            attempt_id=attempt_id,
        ):
            return True
        if (
            connection.execute(
                "SELECT 1 FROM run_system_bindings WHERE memorial_id=? AND attempt_id=? LIMIT 1",
                (memorial_id, attempt_id),
            ).fetchone()
            is not None
        ):
            return False
        generation_binding = connection.execute(
            "SELECT state FROM run_generation_bindings WHERE memorial_id=? AND attempt_id=?",
            (memorial_id, attempt_id),
        ).fetchone()
        if generation_binding is None:
            return False
        if generation_binding["state"] != "bound":
            raise GenerationBindingUnavailable("generation_binding_unavailable")
        return True

    @staticmethod
    def _commit_prepared(
        unit_of_work: SqliteUnitOfWork,
        prepared: PreparedFire,
    ) -> PreparedFire:
        try:
            unit_of_work.commit()
        except FrozenContentViewUnavailable as exc:
            raise ScheduledFireBindingUnavailable(prepared) from exc
        return prepared

    def _prebind_runtime_required(
        self,
        unit_of_work: SqliteUnitOfWork,
        *,
        memorial_id: str,
        attempt_id: str,
        runtime: EdictRuntime,
    ) -> None:
        requires_generation_selection = runtime.executor == "keqing:pi"
        binding = self._challenger_router.prebind_runtime_current(
            unit_of_work,
            memorial_id=memorial_id,
            attempt_id=attempt_id,
        )
        if unit_of_work.has_post_commit_failure:
            return
        if binding is None and requires_generation_selection:
            raise GenerationBindingUnavailable("generation_binding_unavailable")
        if self._require_runtime_binding and (binding is None or binding.system_snapshot is None):
            raise GenerationBindingUnavailable("generation_binding_unavailable")

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
        expected_fingerprint: str,
        expected_max_attempts: int,
    ) -> PreparedFire:
        if (
            row["source"] != job_id
            or row["kind"] != schedule.type
            or row["edict_id"] != edict_id
            or _utc(datetime.fromisoformat(str(row["started_at"]))) != scheduled_at
            or row["status"] not in {"prepared", "skipped"}
            or row["error"] != expected_fingerprint
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
                "SELECT attempt_id, max_attempts FROM execution_attempts "
                "WHERE attempt_id = ? AND memorial_id = ? AND attempt_no = 1",
                (expected_attempt_id, memorial_id),
            ).fetchone()
            if attempt is None or attempt["max_attempts"] != expected_max_attempts:
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
            "SELECT edict_id, instruction, status, dag_node_id, parent_memorial_id "
            "FROM memorials WHERE id = ?",
            (memorial_id,),
        ).fetchone()
        if existing is not None:
            if initial_memorial_id is None:
                raise ScheduledFireConflict("scheduled periodic Memorial already exists")
            self._validate_initial_memorial(
                connection,
                job_id=job_id,
                edict_id=edict_id,
                memorial_id=memorial_id,
                goal=goal,
            )
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
    def _is_bound_submission(
        connection: sqlite3.Connection,
        *,
        job_id: str,
        edict_id: str,
        memorial_id: str,
    ) -> bool:
        rows = connection.execute(
            """
            SELECT event_id
            FROM outbox_events
            WHERE event_type = 'edict.submitted'
              AND edict_id = ? AND memorial_id = ?
            """,
            (edict_id, memorial_id),
        ).fetchall()
        return any(_submission_job_id(str(row["event_id"])) == job_id for row in rows)

    @classmethod
    def _validate_initial_memorial(
        cls,
        connection: sqlite3.Connection,
        *,
        job_id: str,
        edict_id: str,
        memorial_id: str,
        goal: str,
    ) -> None:
        row = connection.execute(
            "SELECT edict_id, instruction, status, dag_node_id, parent_memorial_id "
            "FROM memorials WHERE id = ?",
            (memorial_id,),
        ).fetchone()
        if (
            row is None
            or row["edict_id"] != edict_id
            or row["instruction"] != goal
            or row["status"] != TaskStatus.SUBMITTED.value
            or row["dag_node_id"] is not None
            or row["parent_memorial_id"] is not None
            or not cls._is_bound_submission(
                connection,
                job_id=job_id,
                edict_id=edict_id,
                memorial_id=memorial_id,
            )
        ):
            raise ScheduledFireConflict("initial Memorial conflicts with durable envelope")

    @staticmethod
    def _fingerprint(
        *,
        job: sqlite3.Row,
        schedule: EdictSchedule,
        scheduled_at: datetime,
        next_run: datetime | None,
        initial_memorial_id: str | None,
        status: str,
        max_attempts: int,
    ) -> str:
        digest = canonical_sha256(
            {
                "schema_version": 1,
                "job": {
                    "job_id": str(job["job_id"]),
                    "edict_id": str(job["edict_id"]),
                    "schedule_type": str(job["schedule_type"]),
                    "cron_expr": job["cron_expr"],
                    "interval_seconds": job["interval_seconds"],
                },
                "edict_schedule": schedule.model_dump(mode="json", exclude_none=False),
                "scheduled_at": scheduled_at.isoformat(),
                "next_run": next_run.isoformat() if next_run is not None else None,
                "initial_memorial_id": initial_memorial_id,
                "status": status,
                "max_attempts": max_attempts,
            }
        )
        return f"fire-envelope-sha256:{digest}"

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


def _submission_job_id(event_id: str) -> str:
    digest = hashlib.sha256(event_id.encode()).hexdigest()
    return f"submitted-{digest}"


__all__ = [
    "PreparedFire",
    "ScheduledFireBindingUnavailable",
    "ScheduledFireConflict",
    "ScheduledRunPreparer",
]
