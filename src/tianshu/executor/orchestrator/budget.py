"""预算用量比 (usage_ratio) 计算。

任一字段缺省（None / 0 / 负）则该维度不计入 max。全部缺省返回 0.0。
"""
from __future__ import annotations

from dataclasses import dataclass

SOFT_LANDING_THRESHOLD: float = 0.9
HARD_LIMIT: float = 1.0


@dataclass(frozen=True)
class BudgetSnapshot:
    tokens_used: int
    token_budget: int | None
    cost_used_cny: float
    cost_budget_cny: float | None
    time_used_seconds: int
    deadline_seconds: int | None


def _ratio(used: float, budget: float | int | None) -> float | None:
    if budget is None or budget <= 0:
        return None
    return float(used) / float(budget)


def compute_usage_ratio(snap: BudgetSnapshot) -> float:
    """跨 token / cost / time 三维取最大；全部缺省返回 0.0。"""
    candidates = [
        _ratio(snap.tokens_used, snap.token_budget),
        _ratio(snap.cost_used_cny, snap.cost_budget_cny),
        _ratio(snap.time_used_seconds, snap.deadline_seconds),
    ]
    set_values = [r for r in candidates if r is not None]
    if not set_values:
        return 0.0
    return max(set_values)
