"""Storage 廷议领域 Mixin —— 廷议会话的落库、增量更新与列表查询。"""

import json
import sqlite3
import threading

from tianshu.consultation.models import ConsultationResponse


def _to_row_params(consultation: ConsultationResponse) -> tuple:
    request = consultation.request
    return (
        consultation.id,
        consultation.status,
        request.topic if request else "",
        request.edict_id if request else None,
        json.dumps(request.model_dump(mode="json") if request else {}, ensure_ascii=False),
        json.dumps(
            [o.model_dump(mode="json") for o in consultation.opinions],
            ensure_ascii=False,
        ),
        consultation.synthesis,
        consultation.decision,
        consultation.error,
        consultation.created_at.isoformat(),
        consultation.completed_at.isoformat() if consultation.completed_at else None,
    )


def _row_to_consultation(row: sqlite3.Row) -> ConsultationResponse:
    return ConsultationResponse.model_validate(
        {
            "id": row["id"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "opinions": json.loads(row["opinions_json"]),
            "synthesis": row["synthesis"],
            "decision": row["decision"],
            "error": row["error"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }
    )


class ConsultationMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def save_consultation(self, consultation: ConsultationResponse) -> None:
        """整体 upsert —— 廷议按状态推进多次落盘（pending → 每条意见 → completed）。"""
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO consultations
                   (id, status, topic, edict_id, request_json, opinions_json,
                    synthesis, decision, error, created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status,
                     opinions_json=excluded.opinions_json,
                     synthesis=excluded.synthesis,
                     decision=excluded.decision,
                     error=excluded.error,
                     completed_at=excluded.completed_at""",
                _to_row_params(consultation),
            )

    def get_consultation(self, consultation_id: str) -> ConsultationResponse | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM consultations WHERE id = ?",
                (consultation_id,),
            ).fetchone()
        return _row_to_consultation(row) if row else None

    def list_consultations(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ConsultationResponse]:
        sql = "SELECT * FROM consultations"
        params: list[object] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_consultation(row) for row in rows]

    def count_consultations(self, *, status: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM consultations"
        params: list[object] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def mark_stale_consultations_failed(self, error: str) -> int:
        """进程重启后把孤儿 running/pending 廷议判死——否则前端会永久轮询。"""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE consultations
                   SET status = 'failed',
                       error = ?,
                       completed_at = COALESCE(completed_at, datetime('now'))
                   WHERE status IN ('pending', 'running')""",
                (error,),
            )
        return cursor.rowcount or 0
