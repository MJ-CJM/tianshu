"""usage_ratio 计算测试。"""
from __future__ import annotations

import pytest

from tianshu.executor.orchestrator.budget import (
    BudgetSnapshot,
    compute_usage_ratio,
    HARD_LIMIT,
    SOFT_LANDING_THRESHOLD,
)


def test_returns_zero_when_no_budgets_set():
    snap = BudgetSnapshot(
        tokens_used=1000, token_budget=None,
        cost_used_cny=0.5, cost_budget_cny=None,
        time_used_seconds=10, deadline_seconds=None,
    )
    assert compute_usage_ratio(snap) == 0.0


def test_uses_token_budget_when_only_tokens_set():
    snap = BudgetSnapshot(
        tokens_used=900, token_budget=1000,
        cost_used_cny=0, cost_budget_cny=None,
        time_used_seconds=0, deadline_seconds=None,
    )
    assert compute_usage_ratio(snap) == pytest.approx(0.9)


def test_takes_max_across_all_set_dimensions():
    snap = BudgetSnapshot(
        tokens_used=500, token_budget=1000,    # 0.5
        cost_used_cny=0.95, cost_budget_cny=1.0,  # 0.95
        time_used_seconds=10, deadline_seconds=100,  # 0.1
    )
    assert compute_usage_ratio(snap) == pytest.approx(0.95)


def test_can_exceed_one_when_over_budget():
    snap = BudgetSnapshot(
        tokens_used=1500, token_budget=1000,
        cost_used_cny=0, cost_budget_cny=None,
        time_used_seconds=0, deadline_seconds=None,
    )
    assert compute_usage_ratio(snap) == pytest.approx(1.5)


def test_zero_budget_treated_as_unset():
    """token_budget=0 视为未设，避免除零。"""
    snap = BudgetSnapshot(
        tokens_used=10, token_budget=0,
        cost_used_cny=0, cost_budget_cny=None,
        time_used_seconds=0, deadline_seconds=None,
    )
    assert compute_usage_ratio(snap) == 0.0


def test_soft_landing_threshold_is_zero_point_nine():
    assert SOFT_LANDING_THRESHOLD == pytest.approx(0.9)


def test_hard_limit_is_one_point_zero():
    assert HARD_LIMIT == 1.0


def test_negative_used_returns_zero_not_negative():
    """负 used 视为 0（保护性，避免 tracker bug 让 ratio 变负绕过阈值）。"""
    snap = BudgetSnapshot(
        tokens_used=-100, token_budget=1000,
        cost_used_cny=0, cost_budget_cny=None,
        time_used_seconds=0, deadline_seconds=None,
    )
    assert compute_usage_ratio(snap) == 0.0
