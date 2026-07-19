"""Storage Memorial 领域 Mixin —— 奏折/批复 CRUD、心跳存活判定、位面反馈打分。"""

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

from tianshu.models import Decree, Memorial, UsageSummary, resolve_failure_reason
from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.storage.mappers import _memorial_to_params, _row_to_memorial


def _insert_effective_governance_contract(
    conn: sqlite3.Connection,
    memorial_id: str,
    edict_id: str,
    contract: EffectiveGovernanceContractV1,
) -> None:
    conn.execute(
        """
        INSERT INTO effective_governance_contracts
            (memorial_id, edict_id, schema_version, requested_contract_hash,
             contract_json, contract_hash, executor_manifest_id,
             executor_manifest_version, executor_manifest_hash, runtime_probe_id,
             created_at)
        VALUES (?, ?, '1', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memorial_id,
            edict_id,
            contract.requested_contract_hash,
            contract.canonical_json(),
            contract.content_hash,
            contract.executor_manifest_id,
            contract.executor_manifest_version,
            contract.executor_manifest_hash,
            contract.runtime_probe_id,
            datetime.now(UTC).isoformat(),
        ),
    )


def _insert_memorial(conn: sqlite3.Connection, memorial: Memorial) -> None:
    conn.execute(
        """INSERT INTO memorials
           (id, edict_id, instruction, status, summary, result, usage_json,
            error, created_at, started_at, completed_at,
            attempt, parent_memorial_id, review_status, audit_json,
            artifacts_json, timeline_json, dag_node_id, persona_id,
            runtime_override_json, acceptance_override_json,
            reasoning_content, final_output, universe_id, last_heartbeat_at,
            failure_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        _memorial_to_params(memorial),
    )
    if memorial.effective_governance_contract is not None:
        _insert_effective_governance_contract(
            conn,
            memorial.id,
            memorial.edict_id,
            memorial.effective_governance_contract,
        )


def insert_memorial(conn: sqlite3.Connection, memorial: Memorial) -> None:
    """Insert one Memorial on a caller-owned transaction."""
    _insert_memorial(conn, memorial)


def list_memorials_for_edict_current(
    conn: sqlite3.Connection,
    edict_id: str,
) -> list[Memorial]:
    """Load all Memorials and effective contracts without per-row queries."""

    rows = conn.execute(
        """
        SELECT memorial.*, effective.contract_json AS effective_contract_json,
               effective.contract_hash AS effective_contract_hash
        FROM memorials AS memorial
        LEFT JOIN effective_governance_contracts AS effective
          ON effective.memorial_id = memorial.id
        WHERE memorial.edict_id = ?
        ORDER BY memorial.created_at, memorial.id
        """,
        (edict_id,),
    ).fetchall()
    memorials: list[Memorial] = []
    for row in rows:
        contract = None
        if row["effective_contract_json"] is not None:
            contract = EffectiveGovernanceContractV1.model_validate_json(
                row["effective_contract_json"]
            )
            if contract.content_hash != row["effective_contract_hash"]:
                raise ValueError(f"effective governance contract hash mismatch for {row['id']}")
        memorials.append(_row_to_memorial(row, effective_governance_contract=contract))
    return memorials


class MemorialMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def _save_effective_governance_contract_unlocked(
        self,
        memorial_id: str,
        edict_id: str,
        contract: EffectiveGovernanceContractV1,
    ) -> None:
        _insert_effective_governance_contract(self._conn, memorial_id, edict_id, contract)

    def save_effective_governance_contract(
        self,
        memorial_id: str,
        edict_id: str,
        contract: EffectiveGovernanceContractV1,
    ) -> None:
        with self._lock, self._conn:
            self._save_effective_governance_contract_unlocked(
                memorial_id,
                edict_id,
                contract,
            )

    def get_effective_governance_contract(
        self,
        memorial_id: str,
    ) -> EffectiveGovernanceContractV1 | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT contract_json, contract_hash
                FROM effective_governance_contracts WHERE memorial_id = ?
                """,
                (memorial_id,),
            ).fetchone()
        if row is None:
            return None
        contract = EffectiveGovernanceContractV1.model_validate_json(row["contract_json"])
        if contract.content_hash != row["contract_hash"]:
            raise ValueError(f"effective governance contract hash mismatch for {memorial_id}")
        return contract

    def _row_with_effective_contract(self, row: sqlite3.Row) -> Memorial:
        return _row_to_memorial(
            row,
            effective_governance_contract=self.get_effective_governance_contract(row["id"]),
        )

    def save_memorial(self, memorial: Memorial) -> None:
        with self._lock, self._conn:
            _insert_memorial(self._conn, memorial)

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
                   last_heartbeat_at=?, failure_reason=?
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
                    resolve_failure_reason(
                        memorial.status.value, memorial.error, memorial.failure_reason
                    ),
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
        return self._row_with_effective_contract(row) if row else None

    def get_memorial_by_edict(self, edict_id: str) -> Memorial | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memorials WHERE edict_id = ? ORDER BY created_at DESC LIMIT 1",
                (edict_id,),
            ).fetchone()
        return self._row_with_effective_contract(row) if row else None

    def list_memorials_by_edict(self, edict_id: str) -> list[Memorial]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memorials WHERE edict_id = ? ORDER BY created_at ASC",
                (edict_id,),
            ).fetchall()
        return [self._row_with_effective_contract(r) for r in rows]

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
        return [self._row_with_effective_contract(r) for r in rows], total

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
        return [self._row_with_effective_contract(r) for r in rows]

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

    def save_decree_if_absent(self, decree: Decree) -> bool:
        """Persist an idempotent compatibility projection by deterministic Decree ID."""

        with self._lock, self._conn:
            cursor = self._conn.execute(
                """INSERT INTO decrees
                   (id, memorial_id, action, comment, amended_goal, actor, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO NOTHING""",
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
        return cursor.rowcount == 1

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

    def list_recent_decrees(self, limit: int = 50) -> list[Decree]:
        """最近批红行为(全局)——起居注读它蒸馏用户批红习惯(迭代 4)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM decrees ORDER BY created_at DESC LIMIT ?", (limit,)
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

    # --- 失败归因（迭代 2「证明」）---

    def failure_reason_distribution(self, days: int | None = None) -> list[dict]:
        """failed memorial 的归因分布(reason/count/最近样例),喂审计面板与太医诊断。"""
        sql = (
            "SELECT failure_reason AS reason, COUNT(*) AS count, MAX(created_at) AS last_seen "
            "FROM memorials WHERE status = 'failed'"
        )
        params: tuple = ()
        if days is not None:
            cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            sql += " AND created_at >= ?"
            params = (cutoff,)
        sql += " GROUP BY failure_reason ORDER BY count DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "reason": r["reason"] or "unclassified",
                "count": r["count"],
                "last_seen": r["last_seen"],
            }
            for r in rows
        ]

    def backfill_failure_reasons(self, *, reclassify: bool = False) -> int:
        """按分类学回填历史 failed 行;reclassify=True 时全量重分类(分类器升级后用)。

        与写路径共用 resolve_failure_reason,保证在库口径统一。返回更新行数。
        """
        where = "status = 'failed'" + ("" if reclassify else " AND failure_reason IS NULL")
        with self._lock, self._conn:
            rows = self._conn.execute(f"SELECT id, error FROM memorials WHERE {where}").fetchall()
            updated = 0
            for r in rows:
                reason = resolve_failure_reason("failed", r["error"], None)
                self._conn.execute(
                    "UPDATE memorials SET failure_reason = ? WHERE id = ?", (reason, r["id"])
                )
                updated += 1
        return updated

    # --- 后台史官(迭代 4「记忆 2.0」)---

    def list_undistilled_memorials(self, limit: int = 10) -> list[Memorial]:
        """成功终态且史官尚未蒸馏的 memorial(最近优先)——史官蒸馏执行知识用。"""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM memorials
                   WHERE status IN ('completed', 'approved')
                   AND id NOT IN (SELECT memorial_id FROM historian_log)
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [_row_to_memorial(r) for r in rows]

    def count_successful_memorials(self) -> int:
        """审计通过(成功终态)的 memorial 总数——自进化请旨解锁的阈值口径(ADR-0004)。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM memorials WHERE status IN ('completed', 'approved')"
            ).fetchone()
        return int(row[0]) if row else 0

    def report_window_stats(self, since_iso: str) -> dict:
        """窗口(实录馆周报,迭代 7)统计:自 since 起的敕令/执行/代批计数,供《实录》汇编。"""
        with self._lock:
            edicts = self._conn.execute(
                "SELECT COUNT(*) FROM edicts WHERE created_at >= ?", (since_iso,)
            ).fetchone()[0]
            rows = self._conn.execute(
                """SELECT status, COUNT(*) FROM memorials
                   WHERE created_at >= ? GROUP BY status""",
                (since_iso,),
            ).fetchall()
            auto = self._conn.execute(
                "SELECT COUNT(*) FROM decrees WHERE actor = 'silijian' AND created_at >= ?",
                (since_iso,),
            ).fetchone()[0]
        by_status = {r[0]: r[1] for r in rows}
        return {
            "edicts": int(edicts),
            "memorials_total": int(sum(by_status.values())),
            "completed": int(by_status.get("completed", 0) + by_status.get("approved", 0)),
            "failed": int(by_status.get("failed", 0)),
            "auto_approvals": int(auto),
        }

    def mark_distilled(self, memorial_id: str, insight_written: bool, now_iso: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO historian_log
                   (memorial_id, distilled_at, insight_written) VALUES (?, ?, ?)""",
                (memorial_id, now_iso, 1 if insight_written else 0),
            )
