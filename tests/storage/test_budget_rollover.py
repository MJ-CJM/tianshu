"""出厂预算护栏按 UTC 周期或显式 reset_at 滚动。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class TestBudgetRollover:
    def test_daily_budget_resets_across_period(self, storage):
        storage.upsert_budget("global", 20.0, period="daily")
        storage.update_budget_spent("global", 15.0)
        assert storage.get_budget("global")["spent_cny"] == 15.0

        # 手工把 period_start 拨到昨天 → 下次读应清零
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        storage._conn.execute(
            "UPDATE cost_budgets SET period_start = ? WHERE scope = 'global'", (yesterday,)
        )
        storage._conn.commit()
        rolled = storage.get_budget("global")
        assert rolled["spent_cny"] == 0.0
        assert rolled["budget_cny"] == 20.0  # 预算额度不变

    def test_monthly_budget_resets_across_period(self, storage):
        storage.upsert_budget("global", 100.0, period="monthly")
        storage.update_budget_spent("global", 30.0)
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        storage._conn.execute(
            "UPDATE cost_budgets SET period_start = ? WHERE scope = 'global'", (old,)
        )
        storage._conn.commit()
        assert storage.get_budget("global")["spent_cny"] == 0.0

    def test_weekly_budget_resets_across_utc_week(self, storage):
        storage.upsert_budget("global", 100.0, period="weekly")
        storage.update_budget_spent("global", 30.0)
        previous_week = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        storage._conn.execute(
            "UPDATE cost_budgets SET period_start = ? WHERE scope = 'global'",
            (previous_week,),
        )
        storage._conn.commit()

        assert storage.get_budget("global")["spent_cny"] == 0.0

    def test_expired_reset_at_resets_and_is_consumed(self, storage):
        storage.upsert_budget("global", 100.0, period="monthly")
        storage.update_budget_spent("global", 30.0)
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        storage._conn.execute(
            "UPDATE cost_budgets SET reset_at = ? WHERE scope = 'global'", (expired,)
        )
        storage._conn.commit()

        rolled = storage.get_budget("global")

        assert rolled["spent_cny"] == 0.0
        assert rolled["reset_at"] is None

    def test_first_spend_after_period_boundary_is_not_lost(self, storage):
        storage.upsert_budget("global", 20.0, period="daily")
        storage.update_budget_spent("global", 15.0)
        yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        storage._conn.execute(
            "UPDATE cost_budgets SET period_start = ? WHERE scope = 'global'", (yesterday,)
        )
        storage._conn.commit()

        storage.update_budget_spent("global", 5.0)

        assert storage.get_budget("global")["spent_cny"] == 5.0

    def test_first_spend_after_reset_at_is_not_lost(self, storage):
        storage.upsert_budget("global", 20.0, period="monthly")
        storage.update_budget_spent("global", 15.0)
        expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        storage._conn.execute(
            "UPDATE cost_budgets SET reset_at = ? WHERE scope = 'global'", (expired,)
        )
        storage._conn.commit()

        storage.update_budget_spent("global", 5.0)

        rolled = storage.get_budget("global")
        assert rolled["spent_cny"] == 5.0
        assert rolled["reset_at"] is None

    def test_same_period_no_reset(self, storage):
        storage.upsert_budget("global", 20.0, period="daily")
        storage.update_budget_spent("global", 5.0)
        # 当期内多次读取不清零
        assert storage.get_budget("global")["spent_cny"] == 5.0
        assert storage.get_budget("global")["spent_cny"] == 5.0
