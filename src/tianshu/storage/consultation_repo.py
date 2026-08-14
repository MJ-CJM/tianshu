"""Storage 廷议领域 Mixin —— 廷议容器与轮次的落库、增量更新与列表查询。"""

import json
import sqlite3
import threading
from datetime import UTC, datetime

from tianshu.consultation.models import ConsultationRequest, ConsultationResponse, ConsultationRound


def _consultation_params(consultation: ConsultationResponse) -> tuple:
    request = consultation.request or ConsultationRequest(topic="")
    return (
        consultation.id,
        consultation.status,
        request.topic,
        request.edict_id,
        json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
        json.dumps(request.censor_persona_ids, ensure_ascii=False),
        consultation.verdict,
        consultation.verdict_at.isoformat() if consultation.verdict_at else None,
        consultation.error,
        consultation.created_at.isoformat(),
        consultation.completed_at.isoformat() if consultation.completed_at else None,
    )


def _round_params(round_: ConsultationRound) -> tuple:
    return (
        round_.id,
        round_.consultation_id,
        round_.round_index,
        round_.prompt,
        json.dumps(round_.participant_ids, ensure_ascii=False),
        json.dumps([o.model_dump(mode="json") for o in round_.opinions], ensure_ascii=False),
        round_.synthesis,
        round_.proposal,
        round_.synthesizer_persona_id,
        round_.synthesizer_name,
        round_.synthesizer_department,
        round_.status,
        round_.error,
        round_.created_at.isoformat(),
        round_.completed_at.isoformat() if round_.completed_at else None,
    )


def _row_to_round(row: sqlite3.Row) -> ConsultationRound:
    return ConsultationRound.model_validate(
        {
            "id": row["id"],
            "consultation_id": row["consultation_id"],
            "round_index": row["round_index"],
            "prompt": row["prompt"],
            "participant_ids": json.loads(row["participant_ids_json"]),
            "opinions": json.loads(row["opinions_json"]),
            "synthesis": row["synthesis"],
            "proposal": row["proposal"],
            "synthesizer_persona_id": row["synthesizer_persona_id"],
            "synthesizer_name": row["synthesizer_name"],
            "synthesizer_department": row["synthesizer_department"],
            "status": row["status"],
            "error": row["error"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }
    )


def _row_to_consultation(
    row: sqlite3.Row,
    rounds: list[ConsultationRound],
) -> ConsultationResponse:
    request = json.loads(row["request_json"])
    # 言官名单单列存放（可在廷议中途改任），以列为准而非 request 快照
    request["censor_persona_ids"] = json.loads(row["censor_persona_ids_json"] or "[]")
    return ConsultationResponse.model_validate(
        {
            "id": row["id"],
            "status": row["status"],
            "request": request,
            "rounds": [r.model_dump(mode="json") for r in rounds],
            "verdict": row["verdict"],
            "verdict_at": row["verdict_at"],
            "error": row["error"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
        }
    )


class ConsultationMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- consultation container ---

    def save_consultation(self, consultation: ConsultationResponse) -> None:
        """整体 upsert 容器；轮次各自落库，不在此处联动写。"""
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO consultations
                   (id, status, topic, edict_id, request_json, censor_persona_ids_json,
                    verdict, verdict_at, error, created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     status=excluded.status,
                     censor_persona_ids_json=excluded.censor_persona_ids_json,
                     verdict=excluded.verdict,
                     verdict_at=excluded.verdict_at,
                     error=excluded.error,
                     completed_at=excluded.completed_at""",
                _consultation_params(consultation),
            )

    def get_consultation(self, consultation_id: str) -> ConsultationResponse | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM consultations WHERE id = ?",
                (consultation_id,),
            ).fetchone()
            if row is None:
                return None
            round_rows = self._conn.execute(
                """SELECT * FROM consultation_rounds
                   WHERE consultation_id = ? ORDER BY round_index""",
                (consultation_id,),
            ).fetchall()
        return _row_to_consultation(row, [_row_to_round(r) for r in round_rows])

    def list_consultations(
        self,
        *,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ConsultationResponse]:
        """列表只取容器概要——不 join 轮次，避免把每场的全部意见都拖出来。"""
        sql = "SELECT * FROM consultations"
        params: list[object] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_consultation(row, []) for row in rows]

    def count_consultations(self, *, status: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM consultations"
        params: list[object] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def set_consultation_verdict(self, consultation_id: str, verdict: str) -> bool:
        """落裁决——LLM 只出票拟，最终决定由用户写下（issue #55）。"""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE consultations SET verdict = ?, verdict_at = ? WHERE id = ?",
                (verdict, now, consultation_id),
            )
        return bool(cursor.rowcount)

    def mark_stale_consultations_failed(self, error: str) -> int:
        """进程重启后把孤儿 running/pending 廷议判死——否则前端会永久轮询。"""
        # 时间戳走 Python 侧 isoformat，与其他写入路径一致带时区；
        # SQLite 的 datetime('now') 产出的是无时区字符串。
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """UPDATE consultations
                   SET status = 'failed',
                       error = ?,
                       completed_at = COALESCE(completed_at, ?)
                   WHERE status IN ('pending', 'running')""",
                (error, now),
            )
            self._conn.execute(
                """UPDATE consultation_rounds
                   SET status = 'failed',
                       error = COALESCE(error, ?),
                       completed_at = COALESCE(completed_at, ?)
                   WHERE status IN ('pending', 'running')""",
                (error, now),
            )
        return cursor.rowcount or 0

    # --- rounds ---

    def save_consultation_round(self, round_: ConsultationRound) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO consultation_rounds
                   (id, consultation_id, round_index, prompt, participant_ids_json,
                    opinions_json, synthesis, proposal, synthesizer_persona_id,
                    synthesizer_name, synthesizer_department, status, error,
                    created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     opinions_json=excluded.opinions_json,
                     synthesis=excluded.synthesis,
                     proposal=excluded.proposal,
                     synthesizer_persona_id=excluded.synthesizer_persona_id,
                     synthesizer_name=excluded.synthesizer_name,
                     synthesizer_department=excluded.synthesizer_department,
                     status=excluded.status,
                     error=excluded.error,
                     completed_at=excluded.completed_at""",
                _round_params(round_),
            )

    def get_consultation_round(self, round_id: str) -> ConsultationRound | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM consultation_rounds WHERE id = ?",
                (round_id,),
            ).fetchone()
        return _row_to_round(row) if row else None

    def list_consultation_rounds(self, consultation_id: str) -> list[ConsultationRound]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM consultation_rounds
                   WHERE consultation_id = ? ORDER BY round_index""",
                (consultation_id,),
            ).fetchall()
        return [_row_to_round(row) for row in rows]

    def next_round_index(self, consultation_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(round_index) FROM consultation_rounds WHERE consultation_id = ?",
                (consultation_id,),
            ).fetchone()
        return 0 if row is None or row[0] is None else int(row[0]) + 1
