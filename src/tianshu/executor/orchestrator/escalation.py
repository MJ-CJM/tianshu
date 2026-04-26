"""Escalation FSM — 纯函数，决定下一 level。"""

from __future__ import annotations

from typing import Literal

from tianshu.executor.orchestrator.state import Level, OuterLoopState
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.edict import Edict

Decision = Literal["L0", "L1", "L2", "L3", "EXHAUSTED"]


def decide_escalation(
    state: OuterLoopState,
    edict: Edict,
    acceptance: AcceptanceCriteria,
    *,
    last_critic_passed: bool,
) -> Decision:
    """决定下一步：留级 / 升级 / 耗尽。

    last_critic_passed=True 时调用方应直接收工，不应调本函数；
    保留参数仅作 sanity check（True 时返回当前 level）。
    """
    if last_critic_passed:
        return state.current_level  # type: ignore[return-value]

    # 1. iteration / cost / deadline 硬上限
    if state.iteration >= acceptance.max_outer_iterations:
        return "EXHAUSTED"
    budget = edict.runtime.cost_budget_cny
    if budget is not None and state.total_cost_cny >= budget:
        return "EXHAUSTED"

    enabled = acceptance.escalation.enabled_levels
    threshold = acceptance.critic.same_issue_threshold

    if state.current_level == "L0":
        if state.same_issue_streak >= threshold:
            if "L1" in enabled:
                return "L1"
            if "L2" in enabled:
                return "L2"
            if "L3" in enabled:
                return "L3"
        return "L0"

    if state.current_level == "L1":
        if state.l1_rounds_used >= acceptance.escalation.l1_max_rounds:
            if "L2" in enabled:
                return "L2"
            if "L3" in enabled:
                return "L3"
            return "EXHAUSTED"
        return "L1"

    if state.current_level == "L2":
        if state.l2_rounds_used >= acceptance.escalation.l2_max_rounds:
            if "L3" in enabled:
                return "L3"
            return "EXHAUSTED"
        return "L2"

    # L3 不再升级，只能等审批
    return "L3"
