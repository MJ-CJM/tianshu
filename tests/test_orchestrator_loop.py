"""orchestrator outer loop 集成测试 —— mock actor / critic / approvals。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.orchestrator import OrchestratorContext, run
from tianshu.executor.orchestrator.human_decision import HumanDecision
from tianshu.models.acceptance import (
    AcceptanceCriteria,
    CheckSpec,
    CriticSpec,
    EscalationSpec,
)
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    if hasattr(s, "init_db"):
        s.init_db()
    return s


@pytest.fixture
def bus():
    b = MagicMock(spec=EventBus)
    b.emit = AsyncMock()
    return b


_AUDIT_PASS_JSON = json.dumps({"passed": True, "gaps": []})


def _make_ctx(storage, bus, agent, critic_responses):
    """critic_responses = list[dict] 按调用次序返回 verdict/issue_class/feedback。

    critic pass 后 orchestrator 会调用同一 critic_llm 跑 completion audit；
    此处在每个 pass 响应之后自动插入一个合法的 audit-pass JSON，保证 mock 顺序正确。
    """
    actor_llm = MagicMock()
    critic_llm = MagicMock()
    side_effects = []
    for r in critic_responses:
        side_effects.append(MagicMock(content=json.dumps(r)))
        if r.get("verdict") == "pass":
            # audit 门：复用 critic_llm，需要额外一次返回合法 audit JSON
            side_effects.append(MagicMock(content=_AUDIT_PASS_JSON))
    critic_llm.chat = AsyncMock(side_effect=side_effects)
    return OrchestratorContext(
        agent=agent, storage=storage, bus=bus,
        actor_llm=actor_llm, critic_llm=critic_llm,
    )


def _agent(output_per_iter: list[str]):
    a = MagicMock()
    results = [
        MagicMock(
            result=o, summary=o,
            usage=MagicMock(cost_cny=0.1),
        ) for o in output_per_iter
    ]
    a.execute = AsyncMock(side_effect=results)
    return a


def _edict(**kwargs) -> Edict:
    base = AcceptanceCriteria(
        max_outer_iterations=5,
        critic=CriticSpec(same_issue_threshold=2),
    )
    base = base.model_copy(update=kwargs)
    return Edict(goal="g", acceptance=base)


def _memorial(edict_id):
    return Memorial(edict_id=edict_id, instruction="g")


@pytest.mark.integration
async def test_pass_first_try(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["draft v1"]), [
        {"verdict": "pass", "feedback": "ok"},
    ])
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "draft v1"
    assert r.state.iteration == 1


@pytest.mark.integration
async def test_l0_to_l1_on_same_issue(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "wrong fact"},
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "still wrong"},
        {"verdict": "pass", "feedback": "ok now"},
    ])
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    # 第二轮后升 L1（streak=2 == threshold），第三轮在 L1 通过
    assert any(rec.level == "L1" for rec in r.state.history)


@pytest.mark.integration
async def test_streak_resets_on_different_issue(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "f1"},
        {"verdict": "fail", "issue_class": "tone_mismatch", "feedback": "f2"},
        {"verdict": "pass", "feedback": "ok"},
    ])
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    # 不同 issue_class → streak 重置，未升 L1
    assert all(rec.level == "L0" for rec in r.state.history)


@pytest.mark.integration
async def test_exhausted_best_effort(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
    ])
    e = _edict(max_outer_iterations=3, on_exhaustion="best_effort",
               escalation=EscalationSpec(enabled_levels=[]))  # 关闭升级
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED  # best_effort 视为成功
    assert r.final_output == "v3"


@pytest.mark.integration
async def test_checks_failed_skips_critic(storage, bus):
    # checks 不过 → critic 不调用
    ctx = _make_ctx(storage, bus, _agent(["v1"]), [])  # critic 0 calls
    e = _edict(checks=[CheckSpec(kind="bash", name="must_fail", command="exit 1")],
               max_outer_iterations=1, on_exhaustion="fail")
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.FAILED
    assert ctx.critic_llm.chat.call_count == 0


@pytest.mark.integration
async def test_failed_keeps_last_actor_output_for_postmortem(storage, bus):
    """on_exhaustion=fail 时 final_output 应保留最后一轮 actor_output 供尸检。"""
    ctx = _make_ctx(storage, bus, _agent(["draft v1", "draft v2", "draft v3"]), [
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
    ])
    e = _edict(max_outer_iterations=3, on_exhaustion="fail",
               escalation=EscalationSpec(enabled_levels=[]))
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.FAILED
    # 即使 FAILED，也要把最后一轮 actor_output 留下来（C 修复）
    assert r.final_output == "draft v3"
    assert r.error == "outer loop exhausted"


@pytest.mark.integration
async def test_critic_unavailable_skip(storage, bus):
    """critic 不可用 + skip → audit 退化为 actor_llm 自审（spec §7）。"""
    actor = _agent(["v1"])
    actor_llm = MagicMock()
    critic_llm = MagicMock()
    # critic._review_single 会重试 max_retries=2 次，全部 RuntimeError → CriticUnavailable → skip → verdict=pass
    # audit 门因 critic_skipped=True，改用 actor_llm，所以 critic_llm 不再需要 audit 响应
    critic_llm.chat = AsyncMock(side_effect=[
        RuntimeError("critic down"),  # critic attempt 0
        RuntimeError("critic down"),  # critic attempt 1 (max_retries=2)
    ])
    # actor_llm 处理 audit 调用（critic-skip 退化路径）
    actor_llm.chat = AsyncMock(return_value=MagicMock(content=_AUDIT_PASS_JSON))
    ctx = OrchestratorContext(
        agent=actor, storage=storage, bus=bus,
        actor_llm=actor_llm, critic_llm=critic_llm,
    )
    e = _edict()  # on_critic_unavailable 默认 skip
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "v1"
    # 验证 audit 确实用了 actor_llm（非 critic_llm）
    assert actor_llm.chat.call_count >= 1, "audit 应通过 actor_llm 执行"
    assert critic_llm.chat.call_count == 2, "critic_llm 只调用 2 次（重试），不含 audit"


@pytest.mark.integration
async def test_l3_approval_accept_as_is(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
    ])
    approvals = MagicMock()
    approvals.wait_for_outer_loop_decision = AsyncMock(return_value=HumanDecision(action="accept_as_is"))
    ctx.approvals = approvals

    e = _edict(
        max_outer_iterations=3,
        critic=CriticSpec(same_issue_threshold=1),
        escalation=EscalationSpec(l1_max_rounds=1, l2_max_rounds=1),
        on_exhaustion="escalate",
    )
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    assert "v" in (r.final_output or "")


@pytest.mark.integration
async def test_l3_approval_abort(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
    ])
    approvals = MagicMock()
    approvals.wait_for_outer_loop_decision = AsyncMock(return_value=HumanDecision(action="abort"))
    ctx.approvals = approvals

    e = _edict(
        max_outer_iterations=3,
        critic=CriticSpec(same_issue_threshold=1),
        escalation=EscalationSpec(l1_max_rounds=1, l2_max_rounds=1),
        on_exhaustion="escalate",
    )
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.FAILED


@pytest.mark.integration
async def test_l3_modify_acceptance_resets_streak(storage, bus):
    """L3 用户调宽标准 → 回 L0，streak 清零，下一轮按新标准跑。"""
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3", "v4"]), [
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
        {"verdict": "pass", "feedback": "ok with looser criteria"},
    ])
    new_acceptance = AcceptanceCriteria(
        max_outer_iterations=10,
        critic=CriticSpec(same_issue_threshold=5),
    )
    approvals = MagicMock()
    approvals.wait_for_outer_loop_decision = AsyncMock(return_value=HumanDecision(
        action="modify_acceptance",
        new_acceptance=new_acceptance,
    ))
    ctx.approvals = approvals

    e = _edict(
        max_outer_iterations=4,
        critic=CriticSpec(same_issue_threshold=1),
        escalation=EscalationSpec(l1_max_rounds=1, l2_max_rounds=1),
        on_exhaustion="escalate",
    )
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    # modify_acceptance 后回 L0 重跑，最终通过
    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "v4"


# ---- pause 等待循环：paused → resume 续跑、paused → 终结干净退出 ----

@pytest.mark.integration
async def test_pause_then_resume_continues_loop(storage, bus, monkeypatch):
    """paused 状态下 outer loop 应等待，resume 后续跑并最终 COMPLETED。"""
    import asyncio

    monkeypatch.setattr(
        "tianshu.executor.orchestrator.loop.PAUSE_POLL_INTERVAL_SECONDS",
        0.01,
    )

    ctx = _make_ctx(storage, bus, _agent(["draft v1"]), [
        {"verdict": "pass", "feedback": "ok"},
    ])
    e = _edict()
    storage.save_edict(e)
    # 创建后立即切到 paused（模拟用户已点暂停）
    storage.update_edict_lifecycle_phase(e.id, "paused")

    # 后台 task 在 100ms 后把 lifecycle 切回 active（模拟用户恢复）
    async def _delayed_resume():
        await asyncio.sleep(0.1)
        storage.update_edict_lifecycle_phase(e.id, "active")

    resume_task = asyncio.create_task(_delayed_resume())
    r = await run(e, _memorial(e.id), ctx)
    await resume_task

    assert r.status == TaskStatus.COMPLETED, f"应正常完成，得到 {r.status}"
    assert r.final_output == "draft v1"
    assert r.state.iteration == 1


@pytest.mark.integration
async def test_pause_then_complete_externally_returns_cancelled(storage, bus, monkeypatch):
    """paused 状态下 lifecycle 被外部切到 complete（如 cancel），应干净 CANCELLED 退出。"""
    import asyncio

    monkeypatch.setattr(
        "tianshu.executor.orchestrator.loop.PAUSE_POLL_INTERVAL_SECONDS",
        0.01,
    )

    ctx = _make_ctx(storage, bus, _agent(["never run"]), [])
    e = _edict()
    storage.save_edict(e)
    storage.update_edict_lifecycle_phase(e.id, "paused")

    async def _delayed_complete():
        await asyncio.sleep(0.1)
        storage.update_edict_lifecycle_phase(e.id, "complete")

    end_task = asyncio.create_task(_delayed_complete())
    r = await run(e, _memorial(e.id), ctx)
    await end_task

    assert r.status == TaskStatus.CANCELLED
    # actor 没被调用过（agent.execute side_effect 未消费）
    assert ctx.agent.execute.call_count == 0


@pytest.mark.integration
async def test_pause_emits_paused_event_only_once_on_entry(storage, bus, monkeypatch):
    """outer_loop.paused 事件应只在入场发一次，等待循环里不应重复发。"""
    import asyncio

    monkeypatch.setattr(
        "tianshu.executor.orchestrator.loop.PAUSE_POLL_INTERVAL_SECONDS",
        0.01,
    )

    ctx = _make_ctx(storage, bus, _agent(["v1"]), [
        {"verdict": "pass", "feedback": "ok"},
    ])
    e = _edict()
    storage.save_edict(e)
    storage.update_edict_lifecycle_phase(e.id, "paused")

    async def _delayed_resume():
        # 多 sleep 几次保证经过多轮 PAUSE_POLL_INTERVAL_SECONDS
        await asyncio.sleep(0.15)
        storage.update_edict_lifecycle_phase(e.id, "active")

    resume_task = asyncio.create_task(_delayed_resume())
    await run(e, _memorial(e.id), ctx)
    await resume_task

    # 数 storage 里的 outer_loop.paused 事件
    paused_events = [
        ev for ev in storage.get_events(e.id)
        if ev["event_type"] == "outer_loop.paused"
    ]
    assert len(paused_events) == 1, f"应只发一次入场事件，实际 {len(paused_events)} 次"
    # 续跑后应有恢复事件
    resumed_events = [
        ev for ev in storage.get_events(e.id)
        if ev["event_type"] == "outer_loop.resumed"
    ]
    assert len(resumed_events) == 1
