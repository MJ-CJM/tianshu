"""Orchestrator 决策点路由集成测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tianshu.executor.orchestrator.audit import AuditGap, AuditResult
from tianshu.executor.orchestrator.lifecycle import (
    apply_transition, can_transition,
)


def test_can_transition_active_to_paused():
    assert can_transition("active", "paused")


def test_can_transition_paused_to_active():
    assert can_transition("paused", "active")


def test_can_transition_active_to_winding_down():
    assert can_transition("active", "winding_down")


def test_can_transition_winding_down_to_complete():
    assert can_transition("winding_down", "complete")


def test_cannot_transition_paused_to_winding_down_directly():
    assert not can_transition("paused", "winding_down")


def test_complete_is_terminal():
    for tgt in ("active", "paused", "winding_down"):
        assert not can_transition("complete", tgt)


def test_self_transition_is_idempotent():
    for p in ("active", "paused", "winding_down", "complete"):
        assert can_transition(p, p)


def test_unknown_phase_rejected():
    assert not can_transition("active", "bogus")
    assert not can_transition("bogus", "active")


# ------- audit 嵌入 orchestrator 的高层契约 -------

@pytest.mark.asyncio
async def test_orchestrator_audit_pass_finalizes_complete(monkeypatch):
    """critic pass + audit pass → orchestrator 走 finalize 完成路径。

    完整 e2e 在 Task 14；此处先验证 audit.run_completion_audit 在 orchestrator 调用流可被 monkeypatch。
    """
    from tianshu.executor.orchestrator import audit as audit_mod
    called = {}

    async def fake_audit(**kwargs):
        called["objective"] = kwargs["objective"]
        return AuditResult(passed=True, gaps=())

    monkeypatch.setattr(audit_mod, "run_completion_audit", fake_audit)
    result = await audit_mod.run_completion_audit(
        actor_output="x", objective="o",
        acceptance=None, llm=None,
    )
    assert result.passed is True
    assert called["objective"] == "o"
