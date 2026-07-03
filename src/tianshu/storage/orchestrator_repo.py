"""Storage Orchestrator 领域 Mixin —— 外环迭代/检查点归档、监督报告。"""

import sqlite3
import threading


class OrchestratorMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- outer loop iterations ------------------------------------------

    def save_outer_loop_iteration(self, record: dict) -> None:
        """写入一条 outer loop iteration（dict 形式以避免循环 import）。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO outer_loop_iterations
                (id, edict_id, iteration, level, actor_output, checks_result,
                 critic_result, cost_cny, started_at, finished_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(edict_id, iteration) DO NOTHING
            """,
                (
                    record["id"],
                    record["edict_id"],
                    record["iteration"],
                    record["level"],
                    record["actor_output"],
                    record["checks_result"],
                    record["critic_result"],
                    record["cost_cny"],
                    record["started_at"],
                    record["finished_at"],
                ),
            )

    def get_outer_loop_iterations(self, edict_id: str) -> list[dict]:
        """按 iteration 升序返回所有迭代记录。"""
        rows = self._conn.execute(
            """
            SELECT id, edict_id, iteration, level, actor_output, checks_result,
                   critic_result, cost_cny, started_at, finished_at, archived_at
            FROM outer_loop_iterations
            WHERE edict_id = ?
            ORDER BY iteration ASC
        """,
            (edict_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_iterations_to_archive(self, before: str) -> list[str]:
        """返回 finished_at < before 且未归档的 iteration id 列表。"""
        rows = self._conn.execute(
            """
            SELECT id FROM outer_loop_iterations
            WHERE finished_at < ? AND archived_at IS NULL
        """,
            (before,),
        ).fetchall()
        return [r["id"] for r in rows]

    def archive_iteration(self, iteration_id: str, archived_at: str) -> None:
        """归档：actor_output 置 NULL，archived_at 写时间戳。"""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE outer_loop_iterations
                SET actor_output = NULL, archived_at = ?
                WHERE id = ?
            """,
                (archived_at, iteration_id),
            )

    # --- outer loop checkpoints ------------------------------------------

    def save_outer_loop_checkpoint(self, edict_id: str, data_json: str, saved_at: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO outer_loop_checkpoints (edict_id, data_json, saved_at)
                VALUES (?, ?, ?)
                ON CONFLICT(edict_id) DO UPDATE SET data_json=excluded.data_json, saved_at=excluded.saved_at
            """,
                (edict_id, data_json, saved_at),
            )

    def get_outer_loop_checkpoint(self, edict_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT data_json FROM outer_loop_checkpoints WHERE edict_id = ?",
            (edict_id,),
        ).fetchone()
        return row["data_json"] if row else None

    def clear_outer_loop_checkpoint(self, edict_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM outer_loop_checkpoints WHERE edict_id = ?",
                (edict_id,),
            )

    # --- Supervision report (long task 终态总评) ---

    def save_supervision_report(self, record: dict) -> None:
        """写一行监督报告（PK = (memorial_id, persona_id)）。

        record 必带 memorial_id；旧调用方未传时会落 KeyError，强制升级到新 schema。
        """
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO supervision_reports
                   (edict_id, memorial_id, persona_id, persona_name, final_status,
                    iterations_count, total_cost_cny, report_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["edict_id"],
                    record["memorial_id"],
                    record["persona_id"],
                    record["persona_name"],
                    record["final_status"],
                    record["iterations_count"],
                    record["total_cost_cny"],
                    record["report_json"],
                    record["created_at"],
                ),
            )

    def get_supervision_report(self, edict_id: str) -> dict | None:
        """单监督官兼容入口；返同 edict 最新一行。"""
        row = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE edict_id = ? ORDER BY created_at DESC LIMIT 1",
            (edict_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_supervision_reports(self, edict_id: str) -> list[dict]:
        """同 edict 全部报告，按 created_at DESC + persona_id 排序。"""
        rows = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE edict_id = ? "
            "ORDER BY created_at DESC, persona_id ASC",
            (edict_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_supervision_reports_by_memorial(self, memorial_id: str) -> list[dict]:
        """按 memorial 维度返回报告（每条奏折独立的监督报告）。"""
        rows = self._conn.execute(
            "SELECT * FROM supervision_reports WHERE memorial_id = ? ORDER BY persona_id ASC",
            (memorial_id,),
        ).fetchall()
        return [dict(r) for r in rows]
