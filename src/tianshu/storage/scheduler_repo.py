"""Storage Scheduler 领域 Mixin —— 调度任务（scheduler_jobs）与调度台账（schedule_run）。"""

import sqlite3
import threading
from datetime import UTC, datetime

from ulid import ULID


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
