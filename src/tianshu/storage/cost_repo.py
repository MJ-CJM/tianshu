"""Storage Cost 领域 Mixin —— 成本流水与预算。"""

import sqlite3
import threading
from datetime import UTC, datetime

from ulid import ULID


class CostMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- Cost Ledger ---

    def save_cost_record(self, record) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO cost_ledger
                   (id, edict_id, memorial_id, provider_name, model,
                    prompt_tokens, completion_tokens, total_tokens, cost_cny, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.edict_id,
                    record.memorial_id,
                    record.provider_name,
                    record.model,
                    record.prompt_tokens,
                    record.completion_tokens,
                    record.total_tokens,
                    record.cost_cny,
                    record.created_at.isoformat(),
                ),
            )

    def get_cost_summary(
        self,
        period: str | None = None,
        edict_id: str | None = None,
    ) -> dict:
        conditions: list[str] = []
        params: list = []
        if edict_id:
            conditions.append("edict_id = ?")
            params.append(edict_id)
        if period == "day":
            conditions.append("date(created_at) = date('now')")
        elif period == "week":
            conditions.append("date(created_at) >= date('now', '-7 days')")
        elif period == "month":
            conditions.append("date(created_at) >= date('now', '-30 days')")

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            row = self._conn.execute(
                f"""SELECT
                    COUNT(*) as total_records,
                    COALESCE(SUM(prompt_tokens), 0) as total_prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) as total_completion_tokens,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(cost_cny), 0.0) as total_cost_cny
                FROM cost_ledger{where}""",
                params,
            ).fetchone()
        return {
            "total_records": row["total_records"],
            "total_prompt_tokens": row["total_prompt_tokens"],
            "total_completion_tokens": row["total_completion_tokens"],
            "total_tokens": row["total_tokens"],
            "total_cost_cny": row["total_cost_cny"],
        }

    def list_cost_records(
        self,
        edict_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        conditions: list[str] = []
        params: list = []
        if edict_id:
            conditions.append("edict_id = ?")
            params.append(edict_id)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM cost_ledger{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            total = self._conn.execute(
                f"SELECT COUNT(*) FROM cost_ledger{where}", params
            ).fetchone()[0]
        return [dict(r) for r in rows], total

    def get_budget(self, scope: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM cost_budgets WHERE scope = ?", (scope,)
            ).fetchone()
        return dict(row) if row else None

    def upsert_budget(
        self,
        scope: str,
        budget_cny: float,
        period: str = "monthly",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT id FROM cost_budgets WHERE scope = ?", (scope,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE cost_budgets SET budget_cny = ?, period = ? WHERE scope = ?",
                    (budget_cny, period, scope),
                )
            else:
                self._conn.execute(
                    """INSERT INTO cost_budgets (id, scope, budget_cny, period, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (str(ULID()), scope, budget_cny, period, now),
                )

    def update_budget_spent(self, scope: str, amount_cny: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE cost_budgets SET spent_cny = spent_cny + ? WHERE scope = ?",
                (amount_cny, scope),
            )
