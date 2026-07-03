"""Storage Event 领域 Mixin —— 事件流水、审计统计、网络事件查询。"""

import json
import sqlite3
import threading
from datetime import UTC, datetime

from ulid import ULID


class EventMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Events ---

    def append_event(
        self,
        edict_id: str,
        memorial_id: str | None,
        event_type: str,
        payload: dict,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO events
                   (id, edict_id, memorial_id, event_type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    str(ULID()),
                    edict_id,
                    memorial_id,
                    event_type,
                    json.dumps(payload, default=str),
                    datetime.now(UTC).isoformat(),
                ),
            )
            # 心跳（Multica 借鉴 #1）：活跃 memorial 每次有事件即刷新，供 sweeper 判活。
            # 仅活跃态更新，避免复活已终态/暂停的 memorial。
            if memorial_id:
                self._conn.execute(
                    "UPDATE memorials SET last_heartbeat_at = ? "
                    "WHERE id = ? AND status IN ('running', 'planning', 'auditing')",
                    (datetime.now(UTC).isoformat(), memorial_id),
                )

    def get_events(self, edict_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM events WHERE edict_id = ? ORDER BY created_at ASC",
                (edict_id,),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "edict_id": r["edict_id"],
                "memorial_id": r["memorial_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def get_event_stats(self) -> dict:
        """Get event count by type."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT event_type, COUNT(*) as count FROM events GROUP BY event_type ORDER BY count DESC"
            )
            rows = cur.fetchall()
            return {row[0]: row[1] for row in rows}

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        """Get recent events across all edicts."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, edict_id, memorial_id, event_type, payload_json, created_at "
                "FROM events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [
                {
                    "id": row[0],
                    "edict_id": row[1],
                    "memorial_id": row[2],
                    "event_type": row[3],
                    "payload": json.loads(row[4]) if row[4] else {},
                    "created_at": row[5],
                }
                for row in cur.fetchall()
            ]

    def list_persona_events(self, persona_id: str, since_iso: str) -> list[dict]:
        """Events whose payload.persona_id = persona_id AND created_at >= since_iso.

        Note: the project's events table uses columns `created_at` (not `timestamp`)
        and `payload_json` (not `payload`); see CREATE TABLE near the top of this
        file. Output dict normalises `payload_json` -> `payload` so callers see a
        decoded dict, matching the convention used by `get_events` above.
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT id, event_type, edict_id, memorial_id, created_at, payload_json
                FROM events
                WHERE created_at >= ?
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (since_iso,),
            )
            rows = [dict(r) for r in cur.fetchall()]
        out: list[dict] = []
        for r in rows:
            try:
                payload = json.loads(r.get("payload_json") or "{}")
            except json.JSONDecodeError:
                payload = {}
            if (
                payload.get("persona_id") == persona_id
                or payload.get("assigned_persona_id") == persona_id
                or payload.get("assigned_official") == persona_id
            ):
                r["payload"] = payload
                r.pop("payload_json", None)
                out.append(r)
        return out

    # --- Audit ---

    def get_audit_stats(self, top_n: int = 20) -> dict:
        with self._lock:
            # Query A — global summary
            summary_row = self._conn.execute("""
                SELECT
                    COUNT(*) as total_memorials,
                    COALESCE(SUM(json_extract(usage_json, '$.prompt_tokens')), 0) as total_prompt_tokens,
                    COALESCE(SUM(json_extract(usage_json, '$.completion_tokens')), 0) as total_completion_tokens,
                    COALESCE(SUM(json_extract(usage_json, '$.total_tokens')), 0) as total_tokens,
                    COUNT(CASE WHEN json_extract(audit_json, '$.verdict') = 'pass' THEN 1 END) as audit_pass,
                    COUNT(CASE WHEN json_extract(audit_json, '$.verdict') = 'flag' THEN 1 END) as audit_flag,
                    COUNT(CASE WHEN json_extract(audit_json, '$.verdict') = 'block' THEN 1 END) as audit_block,
                    COUNT(CASE WHEN review_status = 'pending' THEN 1 END) as review_pending,
                    COUNT(CASE WHEN review_status = 'approved' THEN 1 END) as review_approved,
                    COUNT(CASE WHEN review_status = 'rejected' THEN 1 END) as review_rejected
                FROM memorials
            """).fetchone()

            summary = {
                "total_memorials": summary_row["total_memorials"],
                "total_prompt_tokens": summary_row["total_prompt_tokens"],
                "total_completion_tokens": summary_row["total_completion_tokens"],
                "total_tokens": summary_row["total_tokens"],
                "audit_pass": summary_row["audit_pass"],
                "audit_flag": summary_row["audit_flag"],
                "audit_block": summary_row["audit_block"],
                "review_pending": summary_row["review_pending"],
                "review_approved": summary_row["review_approved"],
                "review_rejected": summary_row["review_rejected"],
            }

            # Query B — per-edict token usage
            per_edict_rows = self._conn.execute(
                """
                SELECT
                    m.edict_id,
                    e.title as edict_title,
                    e.priority,
                    json_extract(e.runtime_json, '$.token_budget') as token_budget,
                    COUNT(*) as memorial_count,
                    COALESCE(SUM(json_extract(m.usage_json, '$.prompt_tokens')), 0) as prompt_tokens,
                    COALESCE(SUM(json_extract(m.usage_json, '$.completion_tokens')), 0) as completion_tokens,
                    COALESCE(SUM(json_extract(m.usage_json, '$.total_tokens')), 0) as total_tokens
                FROM memorials m
                JOIN edicts e ON m.edict_id = e.id
                GROUP BY m.edict_id
                ORDER BY total_tokens DESC
                LIMIT ?
            """,
                (top_n,),
            ).fetchall()

            per_edict = [
                {
                    "edict_id": r["edict_id"],
                    "edict_title": r["edict_title"],
                    "priority": r["priority"],
                    "token_budget": r["token_budget"],
                    "memorial_count": r["memorial_count"],
                    "prompt_tokens": r["prompt_tokens"],
                    "completion_tokens": r["completion_tokens"],
                    "total_tokens": r["total_tokens"],
                }
                for r in per_edict_rows
            ]

            # Query C — recent audits
            audit_rows = self._conn.execute("""
                SELECT
                    m.id, m.edict_id, e.title as edict_title,
                    m.audit_json, m.review_status, m.completed_at
                FROM memorials m
                JOIN edicts e ON m.edict_id = e.id
                WHERE m.audit_json IS NOT NULL
                ORDER BY COALESCE(m.completed_at, m.created_at) DESC
                LIMIT 20
            """).fetchall()

            recent_audits = []
            for r in audit_rows:
                audit_data = json.loads(r["audit_json"]) if r["audit_json"] else {}
                recent_audits.append(
                    {
                        "memorial_id": r["id"],
                        "edict_id": r["edict_id"],
                        "edict_title": r["edict_title"],
                        "verdict": audit_data.get("verdict"),
                        "reasons": audit_data.get("reasons", []),
                        "rules_checked": audit_data.get("rules_checked", 0),
                        "llm_reviewed": audit_data.get("llm_reviewed", False),
                        "review_status": r["review_status"],
                        "completed_at": r["completed_at"],
                    }
                )

        return {
            "summary": summary,
            "per_edict": per_edict,
            "recent_audits": recent_audits,
        }

    def list_network_events(
        self,
        *,
        limit: int = 50,
        tool: str | None = None,
        host: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """返回带 details.network 的 tool.completed / tool.failed 事件，时间降序。

        Python 侧过滤，避免 sqlite json_extract 版本兼容。
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT e.id, e.edict_id, e.memorial_id, e.event_type,
                          e.payload_json, e.created_at, ed.title as edict_title
                   FROM events e
                   LEFT JOIN edicts ed ON ed.id = e.edict_id
                   WHERE e.event_type IN ('tool.completed', 'tool.failed')
                   ORDER BY e.created_at DESC
                   LIMIT ?""",
                (max(limit * 5, 200),),  # 预取多一些，留余量给 Python 过滤
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                continue
            details = payload.get("details") or {}
            network = details.get("network") if isinstance(details, dict) else None
            if not isinstance(network, dict):
                continue

            row_tool = network.get("tool") or payload.get("tool")
            row_is_error = bool(payload.get("is_error"))

            # 过滤
            if tool and row_tool != tool:
                continue
            if host and network.get("host") != host:
                continue
            if status == "ok" and row_is_error:
                continue
            if status == "error" and not row_is_error:
                continue

            out.append(
                {
                    "event_id": r["id"],
                    "created_at": r["created_at"],
                    "edict_id": r["edict_id"],
                    "edict_title": r["edict_title"],
                    "tool": row_tool,
                    "host": network.get("host"),
                    "method": network.get("method"),
                    "http_status": network.get("http_status"),
                    "bytes_out": network.get("bytes_out"),
                    "credential_name": network.get("credential_name"),
                    "cached": bool(network.get("cached", False)),
                    "is_error": row_is_error,
                    "reason": payload.get("result_preview") if row_is_error else None,
                    "provider": network.get("provider"),  # web_search
                    "result_count": network.get("result_count"),  # web_search
                    "truncated": bool(network.get("truncated", False)),  # api_request
                }
            )
            if len(out) >= limit:
                break
        return out

    def last_activity_at(self) -> str | None:
        """Most recent event timestamp (ISO) for idle gating; None if no events.

        Execution events carry an edict_id and are persisted, so MAX(created_at)
        across the events table approximates the last real agent activity.
        """
        with self._lock:
            row = self._conn.execute("SELECT MAX(created_at) AS ts FROM events").fetchone()
        return row["ts"] if row and row["ts"] else None
