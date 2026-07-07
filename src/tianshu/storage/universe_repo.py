"""Storage Universe 领域 Mixin —— 平行位面 CRUD、变体评估记录、位面维度奏折统计。"""

import json
import sqlite3
import threading

from tianshu.storage.mappers import _row_to_eval_run, _row_to_universe


def _audit_passed(a: dict) -> bool:
    """AuditResult.verdict == 'pass' を「合格」とみなす（conservative: 不明は不合格）。"""
    return a.get("verdict") == "pass"


class UniverseMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Universes (平行位面) ---

    def save_universe(self, uni: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO universes
                   (id, name, parent_universe_id, status, origin,
                    mutation_reason, description, fitness_json, code_ref, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uni["id"],
                    uni["name"],
                    uni.get("parent_universe_id"),
                    uni["status"],
                    uni["origin"],
                    uni.get("mutation_reason"),
                    uni.get("description", ""),
                    json.dumps(uni.get("fitness", {}), ensure_ascii=False),
                    uni.get("code_ref"),
                    uni["created_at"],
                ),
            )

    def get_universe(self, universe_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM universes WHERE id = ?", (universe_id,)
            ).fetchone()
        return _row_to_universe(row) if row else None

    def list_universes(self, *, include_archived: bool = True) -> list[dict]:
        with self._lock:
            sql = "SELECT * FROM universes"
            if not include_archived:
                sql += " WHERE status != 'archived'"
            sql += " ORDER BY created_at DESC"
            return [_row_to_universe(r) for r in self._conn.execute(sql).fetchall()]

    def get_champion_universe(self) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM universes WHERE status = 'champion'").fetchone()
        return _row_to_universe(row) if row else None

    def set_universe_status(self, universe_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE universes SET status = ? WHERE id = ?", (status, universe_id)
            )

    def update_universe_fitness(self, universe_id: str, fitness: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE universes SET fitness_json = ? WHERE id = ?",
                (json.dumps(fitness, ensure_ascii=False), universe_id),
            )

    def delete_universe(self, universe_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM variant_eval_runs WHERE universe_id = ?", (universe_id,)
            )
            self._conn.execute("DELETE FROM universes WHERE id = ?", (universe_id,))

    def save_variant_eval_run(self, run: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO variant_eval_runs
                   (id, universe_id, gate_passed, gate_detail,
                    fitness_json, eval_set_version, cost, created_at, baseline_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run["id"],
                    run["universe_id"],
                    1 if run.get("gate_passed") else 0,
                    json.dumps(run.get("gate_detail"), ensure_ascii=False)
                    if run.get("gate_detail") is not None
                    else None,
                    json.dumps(run.get("fitness", {}), ensure_ascii=False),
                    run.get("eval_set_version"),
                    float(run.get("cost", 0.0)),
                    run["created_at"],
                    json.dumps(run["baseline"], ensure_ascii=False)
                    if run.get("baseline") is not None
                    else None,
                ),
            )

    def latest_baseline_fitness(self, eval_set_version: str) -> dict | None:
        """同评估集指纹下最近一次冠军基线分(供 evaluate_paired 缓存复用)。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT baseline_json FROM variant_eval_runs "
                "WHERE eval_set_version = ? AND baseline_json IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (eval_set_version,),
            ).fetchone()
        if not row or not row["baseline_json"]:
            return None
        try:
            return json.loads(row["baseline_json"])
        except (ValueError, TypeError):
            return None

    def list_variant_eval_runs(self, universe_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM variant_eval_runs WHERE universe_id = ? ORDER BY created_at DESC",
                (universe_id,),
            ).fetchall()
        return [_row_to_eval_run(r) for r in rows]

    def universe_memorial_stats(self, universe_id: str) -> dict:
        """聚合某位面下 memorial 的成功/失败/重试/成本/审计/反馈。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, attempt, usage_json, audit_json, feedback_score "
                "FROM memorials WHERE universe_id = ?",
                (universe_id,),
            ).fetchall()
        total = len(rows)
        success = sum(1 for r in rows if r["status"] in ("completed", "approved"))
        retries = sum(max(0, (r["attempt"] or 1) - 1) for r in rows)
        feedback = sum((r["feedback_score"] or 0) for r in rows)
        audited = 0
        audit_pass = 0
        cost = 0.0
        for r in rows:
            try:
                u = json.loads(r["usage_json"] or "{}")
                cost += float(u.get("cost_cny", 0.0) or 0.0)
            except (ValueError, TypeError):
                pass
            aj = r["audit_json"]
            if aj:
                audited += 1
                try:
                    a = json.loads(aj)
                    if _audit_passed(a):
                        audit_pass += 1
                except (ValueError, TypeError):
                    pass
        return {
            "total": total,
            "success": success,
            "retries": retries,
            "audited": audited,
            "audit_pass": audit_pass,
            "cost": round(cost, 6),
            "feedback": feedback,
        }
