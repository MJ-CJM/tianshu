"""decide_escalation 参数化测试 — 覆盖所有升级分支。"""

from __future__ import annotations

import pytest

from tianshu.executor.orchestrator.escalation import decide_escalation
from tianshu.executor.orchestrator.state import OuterLoopState
from tianshu.models.acceptance import (
    AcceptanceCriteria,
    CriticSpec,
    EscalationSpec,
)
from tianshu.models.edict import Edict, EdictRuntime


def _state(**kwargs) -> OuterLoopState:
    defaults = {"edict_id": "e1"}
    defaults.update(kwargs)
    return OuterLoopState(**defaults)


def _accept(**kwargs) -> AcceptanceCriteria:
    return AcceptanceCriteria(**kwargs)


def _edict(cost_budget: float | None = None) -> Edict:
    return Edict(goal="test", runtime=EdictRuntime(cost_budget_cny=cost_budget))


@pytest.mark.unit
@pytest.mark.parametrize(
    "state, accept, edict, expected",
    [
        # L0 同类未达阈值 → 留 L0
        (
            _state(current_level="L0", same_issue_streak=1),
            _accept(critic=CriticSpec(same_issue_threshold=2)),
            _edict(),
            "L0",
        ),
        # L0 同类达阈值 → L1
        (
            _state(current_level="L0", same_issue_streak=2),
            _accept(critic=CriticSpec(same_issue_threshold=2)),
            _edict(),
            "L1",
        ),
        # L0 同类达阈值 + L1 关闭 → 跳 L2
        (
            _state(current_level="L0", same_issue_streak=2),
            _accept(
                critic=CriticSpec(same_issue_threshold=2),
                escalation=EscalationSpec(enabled_levels=["L2", "L3"]),
            ),
            _edict(),
            "L2",
        ),
        # L1 重试用尽 → L2
        (
            _state(current_level="L1", l1_rounds_used=2),
            _accept(escalation=EscalationSpec(l1_max_rounds=2)),
            _edict(),
            "L2",
        ),
        # L2 重试用尽 → L3
        (
            _state(current_level="L2", l2_rounds_used=1),
            _accept(escalation=EscalationSpec(l2_max_rounds=1)),
            _edict(),
            "L3",
        ),
        # L3 留级（等审批）
        (_state(current_level="L3"), _accept(), _edict(), "L3"),
        # iteration 超限 → EXHAUSTED
        (_state(iteration=5), _accept(max_outer_iterations=5), _edict(), "EXHAUSTED"),
        # 预算超限 → EXHAUSTED
        (_state(total_cost_cny=10.0), _accept(), _edict(cost_budget=5.0), "EXHAUSTED"),
    ],
)
def test_decide_escalation(state, accept, edict, expected):
    assert (
        decide_escalation(
            state,
            edict,
            accept,
            last_critic_passed=False,
        )
        == expected
    )


@pytest.mark.unit
def test_decide_escalation_passed_returns_current_level():
    state = _state(current_level="L1")
    assert (
        decide_escalation(
            state,
            _edict(),
            _accept(),
            last_critic_passed=True,
        )
        == "L1"
    )
