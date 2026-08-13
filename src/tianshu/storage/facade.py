"""SQLite storage layer - system truth source.

Storage 由 _StorageBase（连接生命周期：建库/建表/迁移/关闭）与 16 个领域 Mixin 组合而成：
edict/memorial/event/memory/cost/dag/scheduler（批 B）+
config/persona/universe/credential/orchestrator/channel/feishu/telegram（批 C）+
evals（迭代 2）。
本文件仅保留跨域方法（涉及多张表 JOIN，无法唯一归入某个领域表）与 Storage 组合声明本身。
"""

import json
from datetime import UTC, datetime

from ulid import ULID

from tianshu.storage._base import _StorageBase
from tianshu.storage.artifact_repo import ArtifactRepository, EvidenceRepository
from tianshu.storage.attempt_ledger import AttemptLeaseRepository
from tianshu.storage.auth_repo import AuthMixin
from tianshu.storage.channel_repo import ChannelMixin
from tianshu.storage.config_repo import ConfigMixin
from tianshu.storage.consultation_repo import ConsultationMixin
from tianshu.storage.correlation import correlation_for_memorial
from tianshu.storage.cost_repo import CostMixin
from tianshu.storage.credential_repo import CredentialMixin
from tianshu.storage.dag_repo import DagMixin
from tianshu.storage.decision_repo import DecisionRepository
from tianshu.storage.edict_repo import EdictMixin
from tianshu.storage.evals_repo import EvalsMixin
from tianshu.storage.event_repo import EventMixin
from tianshu.storage.feishu_repo import FeishuMixin
from tianshu.storage.flag_repo import FlagMixin
from tianshu.storage.kg_repo import KgMixin
from tianshu.storage.memorial_repo import MemorialMixin
from tianshu.storage.memory_repo import MemoryMixin
from tianshu.storage.model_provider_repo import ModelProviderMixin
from tianshu.storage.notify_repo import NotifyMixin
from tianshu.storage.orchestrator_repo import OrchestratorMixin
from tianshu.storage.persona_repo import PersonaMixin
from tianshu.storage.petition_repo import PetitionMixin
from tianshu.storage.run_state_repo import RunStateRepository
from tianshu.storage.scheduler_repo import SchedulerMixin
from tianshu.storage.security_repo import SecurityMixin
from tianshu.storage.side_effect_journal import SideEffectJournal
from tianshu.storage.system_audit_repo import SystemAuditMixin
from tianshu.storage.telegram_repo import TelegramMixin
from tianshu.storage.unit_of_work import SqliteUnitOfWork
from tianshu.storage.universe_repo import UniverseMixin
from tianshu.storage.workspace_repo import WorkspaceMixin


class EdictArchiveConflict(RuntimeError):
    """An Edict acquired unfinished work while an archive was requested."""


class Storage(
    _StorageBase,
    AuthMixin,
    EdictMixin,
    MemorialMixin,
    EventMixin,
    MemoryMixin,
    CostMixin,
    DagMixin,
    SchedulerMixin,
    ConfigMixin,
    ConsultationMixin,
    ModelProviderMixin,
    PersonaMixin,
    UniverseMixin,
    EvalsMixin,
    SecurityMixin,
    SystemAuditMixin,
    KgMixin,
    NotifyMixin,
    FlagMixin,
    PetitionMixin,
    CredentialMixin,
    OrchestratorMixin,
    ChannelMixin,
    FeishuMixin,
    TelegramMixin,
    WorkspaceMixin,
):
    def __init__(self, db_path: str) -> None:
        super().__init__(db_path)
        self.attempt_repo = AttemptLeaseRepository(self.unit_of_work)
        self.artifact_repo = ArtifactRepository(self.unit_of_work)
        self.decision_repo = DecisionRepository()
        self.evidence_repo = EvidenceRepository(self.unit_of_work)
        self.run_state_repo = RunStateRepository()
        self.side_effect_journal = SideEffectJournal(self.unit_of_work, self.attempt_repo)

    def unit_of_work(self) -> SqliteUnitOfWork:
        return SqliteUnitOfWork(self._conn, self._lock)

    def get_core_correlation_id(self, memorial_id: str) -> str:
        """Query the durable root correlation used by S3 governance records."""
        with self._lock:
            return correlation_for_memorial(self._conn, memorial_id)

    # 以下方法命中多个领域 Mixin 的表（真跨表 JOIN 或语义横跨 persona/memorial/cost），
    # 无法唯一归入某个领域 Mixin，保留在组合根。

    def tombstone_edict(self, edict_id: str, *, reason: str = "user_request") -> list[str]:
        """Atomically archive an Edict, cancel its schedules, and append one audit event.

        Returns the scheduler job IDs that were durably cancelled so the caller can
        also stop their in-memory timers. Repeated calls are idempotent.
        """

        archived_at = datetime.now(UTC).isoformat()
        with self.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            row = connection.execute(
                "SELECT status, runtime_json, metadata_json FROM edicts WHERE id = ?",
                (edict_id,),
            ).fetchone()
            if row is None:
                raise KeyError(edict_id)
            unfinished = connection.execute(
                """
                SELECT 1 FROM memorials
                WHERE edict_id = ?
                  AND status NOT IN ('completed', 'failed', 'cancelled')
                LIMIT 1
                """,
                (edict_id,),
            ).fetchone()
            if unfinished is not None:
                raise EdictArchiveConflict(edict_id)

            runtime = json.loads(row["runtime_json"] or "{}")
            runtime["lifecycle_phase"] = "complete"
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata.setdefault("archived_at", archived_at)
            previous_status = str(row["status"])
            status = "cancelled" if previous_status == "open" else previous_status

            job_rows = connection.execute(
                """
                SELECT job_id FROM scheduler_jobs
                WHERE edict_id = ? AND status IN ('active', 'paused')
                """,
                (edict_id,),
            ).fetchall()
            cancelled_job_ids = [str(job["job_id"]) for job in job_rows]
            connection.execute(
                """
                UPDATE scheduler_jobs
                SET status = 'cancelled', next_run = NULL
                WHERE edict_id = ? AND status IN ('active', 'paused')
                """,
                (edict_id,),
            )
            connection.execute(
                """
                UPDATE edicts
                SET status = ?, runtime_json = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(runtime),
                    json.dumps(metadata, ensure_ascii=False, default=str),
                    edict_id,
                ),
            )
            event_exists = connection.execute(
                """
                SELECT 1 FROM events
                WHERE edict_id = ? AND event_type = 'edict.archived'
                LIMIT 1
                """,
                (edict_id,),
            ).fetchone()
            if event_exists is None:
                connection.execute(
                    """
                    INSERT INTO events
                        (id, edict_id, memorial_id, event_type, payload_json, created_at)
                    VALUES (?, ?, NULL, 'edict.archived', ?, ?)
                    """,
                    (
                        str(ULID()),
                        edict_id,
                        json.dumps(
                            {"reason": reason, "previous_status": previous_status},
                            ensure_ascii=False,
                        ),
                        archived_at,
                    ),
                )
            unit_of_work.commit()
        return cancelled_job_ids

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
        submitter: str | None = None,
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
        params: list[str | int] = [persona_id]
        if submitter is not None:
            join_where += " AND e.submitter = ?"
            count_where += " AND e.submitter = ?"
            params.append(submitter)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT m.*, e.title as edict_title, e.goal as edict_goal, e.status as edict_status
                   FROM memorials m
                   JOIN edicts e ON m.edict_id = e.id
                   WHERE {join_where}
                   ORDER BY m.created_at DESC
                   LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"""SELECT COUNT(*) FROM memorials m
                    JOIN edicts e ON m.edict_id = e.id
                    WHERE {count_where}""",
                params,
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
