"""OuterLoopState advance / with_level 测试。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tianshu.executor.orchestrator.state import (
    ChecksResult,
    CriticResult,
    IterationRecord,
    OuterLoopState,
)


def _record(level="L0", issue_class="factual_error", verdict="fail", cost=0.1) -> IterationRecord:
    return IterationRecord(
        iteration=0,
        level=level,
        actor_output="x",
        checks_result=ChecksResult(all_passed=True),
        critic_result=CriticResult(verdict=verdict, issue_class=issue_class, feedback="f"),
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        cost_cny=cost,
    )


@pytest.mark.unit
def test_advance_streak_increments_on_same_issue():
    s = OuterLoopState(edict_id="e1", last_critic_issue_class="factual_error", same_issue_streak=1)
    s2 = s.advance(_record(issue_class="factual_error"))
    assert s2.same_issue_streak == 2


@pytest.mark.unit
def test_advance_streak_resets_on_different_issue():
    s = OuterLoopState(edict_id="e1", last_critic_issue_class="factual_error", same_issue_streak=2)
    s2 = s.advance(_record(issue_class="tone_mismatch"))
    assert s2.same_issue_streak == 1
    assert s2.last_critic_issue_class == "tone_mismatch"


@pytest.mark.unit
def test_advance_accumulates_cost():
    s = OuterLoopState(edict_id="e1", total_cost_cny=0.5)
    s2 = s.advance(_record(cost=0.3))
    assert abs(s2.total_cost_cny - 0.8) < 1e-9


@pytest.mark.unit
def test_advance_increments_level_rounds():
    s = OuterLoopState(edict_id="e1", current_level="L1")
    s2 = s.advance(_record(level="L1"))
    assert s2.l1_rounds_used == 1
    s3 = s2.advance(_record(level="L2"))
    assert s3.l2_rounds_used == 1


@pytest.mark.unit
def test_with_level_immutable():
    s = OuterLoopState(edict_id="e1", current_level="L0")
    s2 = s.with_level("L1")
    assert s.current_level == "L0"
    assert s2.current_level == "L1"


@pytest.mark.unit
def test_with_consultation_advice():
    s = OuterLoopState(edict_id="e1")
    s2 = s.with_consultation_advice("be more careful")
    assert s.consultation_advice is None
    assert s2.consultation_advice == "be more careful"
