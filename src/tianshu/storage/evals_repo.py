"""Storage Evals 领域 Mixin —— 平台级回归评测的评测集与运行台账(迭代 2「证明」)。

与 universe 的 variant_eval_runs(位面变体门禁评估)分表:那边评的是
「变体 vs 冠军」,这边评的是「平台当前形态 vs 自身历史」——同一 EvalHarness
两个消费方,台账语义不同不混表。
"""

import json
import sqlite3
import threading


def _row_to_eval_set(row: sqlite3.Row) -> dict:
    return {
        "name": row["name"],
        "goals": json.loads(row["goals_json"]),
        "source": row["source"],
        "created_at": row["created_at"],
    }


def _row_to_platform_eval_run(row: sqlite3.Row, *, brief: bool = False) -> dict:
    run = {
        "id": row["id"],
        "eval_set_name": row["eval_set_name"],
        "eval_set_fingerprint": row["eval_set_fingerprint"],
        "target": row["target"],
        "fitness": json.loads(row["fitness_json"]),
        "n": row["n"],
        "truncated": bool(row["truncated"]),
        "delta_vs_prev": row["delta_vs_prev"],
        "created_at": row["created_at"],
    }
    if not brief:
        run["stats"] = json.loads(row["stats_json"])
        run["goal_results"] = (
            json.loads(row["goal_results_json"]) if row["goal_results_json"] else []
        )
    return run


class EvalsMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- 评测集 ---

    def save_eval_set(self, name: str, goals: list[str], *, source: str = "sampled") -> None:
        from datetime import UTC, datetime

        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO eval_sets (name, goals_json, source, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    name,
                    json.dumps(goals, ensure_ascii=False),
                    source,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get_eval_set(self, name: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM eval_sets WHERE name = ?", (name,)).fetchone()
        return _row_to_eval_set(row) if row else None

    def list_eval_sets(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM eval_sets ORDER BY created_at DESC").fetchall()
        return [_row_to_eval_set(r) for r in rows]

    # --- 评测运行台账 ---

    def save_platform_eval_run(self, run: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO eval_runs
                   (id, eval_set_name, eval_set_fingerprint, target, fitness_json,
                    stats_json, goal_results_json, n, truncated, delta_vs_prev, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run["id"],
                    run.get("eval_set_name"),
                    run["eval_set_fingerprint"],
                    run["target"],
                    json.dumps(run.get("fitness", {}), ensure_ascii=False),
                    json.dumps(run.get("stats", {}), ensure_ascii=False),
                    json.dumps(run.get("goal_results", []), ensure_ascii=False),
                    int(run.get("n", 0)),
                    1 if run.get("truncated") else 0,
                    run.get("delta_vs_prev"),
                    run["created_at"],
                ),
            )

    def get_platform_eval_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_platform_eval_run(row) if row else None

    def list_platform_eval_runs(self, limit: int = 50) -> list[dict]:
        """列表视图(brief:不含 stats/goal_results 大字段)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_platform_eval_run(r, brief=True) for r in rows]

    def latest_platform_eval_run(self, fingerprint: str) -> dict | None:
        """同评估集指纹下最近一次运行(算 delta_vs_prev 用)。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM eval_runs WHERE eval_set_fingerprint = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
        return _row_to_platform_eval_run(row) if row else None
