"""Storage Edict 领域 Mixin —— 敕令 CRUD、生命周期状态、幂等查找、host 引用检查。"""

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from tianshu.models import Edict
from tianshu.models.governance_contract import (
    LegacyEdictGovernanceMapper,
    RequestedGovernanceContractV1,
)
from tianshu.storage.mappers import _row_to_edict


def _insert_requested_governance_contract(
    conn: sqlite3.Connection,
    edict: Edict,
) -> RequestedGovernanceContractV1:
    explicit = edict.governance_contract is not None
    contract = edict.governance_contract or LegacyEdictGovernanceMapper.from_edict(
        edict,
        default_workspace_id=str(edict.metadata.get("workspace_id") or "legacy-default"),
    )
    conn.execute(
        """
        INSERT INTO requested_governance_contracts
            (edict_id, schema_version, contract_json, contract_hash, source, created_at)
        VALUES (?, '1', ?, ?, ?, ?)
        """,
        (
            edict.id,
            contract.canonical_json(),
            contract.content_hash,
            "explicit" if explicit else "legacy_derived",
            datetime.now(UTC).isoformat(),
        ),
    )
    return contract


def _insert_edict(
    conn: sqlite3.Connection,
    edict: Edict,
) -> RequestedGovernanceContractV1:
    acceptance_json = edict.acceptance.model_dump_json() if edict.acceptance else None
    conn.execute(
        """INSERT INTO edicts
           (id, title, goal, context, status, created_at,
            idempotency_key, source, submitter, priority, review_policy,
            output_format, constraints_json, schedule_json, dispatch_json,
            runtime_json, metadata_json, assigned_persona_id,
            planner_persona_id, plan_review, acceptance_json, execution_profile)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            edict.id,
            edict.title,
            edict.goal,
            edict.context,
            edict.status.value,
            edict.created_at.isoformat(),
            edict.idempotency_key,
            edict.source,
            edict.submitter,
            edict.priority,
            edict.review_policy,
            edict.output_format,
            json.dumps(edict.constraints),
            edict.schedule.model_dump_json(),
            edict.dispatch.model_dump_json() if edict.dispatch else None,
            edict.runtime.model_dump_json(),
            json.dumps(edict.metadata, default=str),
            edict.assigned_persona_id,
            edict.planner_persona_id,
            int(edict.plan_review),
            acceptance_json,
            edict.execution_profile,
        ),
    )
    return _insert_requested_governance_contract(conn, edict)


def get_edict_current(conn: sqlite3.Connection, edict_id: str) -> Edict | None:
    """Load one Edict and its requested contract on the caller-owned transaction."""

    row = conn.execute(
        """
        SELECT edict.*, requested.contract_json AS requested_contract_json,
               requested.contract_hash AS requested_contract_hash
        FROM edicts AS edict
        LEFT JOIN requested_governance_contracts AS requested ON requested.edict_id = edict.id
        WHERE edict.id = ?
        """,
        (edict_id,),
    ).fetchone()
    if row is None:
        return None
    contract = None
    if row["requested_contract_json"] is not None:
        contract = RequestedGovernanceContractV1.model_validate_json(row["requested_contract_json"])
        if contract.content_hash != row["requested_contract_hash"]:
            raise ValueError(f"requested governance contract hash mismatch for {edict_id}")
    return _row_to_edict(row, governance_contract=contract)


@dataclass(frozen=True, slots=True)
class ControlEdictTotals:
    unarchived: int
    awaiting_follow_up: int
    cancelled: int


def count_control_edict_totals_current(
    conn: sqlite3.Connection,
    *,
    submitter: str | None,
) -> ControlEdictTotals:
    """Return workspace totals from the caller-owned Control Center snapshot."""

    if submitter is not None and not submitter.strip():
        raise ValueError("submitter must not be blank")
    submitter_filter = " AND edict.submitter = ?" if submitter is not None else ""
    parameters: tuple[object, ...] = (submitter,) if submitter is not None else ()
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS unarchived,
            COALESCE(SUM(CASE WHEN edict.status = 'cancelled' THEN 1 ELSE 0 END), 0)
                AS cancelled,
            COALESCE(SUM(CASE
                WHEN edict.status = 'open'
                 AND COALESCE(json_extract(edict.runtime_json, '$.conversation'), 1) = 1
                 AND COALESCE(
                       json_extract(edict.runtime_json, '$.lifecycle_phase'), 'active'
                     ) = 'active'
                 AND latest.status = 'completed'
                 AND COALESCE(latest.review_status, '') != 'pending'
                 AND NOT EXISTS (
                       SELECT 1 FROM run_states AS state
                       WHERE state.edict_id = edict.id
                         AND state.phase NOT IN ('completed', 'failed')
                     )
                 AND NOT EXISTS (
                       SELECT 1 FROM decision_requests AS decision
                       WHERE decision.edict_id = edict.id
                         AND decision.status = 'pending'
                     )
                THEN 1 ELSE 0
            END), 0) AS awaiting_follow_up
        FROM edicts AS edict
        LEFT JOIN memorials AS latest ON latest.id = (
            SELECT memorial.id
            FROM memorials AS memorial
            WHERE memorial.edict_id = edict.id
            ORDER BY memorial.created_at DESC, memorial.id DESC
            LIMIT 1
        )
        WHERE json_extract(edict.metadata_json, '$.archived_at') IS NULL
        {submitter_filter}
        """,
        parameters,
    ).fetchone()
    return ControlEdictTotals(
        unarchived=int(row["unarchived"]),
        awaiting_follow_up=int(row["awaiting_follow_up"]),
        cancelled=int(row["cancelled"]),
    )


class EdictMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Edict ---

    def _save_requested_governance_contract_unlocked(
        self,
        edict: Edict,
    ) -> RequestedGovernanceContractV1:
        return _insert_requested_governance_contract(self._conn, edict)

    def get_requested_governance_contract_record(self, edict_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT schema_version, contract_json, contract_hash, source, created_at
                FROM requested_governance_contracts WHERE edict_id = ?
                """,
                (edict_id,),
            ).fetchone()
        if row is None:
            return None
        contract = RequestedGovernanceContractV1.model_validate_json(row["contract_json"])
        if contract.content_hash != row["contract_hash"]:
            raise ValueError(f"requested governance contract hash mismatch for {edict_id}")
        return {
            "schema_version": row["schema_version"],
            "contract": contract,
            "contract_hash": row["contract_hash"],
            "source": row["source"],
            "created_at": row["created_at"],
        }

    def save_edict(self, edict: Edict) -> None:
        with self._lock, self._conn:
            _insert_edict(self._conn, edict)

    def get_edict(self, edict_id: str) -> Edict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM edicts WHERE id = ?", (edict_id,)).fetchone()
        if row is None:
            return None
        record = self.get_requested_governance_contract_record(edict_id)
        return _row_to_edict(
            row,
            governance_contract=record["contract"] if record else None,
        )

    def list_edicts(
        self,
        status: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
        exclude_assistant_chat: bool = False,
        instance_id: str | None = None,
        submitter: str | None = None,
        include_consultation: bool = False,
    ) -> tuple[list[Edict], int]:
        """列敕令。

        include_consultation=False（默认）时过滤掉廷议合成的议事敕令
        （source='consultation'，issue #59）：它们只是官员工具调用的策略与审计锚点，
        涌进御书房会把真正的敕令淹没。想查证时显式传 True。

        exclude_assistant_chat=True 时过滤掉 metadata.assistant_chat=true 的聊天敕令。
        SQL 用 json_extract(metadata_json, '$.assistant_chat') 实现（SQLite 中 JSON true → 整数 1）。

        instance_id=None 时不按实例过滤（web 全局视图）；指定时只返回
        metadata.instance_id 匹配的敕令。仅当实例 id 以 ``-default`` 结尾时，
        额外纳入无 instance_id 的存量敕令中 metadata.channel 等于该实例 channel
        前缀的（向后兼容旧敕令）；非 default 实例不继承未打标的旧敕令。
        """
        conditions: list[str] = []
        params: list[str | int] = []
        if not include_consultation:
            conditions.append("source != 'consultation'")
        if status:
            conditions.append("status = ?")
            params.append(status)
        if search:
            conditions.append("(title LIKE ? OR goal LIKE ?)")
            params.append(f"%{search}%")
            params.append(f"%{search}%")
        if submitter is not None:
            conditions.append("submitter = ?")
            params.append(submitter)
        if instance_id is not None:
            if instance_id.endswith("-default"):
                channel_prefix = instance_id[: -len("-default")]
                conditions.append(
                    "(json_extract(metadata_json, '$.instance_id') = ? "
                    " OR (json_extract(metadata_json, '$.instance_id') IS NULL "
                    "     AND json_extract(metadata_json, '$.channel') = ?))"
                )
                params.extend([instance_id, channel_prefix])
            else:
                conditions.append("json_extract(metadata_json, '$.instance_id') = ?")
                params.append(instance_id)
        if exclude_assistant_chat:
            conditions.append(
                "(json_extract(metadata_json, '$.assistant_chat') IS NULL "
                "OR json_extract(metadata_json, '$.assistant_chat') != 1)"
            )
        # 归档敕令（metadata.archived_at 非空）不进列表：含治理证据的敕令
        # 物理删除被证据链拦截时降级为归档（见 edicts_api.delete_edict）。
        conditions.append("json_extract(metadata_json, '$.archived_at') IS NULL")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM edicts{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM edicts{where}",
                params,
            ).fetchone()[0]
        edicts = []
        for row in rows:
            record = self.get_requested_governance_contract_record(row["id"])
            edicts.append(
                _row_to_edict(
                    row,
                    governance_contract=record["contract"] if record else None,
                )
            )
        return edicts, total

    def update_edict(
        self,
        edict_id: str,
        title: str | None = None,
        goal: str | None = None,
        context: str | None = None,
    ) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT contract_json FROM requested_governance_contracts WHERE edict_id = ?",
                (edict_id,),
            ).fetchone()
        if row is not None and (goal is not None or context is not None):
            contract = RequestedGovernanceContractV1.model_validate_json(row["contract_json"])
            if (goal is not None and goal != contract.objective.goal) or (
                context is not None and context != contract.objective.context
            ):
                raise ValueError("goal/context are bound by a frozen governance contract")

        sets: list[str] = []
        params: list[str] = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if goal is not None:
            sets.append("goal = ?")
            params.append(goal)
        if context is not None:
            sets.append("context = ?")
            params.append(context)
        if not sets:
            return
        params.append(edict_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE edicts SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def delete_edict(self, edict_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM submission_idempotency WHERE edict_id = ?",
                (edict_id,),
            )
            self._conn.execute("DELETE FROM events WHERE edict_id = ?", (edict_id,))
            # legacy 分流占位（candidate_id NULL，零实验状态的冠军空转记录）随
            # 敕令清理；真实验记录（candidate_id 非空）仍受触发器保护，命中时
            # memorials 的 RESTRICT 会让整个删除 409（治理证据不可删语义保留）。
            self._conn.execute(
                "DELETE FROM run_evolution_assignments WHERE candidate_id IS NULL "
                "AND memorial_id IN (SELECT id FROM memorials WHERE edict_id = ?)",
                (edict_id,),
            )
            self._conn.execute("DELETE FROM memorials WHERE edict_id = ?", (edict_id,))
            self._conn.execute("DELETE FROM edicts WHERE id = ?", (edict_id,))

    def archive_edict(self, edict_id: str) -> None:
        """归档：metadata.archived_at 打标，列表隐藏；治理证据链原样保留。"""
        from datetime import UTC, datetime

        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT metadata_json FROM edicts WHERE id = ?", (edict_id,)
            ).fetchone()
            if not row:
                return
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["archived_at"] = datetime.now(UTC).isoformat()
            self._conn.execute(
                "UPDATE edicts SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), edict_id),
            )

    def update_edict_status(self, edict_id: str, status: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE edicts SET status = ? WHERE id = ?",
                (status, edict_id),
            )

    def update_edict_assigned_persona(self, edict_id: str, persona_id: str) -> None:
        """改派敕令承办官员。

        用于渠道助手 persona 热切换：进行中的聊天敕令 assigned_persona_id 是
        建会话那一刻的快照，不改它则新配置对当前会话永不生效。
        """
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE edicts SET assigned_persona_id = ? WHERE id = ?",
                (persona_id, edict_id),
            )

    def update_edict_lifecycle_phase(self, edict_id: str, phase: str) -> None:
        """部分更新 runtime_json 的 lifecycle_phase 字段，保留其他字段。"""
        if phase not in ("active", "paused", "winding_down", "complete"):
            raise ValueError(f"unknown lifecycle_phase: {phase}")
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT runtime_json FROM edicts WHERE id = ?",
                (edict_id,),
            ).fetchone()
            if not row:
                return
            runtime = json.loads(row["runtime_json"] or "{}")
            runtime["lifecycle_phase"] = phase
            self._conn.execute(
                "UPDATE edicts SET runtime_json = ? WHERE id = ?",
                (json.dumps(runtime), edict_id),
            )

    # --- Idempotency ---

    def find_edict_by_idempotency_key(
        self,
        submitter: str | None,
        idempotency_key: str,
    ) -> Edict | None:
        """Find an existing edict by (submitter, idempotency_key) pair."""
        with self._lock:
            if submitter:
                row = self._conn.execute(
                    "SELECT * FROM edicts WHERE idempotency_key = ? AND submitter = ?",
                    (idempotency_key, submitter),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM edicts WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
        if row is None:
            return None
        record = self.get_requested_governance_contract_record(row["id"])
        return _row_to_edict(
            row,
            governance_contract=record["contract"] if record else None,
        )

    def find_edicts_referencing_host(self, host_pattern: str) -> list[str]:
        """返回引用此 host 的未结束 Edict id 列表。仅匹配精确 host，通配模式统一视为精确。

        用于凭证删除前置检查 — 有引用即阻止删除。
        """
        with self._lock:
            # edicts 表的 runtime 字段存 JSON；在 JSON 里 grep host
            # 约束：只搜 runtime_json LIKE 包含 host 的，Python 侧再确认
            cur = self._conn.execute(
                """SELECT id, runtime_json FROM edicts
                   WHERE status = 'open'
                   AND runtime_json LIKE ?""",
                (f"%{host_pattern}%",),
            )
            hits: list[str] = []
            for row in cur.fetchall():
                rjson = row["runtime_json"] or "{}"
                try:
                    runtime = json.loads(rjson)
                except Exception:
                    continue
                hosts = runtime.get("api_request_hosts") or []
                write_hosts = runtime.get("api_request_write_hosts") or []
                if host_pattern in hosts or host_pattern in write_hosts:
                    hits.append(row["id"])
            return hits
