"""Storage Scheduler 领域 Mixin —— 调度任务（scheduler_jobs）与调度台账（schedule_run）。"""

import sqlite3
import threading
from datetime import UTC, datetime

from ulid import ULID


def load_scheduler_job(connection: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    """Load one scheduler cursor on a caller-owned transaction."""
    return connection.execute(
        "SELECT * FROM scheduler_jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()


def insert_schedule_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    source: str,
    kind: str,
    status: str,
    edict_id: str,
    started_at: datetime,
    envelope_fingerprint: str,
) -> None:
    """Insert one deterministic schedule-run row in the caller's transaction."""
    connection.execute(
        "INSERT INTO schedule_run (id, source, kind, status, edict_id, error, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            source,
            kind,
            status,
            edict_id,
            envelope_fingerprint,
            started_at.astimezone(UTC).isoformat(),
        ),
    )


def compare_and_set_scheduler_cursor(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    expected_next_run_raw: str,
    next_run: datetime | None,
    status: str,
) -> bool:
    """Advance exactly one active scheduler cursor."""
    cursor = connection.execute(
        """
        UPDATE scheduler_jobs
        SET next_run = ?, status = ?
        WHERE job_id = ? AND status = 'active' AND next_run = ?
        """,
        (
            next_run.astimezone(UTC).isoformat() if next_run is not None else None,
            status,
            job_id,
            expected_next_run_raw,
        ),
    )
    return cursor.rowcount == 1


class SchedulerMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Scheduler Jobs ---

    def save_scheduler_job(
        self,
        job_id: str,
        edict_id: str,
        schedule_type: str,
        cron_expr: str | None = None,
        next_run: datetime | None = None,
        interval_seconds: int | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO scheduler_jobs
                   (job_id, edict_id, schedule_type, cron_expr, next_run, status, created_at, interval_seconds)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    job_id,
                    edict_id,
                    schedule_type,
                    cron_expr,
                    next_run.isoformat() if next_run else None,
                    datetime.now(UTC).isoformat(),
                    interval_seconds,
                ),
            )

    def save_scheduler_job_if_absent(
        self,
        job_id: str,
        edict_id: str,
        schedule_type: str,
        cron_expr: str | None = None,
        next_run: datetime | None = None,
        interval_seconds: int | None = None,
    ) -> bool:
        """Reserve a durable job ID once without replacing a replayed effect."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """INSERT OR IGNORE INTO scheduler_jobs
                   (job_id, edict_id, schedule_type, cron_expr, next_run, status, created_at, interval_seconds)
                   VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    job_id,
                    edict_id,
                    schedule_type,
                    cron_expr,
                    next_run.isoformat() if next_run else None,
                    datetime.now(UTC).isoformat(),
                    interval_seconds,
                ),
            )
        return cursor.rowcount == 1

    def update_scheduler_job_next_run(self, job_id: str, next_run: datetime | None) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE scheduler_jobs SET next_run = ? WHERE job_id = ?",
                (next_run.isoformat() if next_run else None, job_id),
            )

    def set_scheduler_job_status(self, job_id: str, status: str) -> None:
        """Set a job's status (active | paused | cancelled)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE scheduler_jobs SET status = ? WHERE job_id = ?",
                (status, job_id),
            )

    def update_scheduler_job_schedule(
        self,
        job_id: str,
        *,
        edict_id: str,
        schedule_json: str,
        schedule_type: str,
        cron_expr: str | None,
        interval_seconds: int | None,
        next_run: datetime,
        status: str,
    ) -> bool:
        """事务性替换敕令与持久游标时间定义，保留 job_id 与历史关联。"""
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT sj.edict_id, sj.status, e.status AS edict_status
                FROM scheduler_jobs sj
                JOIN edicts e ON e.id = sj.edict_id
                WHERE sj.job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["edict_id"] != edict_id
                or row["status"] not in {"active", "paused"}
                or row["edict_status"] != "open"
            ):
                return False
            self._conn.execute(
                "UPDATE edicts SET schedule_json = ? WHERE id = ?",
                (schedule_json, edict_id),
            )
            cursor = self._conn.execute(
                """
                UPDATE scheduler_jobs
                SET schedule_type = ?, cron_expr = ?, interval_seconds = ?,
                    next_run = ?, status = ?
                WHERE job_id = ? AND status IN ('active', 'paused')
                """,
                (
                    schedule_type,
                    cron_expr,
                    interval_seconds,
                    next_run.astimezone(UTC).isoformat(),
                    status,
                    job_id,
                ),
            )
        return cursor.rowcount == 1

    def compare_and_set_scheduler_next_run(
        self,
        job_id: str,
        *,
        expected_next_run: str,
        next_run: datetime,
    ) -> bool:
        """在补跑合并前安全推进游标，避免与暂停/取消/其他实例竞争。"""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE scheduler_jobs
                SET next_run = ?
                WHERE job_id = ? AND status = 'active' AND next_run = ?
                """,
                (next_run.astimezone(UTC).isoformat(), job_id, expected_next_run),
            )
        return cursor.rowcount == 1

    def get_scheduler_job(self, job_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scheduler_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_scheduler_job(self, job_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE scheduler_jobs SET status = 'cancelled' WHERE job_id = ?",
                (job_id,),
            )

    def complete_scheduler_job(self, job_id: str) -> None:
        """标记一次性任务已正常触发，区别于用户取消。"""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE scheduler_jobs SET status = 'completed', next_run = NULL WHERE job_id = ?",
                (job_id,),
            )

    def list_active_scheduler_jobs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduler_jobs WHERE status = 'active' ORDER BY created_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_scheduler_jobs(
        self,
        statuses: tuple[str, ...] = ("active", "paused"),
    ) -> list[dict]:
        """List scheduler jobs filtered by status (default: active + paused)."""
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM scheduler_jobs WHERE status IN ({placeholders}) "
                "ORDER BY created_at ASC",
                tuple(statuses),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Schedule run 台账（Multica 借鉴 #2）---

    def create_schedule_run(
        self,
        source: str,
        kind: str,
        status: str,
        edict_id: str | None = None,
    ) -> str:
        """记一次调度触发。source=系统 job 名 或 edict_id；kind=cron/interval/system。"""
        run_id = str(ULID())
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO schedule_run (id, source, kind, status, edict_id, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, source, kind, status, edict_id, datetime.now(UTC).isoformat()),
            )
        return run_id

    def finish_schedule_run(
        self,
        run_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE schedule_run SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                (status, error, datetime.now(UTC).isoformat(), run_id),
            )

    def has_running_system_job(self, source: str) -> bool:
        """系统 job 是否有上一次仍在 running 的触发（Multica 借鉴 #2-B 去重）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM schedule_run "
                "WHERE source = ? AND kind = 'system' AND status = 'running'",
                (source,),
            ).fetchone()
        return row[0] > 0

    def list_schedule_runs(
        self,
        source: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        with self._lock:
            if source:
                rows = self._conn.execute(
                    "SELECT * FROM schedule_run WHERE source = ? ORDER BY started_at DESC LIMIT ?",
                    (source, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM schedule_run ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def fail_running_system_schedule_runs(self, *, reason: str) -> int:
        """启动时将上个进程遗留的全部 system/running 台账收敛为 failed。"""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE schedule_run
                SET status = 'failed', error = ?, finished_at = ?
                WHERE kind = 'system' AND status = 'running'
                """,
                (reason, now),
            )
        return cursor.rowcount
