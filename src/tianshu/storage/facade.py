"""SQLite storage layer - system truth source.

Storage 由 _StorageBase（连接生命周期：建库/建表/迁移/关闭）与 16 个领域 Mixin 组合而成：
edict/memorial/event/memory/cost/dag/scheduler（批 B）+
config/persona/universe/credential/orchestrator/channel/feishu/telegram（批 C）+
evals（迭代 2）。
本文件仅保留跨域方法（涉及多张表 JOIN，无法唯一归入某个领域表）与 Storage 组合声明本身。
"""

from tianshu.storage._base import _StorageBase
from tianshu.storage.channel_repo import ChannelMixin
from tianshu.storage.config_repo import ConfigMixin
from tianshu.storage.cost_repo import CostMixin
from tianshu.storage.credential_repo import CredentialMixin
from tianshu.storage.dag_repo import DagMixin
from tianshu.storage.edict_repo import EdictMixin
from tianshu.storage.evals_repo import EvalsMixin
from tianshu.storage.event_repo import EventMixin
from tianshu.storage.feishu_repo import FeishuMixin
from tianshu.storage.flag_repo import FlagMixin
from tianshu.storage.kg_repo import KgMixin
from tianshu.storage.memorial_repo import MemorialMixin
from tianshu.storage.memory_repo import MemoryMixin
from tianshu.storage.notify_repo import NotifyMixin
from tianshu.storage.orchestrator_repo import OrchestratorMixin
from tianshu.storage.persona_repo import PersonaMixin
from tianshu.storage.petition_repo import PetitionMixin
from tianshu.storage.scheduler_repo import SchedulerMixin
from tianshu.storage.security_repo import SecurityMixin
from tianshu.storage.telegram_repo import TelegramMixin
from tianshu.storage.universe_repo import UniverseMixin


class Storage(
    _StorageBase,
    EdictMixin,
    MemorialMixin,
    EventMixin,
    MemoryMixin,
    CostMixin,
    DagMixin,
    SchedulerMixin,
    ConfigMixin,
    PersonaMixin,
    UniverseMixin,
    EvalsMixin,
    SecurityMixin,
    KgMixin,
    NotifyMixin,
    FlagMixin,
    PetitionMixin,
    CredentialMixin,
    OrchestratorMixin,
    ChannelMixin,
    FeishuMixin,
    TelegramMixin,
):
    # 以下 3 个方法命中多个领域 Mixin 的表（真跨表 JOIN 或语义横跨 persona/memorial/cost），
    # 无法唯一归入某个领域 Mixin，保留在组合根。

    # --- Persona Stats (Phase 3.12) ---

    def get_persona_stats(self, persona_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) as total_executions,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                    COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled,
                    COALESCE(SUM(json_extract(usage_json, '$.total_tokens')), 0) as total_tokens,
                    COALESCE(AVG(
                        CASE WHEN completed_at IS NOT NULL AND started_at IS NOT NULL
                        THEN (julianday(completed_at) - julianday(started_at)) * 86400
                        END
                    ), 0.0) as avg_duration_seconds
                FROM memorials
                WHERE persona_id = ?
            """,
                (persona_id,),
            ).fetchone()

        total = row["total_executions"] or 0
        completed = row["completed"] or 0
        success_rate = (completed / total * 100) if total > 0 else 0.0
        total_tokens = row["total_tokens"] or 0
        avg_tokens = (total_tokens / total) if total > 0 else 0.0

        # Cost from cost_ledger (join via memorial_id)
        cost_row = self._conn.execute(
            """
            SELECT COALESCE(SUM(cl.cost_cny), 0.0) as total_cost
            FROM cost_ledger cl
            JOIN memorials m ON cl.memorial_id = m.id
            WHERE m.persona_id = ?
        """,
            (persona_id,),
        ).fetchone()

        return {
            "total_executions": total,
            "completed": completed,
            "failed": row["failed"] or 0,
            "cancelled": row["cancelled"] or 0,
            "success_rate": round(success_rate, 2),
            "total_tokens": total_tokens,
            "avg_tokens_per_execution": round(avg_tokens, 1),
            "total_cost_cny": round(cost_row["total_cost"], 6) if cost_row else 0.0,
            "avg_duration_seconds": round(row["avg_duration_seconds"] or 0.0, 2),
        }

    # --- Memorials by Persona ---

    def list_memorials_by_persona(
        self,
        persona_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Return memorials grouped by edict for a persona.

        For 'bingbu' (default executor), also includes memorials with NULL persona_id
        to cover legacy data created before persona_id tracking was added.
        """
        if persona_id == "bingbu":
            join_where = "(m.persona_id = ? OR m.persona_id IS NULL)"
            count_where = "(persona_id = ? OR persona_id IS NULL)"
        else:
            join_where = "m.persona_id = ?"
            count_where = "persona_id = ?"
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT m.*, e.title as edict_title, e.goal as edict_goal, e.status as edict_status
                   FROM memorials m
                   JOIN edicts e ON m.edict_id = e.id
                   WHERE {join_where}
                   ORDER BY m.created_at DESC
                   LIMIT ? OFFSET ?""",
                (persona_id, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM memorials WHERE {count_where}",
                (persona_id,),
            ).fetchone()[0]

        # Group by edict_id
        edicts_map: dict[str, dict] = {}
        for r in rows:
            eid = r["edict_id"]
            if eid not in edicts_map:
                edicts_map[eid] = {
                    "edict_id": eid,
                    "edict_title": r["edict_title"],
                    "edict_goal": r["edict_goal"],
                    "edict_status": r["edict_status"],
                    "memorials": [],
                }
            edicts_map[eid]["memorials"].append(
                {
                    "id": r["id"],
                    "instruction": r["instruction"],
                    "status": r["status"],
                    "result": r["result"],
                    "summary": r["summary"],
                    "error": r["error"],
                    "created_at": r["created_at"],
                    "started_at": r["started_at"],
                    "completed_at": r["completed_at"],
                }
            )

        return list(edicts_map.values()), total
