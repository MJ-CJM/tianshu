"""Storage 时序知识图谱 Mixin —— 三元组的时序增删查(迭代 4「记忆 2.0」)。

时序语义:每条三元组带 valid_from / valid_to(NULL=至今有效)。as_of 查询返回
某时刻有效的事实,支持"偏好漂移"表达与过时事实作废。校勘/幂等在
memory.kg.KnowledgeGraph 层做,这里只管纯存取。
"""

import sqlite3
import threading


def _row_to_triple(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "subject": row["subject"],
        "predicate": row["predicate"],
        "object": row["object"],
        "scope": row["scope"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "confidence": row["confidence"],
        "source": row["source"],
        "created_at": row["created_at"],
    }


class KgMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def save_kg_triple(self, triple: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO kg_triples
                   (id, subject, predicate, object, scope,
                    valid_from, valid_to, confidence, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    triple["id"],
                    triple["subject"],
                    triple["predicate"],
                    triple["object"],
                    triple.get("scope", "court"),
                    triple["valid_from"],
                    triple.get("valid_to"),
                    float(triple.get("confidence", 1.0)),
                    triple.get("source", "agent"),
                    triple["created_at"],
                ),
            )

    def invalidate_kg_triple(self, triple_id: str, valid_to: str) -> None:
        """给三元组盖上失效时刻(时序更新时旧事实退场)。"""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE kg_triples SET valid_to = ? WHERE id = ? AND valid_to IS NULL",
                (valid_to, triple_id),
            )

    def query_kg_triples(
        self,
        *,
        scope: str = "court",
        subject: str | None = None,
        predicate: str | None = None,
        as_of: str | None = None,
    ) -> list[dict]:
        """as_of 时刻有效的三元组(valid_from <= as_of < valid_to 或 valid_to 为空)。

        as_of 为 None 时只返回**当前有效**(valid_to IS NULL)——比"当下时刻"更严格,
        排除已被时序更新替换掉的历史事实。
        """
        sql = "SELECT * FROM kg_triples WHERE scope = ?"
        params: list = [scope]
        if subject is not None:
            sql += " AND subject = ?"
            params.append(subject)
        if predicate is not None:
            sql += " AND predicate = ?"
            params.append(predicate)
        if as_of is None:
            sql += " AND valid_to IS NULL"
        else:
            sql += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"
            params.extend([as_of, as_of])
        sql += " ORDER BY valid_from DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_triple(r) for r in rows]

    def get_kg_triple(self, triple_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM kg_triples WHERE id = ?", (triple_id,)
            ).fetchone()
        return _row_to_triple(row) if row else None
