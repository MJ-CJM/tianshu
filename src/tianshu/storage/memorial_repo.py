"""Storage Memorial 领域 Mixin —— 奏折/批复 CRUD、心跳存活判定、位面反馈打分。"""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

from tianshu.models import Decree, Memorial, UsageSummary
from tianshu.storage.mappers import _memorial_to_params, _row_to_memorial


class MemorialMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def save_memorial(self, memorial: Memorial) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO memorials
                   (id, edict_id, instruction, status, summary, result, usage_json,
                    error, created_at, started_at, completed_at,
                    attempt, parent_memorial_id, review_status, audit_json,
                    artifacts_json, timeline_json, dag_node_id, persona_id,
                    runtime_override_json, acceptance_override_json,
                    reasoning_content, final_output, universe_id, last_heartbeat_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _memorial_to_params(memorial),
            )

    def update_memorial(self, memorial: Memorial) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE memorials SET
                   status=?, summary=?, result=?, usage_json=?, error=?,
                   started_at=?, completed_at=?,
                   attempt=?, review_status=?, audit_json=?,
                   artifacts_json=?, timeline_json=?,
                   dag_node_id=?, persona_id=?,
                   runtime_override_json=?, acceptance_override_json=?,
                   reasoning_content=?, final_output=?, universe_id=?,
                   last_heartbeat_at=?
                   WHERE id=?""",
                (
                    memorial.status.value,
                    memorial.summary,
                    memorial.result,
                    memorial.usage.model_dump_json(),
                    memorial.error,
                    memorial.started_at.isoformat() if memorial.started_at else None,
                    memorial.completed_at.isoformat() if memorial.completed_at else None,
                    memorial.attempt,
                    memorial.review_status,
                    memorial.audit.model_dump_json() if memorial.audit else None,
                    json.dumps([a.model_dump() for a in memorial.artifacts], default=str),
                    json.dumps([t.model_dump() for t in memorial.timeline], default=str),
                    memorial.dag_node_id,
                    memorial.persona_id,
                    json.dumps(memorial.runtime_override) if memorial.runtime_override else None,
                    memorial.acceptance_override.model_dump_json()
                    if memorial.acceptance_override
                    else None,
                    memorial.reasoning_content,
                    memorial.final_output,
                    memorial.universe_id,
                    memorial.last_heartbeat_at.isoformat() if memorial.last_heartbeat_at else None,
                    memorial.id,
                ),
            )

    def update_memorial_usage(self, memorial_id: str, usage: UsageSummary) -> None:
        """只更新 memorial.usage_json 字段。

        与 update_memorial 的差别：避免回写整个 memorial（外环 critic 回写 cost 时只关心 usage）。
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE memorials SET usage_json = ? WHERE id = ?",
                (usage.model_dump_json(), memorial_id),
            )

    def get_memorial(self, memorial_id: str) -> Memorial | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memorials WHERE id = ?", (memorial_id,)
            ).fetchone()
        return _row_to_memorial(row) if row else None

    def get_memorial_by_edict(self, edict_id: str) -> Memorial | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memorials WHERE edict_id = ? ORDER BY created_at DESC LIMIT 1",
                (edict_id,),
            ).fetchone()
        return _row_to_memorial(row) if row else None

    def list_memorials_by_edict(self, edict_id: str) -> list[Memorial]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memorials WHERE edict_id = ? ORDER BY created_at ASC",
                (edict_id,),
            ).fetchall()
        return [_row_to_memorial(r) for r in rows]

    def list_memorials(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Memorial], int]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM memorials WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                ).fetchall()
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM memorials WHERE status = ?", (status,)
                ).fetchone()[0]
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memorials ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                total = self._conn.execute("SELECT COUNT(*) FROM memorials").fetchone()[0]
        return [_row_to_memorial(r) for r in rows], total

    def list_stale_memorials(
        self,
        idle_seconds: int,
        statuses: tuple[str, ...] = ("running", "planning", "auditing"),
        limit: int = 100,
    ) -> list[Memorial]:
        """活跃态但超过 idle_seconds 无心跳的 memorial（孤儿任务候选，Multica 借鉴 #1）。

        无心跳判定用 COALESCE(last_heartbeat_at, started_at, created_at)，
        兼容尚未打过心跳（刚 RUNNING）与存量无该列的行。
        """
        cutoff = (datetime.now(UTC) - timedelta(seconds=idle_seconds)).isoformat()
        placeholders = ", ".join("?" * len(statuses))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM memorials "
                f"WHERE status IN ({placeholders}) "
                f"AND COALESCE(last_heartbeat_at, started_at, created_at) < ? "
                f"ORDER BY COALESCE(last_heartbeat_at, started_at, created_at) ASC "
                f"LIMIT ?",
                (*statuses, cutoff, limit),
            ).fetchall()
        return [_row_to_memorial(r) for r in rows]

    # --- Decree ---

    def save_decree(self, decree: Decree) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO decrees
                   (id, memorial_id, action, comment, amended_goal, actor, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    decree.id,
                    decree.memorial_id,
                    decree.action,
                    decree.comment,
                    decree.amended_goal,
                    decree.actor,
                    decree.created_at.isoformat(),
                ),
            )

    def list_decrees_by_memorial(self, memorial_id: str) -> list[Decree]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decrees WHERE memorial_id = ? ORDER BY created_at ASC",
                (memorial_id,),
            ).fetchall()
        return [
            Decree(
                id=r["id"],
                memorial_id=r["memorial_id"],
                action=r["action"],
                comment=r["comment"],
                amended_goal=r["amended_goal"],
                actor=r["actor"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    # --- Edict active memorial check ---

    def has_active_memorials(self, edict_id: str) -> bool:
        """Check if edict has any SUBMITTED or RUNNING memorials."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM memorials WHERE edict_id = ? AND status IN ('submitted', 'running')",
                (edict_id,),
            ).fetchone()
        return row[0] > 0

    def has_unfinished_memorials(self, edict_id: str) -> bool:
        """edict 是否有未结束（非终态）的 memorial —— 周期任务并发去重用（Multica 借鉴 #2-A）。

        终态 = completed / failed / cancelled；其余（submitted/running/planning/
        auditing/needs_review/scheduled）都视为进行中，比 has_active_memorials 更全，
        避免 planning/auditing 期间被重复触发而叠罗汉。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM memorials WHERE edict_id = ? "
                "AND status NOT IN ('completed', 'failed', 'cancelled')",
                (edict_id,),
            ).fetchone()
        return row[0] > 0

    def set_memorial_feedback(self, memorial_id: str, score: int) -> None:
        """设置某 memorial 的显式反馈分（-1/0/1）。"""
        score = max(-1, min(1, int(score)))
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE memorials SET feedback_score = ? WHERE id = ?",
                (score, memorial_id),
            )
