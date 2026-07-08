"""出厂预算护栏的周期滚动(迭代 3「深防御」)——daily/weekly 跨期自动清零。"""

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

    def test_monthly_budget_does_not_roll(self, storage):
        storage.upsert_budget("global", 100.0, period="monthly")
        storage.update_budget_spent("global", 30.0)
        old = (datetime.now(UTC) - timedelta(days=40)).isoformat()
        storage._conn.execute(
            "UPDATE cost_budgets SET period_start = ? WHERE scope = 'global'", (old,)
        )
        storage._conn.commit()
        # monthly 沿用既有月度语义,不在此滚动
        assert storage.get_budget("global")["spent_cny"] == 30.0

    def test_same_period_no_reset(self, storage):
        storage.upsert_budget("global", 20.0, period="daily")
        storage.update_budget_spent("global", 5.0)
        # 当期内多次读取不清零
        assert storage.get_budget("global")["spent_cny"] == 5.0
        assert storage.get_budget("global")["spent_cny"] == 5.0
