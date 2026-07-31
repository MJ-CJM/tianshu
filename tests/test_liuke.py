"""六科给事中·封驳(迭代 7「制度补全」)——提交预检:超长封还 / 成本超阈升 plan_review 票拟。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from tianshu.executor.liuke import Liuke


def _cost_record(edict_id: str, cost: float):
    return SimpleNamespace(
        id=f"c-{edict_id}-{cost}",
        edict_id=edict_id,
        memorial_id=None,
        provider_name="p",
        model="m",
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        cache_read_tokens=0,
        cost_cny=cost,
        created_at=datetime.now(UTC),
    )


def _liuke(storage, config_manager, **overrides):
    config_manager.update_agent_config(
        liuke_enabled=True, liuke_max_goal_chars=100, liuke_cost_threshold_cny=5.0, **overrides
    )
    return Liuke(storage, config_manager)


class TestPrecheck:
    def test_disabled_always_passes(self, storage, config_manager):
        config_manager.update_agent_config(liuke_enabled=False, liuke_max_goal_chars=10)
        result = Liuke(storage, config_manager).precheck("x" * 999)
        assert result.verdict == "pass"

    def test_oversized_goal_rejected(self, storage, config_manager):
        result = _liuke(storage, config_manager).precheck("x" * 101)
        assert result.verdict == "reject" and "封还" in result.reason

    def test_declared_budget_over_threshold_escalates(self, storage, config_manager):
        result = _liuke(storage, config_manager).precheck("正常目标", declared_budget_cny=12.0)
        assert result.verdict == "plan_review" and result.estimated_cost_cny == 12.0

    def test_normal_passes(self, storage, config_manager):
        result = _liuke(storage, config_manager).precheck("小任务", declared_budget_cny=1.0)
        assert result.verdict == "pass"

    def test_history_estimate_escalates(self, storage, config_manager):
        # 单敕令历史成本 ¥10 > 阈值 ¥5 → 升 plan_review
        storage.save_cost_record(_cost_record("e-hist", 6.0))
        storage.save_cost_record(_cost_record("e-hist", 4.0))
        result = _liuke(storage, config_manager).precheck("像历史那样的任务")
        assert result.verdict == "plan_review"
        assert result.estimated_cost_cny == 10.0
