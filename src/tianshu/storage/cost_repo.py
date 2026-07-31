"""Storage Cost 领域 Mixin —— 成本流水与预算。"""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

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
                    prompt_tokens, completion_tokens, total_tokens, cache_read_tokens,
                    cost_cny, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.edict_id,
                    record.memorial_id,
                    record.provider_name,
                    record.model,
                    record.prompt_tokens,
                    record.completion_tokens,
                    record.total_tokens,
                    record.cache_read_tokens,
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
                    COALESCE(SUM(cache_read_tokens), 0) as total_cache_read_tokens,
                    COALESCE(SUM(total_tokens), 0) as total_tokens,
                    COALESCE(SUM(cost_cny), 0.0) as total_cost_cny
                FROM cost_ledger{where}""",
                params,
            ).fetchone()
        return {
            "total_records": row["total_records"],
            "total_prompt_tokens": row["total_prompt_tokens"],
            "total_completion_tokens": row["total_completion_tokens"],
            "total_cache_read_tokens": row["total_cache_read_tokens"],
            "total_tokens": row["total_tokens"],
            "total_cost_cny": row["total_cost_cny"],
        }

    def estimate_edict_cost(self) -> tuple[float, int]:
        """历史每敕令平均成本(¥)与样本量——六科封驳(迭代 7)的成本预估基线。

        v1 取全体历史敕令的均值(按 goal 相似度加权检索为后续增量);无历史返回 (0.0, 0)。
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT AVG(ec) AS avg_cost, COUNT(*) AS n FROM (
                       SELECT SUM(cost_cny) AS ec FROM cost_ledger
                       WHERE edict_id IS NOT NULL GROUP BY edict_id
                   )"""
            ).fetchone()
        if not row or row["n"] == 0:
            return 0.0, 0
        return float(row["avg_cost"] or 0.0), int(row["n"])

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
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM cost_budgets WHERE scope = ?", (scope,)
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            now = datetime.now(UTC)
            reset = self._rolled_period_start(
                data.get("period"),
                data.get("period_start"),
                data.get("reset_at"),
                now=now,
            )
            if reset is not None:
                reset_at = (
                    None if self._is_reset_due(data.get("reset_at"), now) else data.get("reset_at")
                )
                self._conn.execute(
                    """UPDATE cost_budgets
                       SET spent_cny = 0, period_start = ?, reset_at = ?
                       WHERE scope = ?""",
                    (reset, reset_at, scope),
                )
                data["spent_cny"] = 0.0
                data["period_start"] = reset
                data["reset_at"] = reset_at
        return data

    @classmethod
    def _rolled_period_start(
        cls,
        period: str | None,
        period_start: str | None,
        reset_at: str | None = None,
        *,
        now: datetime | None = None,
    ) -> str | None:
        """返回需要写回的新周期起点;None = 无需滚动。"""
        now = now or datetime.now(UTC)
        cur_start = cls._current_period_start(period, now)
        if cls._is_reset_due(reset_at, now):
            return (cur_start or now).isoformat()
        if cur_start is None:
            return None
        cur_iso = cur_start.isoformat()
        if not period_start:
            return cur_iso  # 首次记账:落当期起点
        prev = cls._parse_budget_timestamp(period_start)
        if prev is None:
            return cur_iso
        return cur_iso if prev < cur_start else None

    @staticmethod
    def _current_period_start(period: str | None, now: datetime) -> datetime | None:
        if period not in ("daily", "weekly", "monthly"):
            return None
        start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "weekly":
            return start - timedelta(days=start.weekday())
        if period == "monthly":
            return start.replace(day=1)
        return start

    @staticmethod
    def _parse_budget_timestamp(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @classmethod
    def _is_reset_due(cls, reset_at: str | None, now: datetime) -> bool:
        reset = cls._parse_budget_timestamp(reset_at)
        return reset is not None and reset <= now

    def upsert_budget(
        self,
        scope: str,
        budget_cny: float,
        period: str = "monthly",
        *,
        reset_at: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        period_start = self._current_period_start(period, now)
        period_start_iso = (period_start or now).isoformat()
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT id FROM cost_budgets WHERE scope = ?", (scope,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    """UPDATE cost_budgets
                       SET budget_cny = ?, period = ?, reset_at = ?
                       WHERE scope = ?""",
                    (budget_cny, period, reset_at, scope),
                )
            else:
                self._conn.execute(
                    """INSERT INTO cost_budgets
                       (id, scope, budget_cny, period, reset_at, created_at, period_start)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(ULID()),
                        scope,
                        budget_cny,
                        period,
                        reset_at,
                        now_iso,
                        period_start_iso,
                    ),
                )

    def update_budget_spent(self, scope: str, amount_cny: float) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM cost_budgets WHERE scope = ?", (scope,)
            ).fetchone()
            if row is None:
                return
            data = dict(row)
            now = datetime.now(UTC)
            reset = self._rolled_period_start(
                data.get("period"),
                data.get("period_start"),
                data.get("reset_at"),
                now=now,
            )
            if reset is None:
                self._conn.execute(
                    "UPDATE cost_budgets SET spent_cny = spent_cny + ? WHERE scope = ?",
                    (amount_cny, scope),
                )
                return
            reset_at = (
                None if self._is_reset_due(data.get("reset_at"), now) else data.get("reset_at")
            )
            self._conn.execute(
                """UPDATE cost_budgets
                   SET spent_cny = ?, period_start = ?, reset_at = ?
                   WHERE scope = ?""",
                (amount_cny, reset, reset_at, scope),
            )
