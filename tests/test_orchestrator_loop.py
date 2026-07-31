"""orchestrator outer loop 集成测试 —— mock actor / critic / approvals。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.cost.manager import CostManager
from tianshu.executor.orchestrator import OrchestratorContext, run
from tianshu.executor.orchestrator.human_decision import HumanDecision
from tianshu.llm import LLMUsageContext
from tianshu.models.acceptance import (
    AcceptanceCriteria,
    CheckSpec,
    CriticSpec,
    EscalationSpec,
)
from tianshu.models.common import TaskStatus, UsageSummary
from tianshu.models.edict import Edict, EdictRuntime
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    if hasattr(s, "init_db"):
        s.init_db()
    yield s
    s.close()


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
        agent=agent,
        storage=storage,
        bus=bus,
        actor_llm=actor_llm,
        critic_llm=critic_llm,
    )


def _agent(output_per_iter: list[str]):
    a = MagicMock()
    results = [
        MagicMock(
            result=o,
            summary=o,
            usage=MagicMock(cost_cny=0.1),
        )
        for o in output_per_iter
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
    ctx = _make_ctx(
        storage,
        bus,
        _agent(["draft v1"]),
        [
            {"verdict": "pass", "feedback": "ok"},
        ],
    )
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "draft v1"
    assert r.state.iteration == 1


@pytest.mark.integration
@pytest.mark.parametrize("status", [TaskStatus.FAILED, TaskStatus.CANCELLED])
async def test_actor_terminal_status_cannot_be_washed_by_critic(storage, bus, status):
    agent = MagicMock()
    agent.execute = AsyncMock(
        return_value=MagicMock(
            status=status,
            result="output that might otherwise pass review",
            summary=None,
            error=f"actor {status.value}",
            usage=UsageSummary(cost_cny=0.1),
        )
    )
    ctx = _make_ctx(storage, bus, agent, [])
    e = _edict()
    storage.save_edict(e)

    result = await run(e, _memorial(e.id), ctx)

    assert result.status is status
    assert result.error == f"actor {status.value}"
    assert result.final_output == "output that might otherwise pass review"
    assert result.state.history == ()
    assert ctx.critic_llm.chat.call_count == 0


@pytest.mark.integration
async def test_rubric_check_completion_is_persisted_as_evidence_event(storage, bus):
    actor = _agent(["candidate output"])
    actor_llm = MagicMock()
    actor_llm.chat = AsyncMock(
        return_value=MagicMock(content=json.dumps({"score": 1.0, "reasoning": "passed"}))
    )
    critic_llm = MagicMock()
    critic_llm.chat = AsyncMock(
        side_effect=[
            MagicMock(content=json.dumps({"verdict": "pass", "feedback": "ok"})),
            MagicMock(content=_AUDIT_PASS_JSON),
        ]
    )
    ctx = OrchestratorContext(
        agent=actor,
        storage=storage,
        bus=bus,
        actor_llm=actor_llm,
        critic_llm=critic_llm,
    )
    e = _edict(
        checks=[CheckSpec(kind="rubric", name="candidate.gate", rubric="Pass the candidate")]
    )
    memorial = _memorial(e.id)
    storage.save_edict(e)

    result = await run(e, memorial, ctx)

    assert result.status == TaskStatus.COMPLETED
    rubric_context = actor_llm.chat.await_args.kwargs["usage_context"]
    assert (rubric_context.edict_id, rubric_context.memorial_id, rubric_context.operation) == (
        e.id,
        memorial.id,
        "acceptance_rubric",
    )
    critic_contexts = [call.kwargs["usage_context"] for call in critic_llm.chat.await_args_list]
    assert [context.operation for context in critic_contexts] == [
        "critic_review",
        "completion_audit",
    ]
    assert all(context.memorial_id == memorial.id for context in critic_contexts)
    evidence_events = [
        event
        for event in storage.get_events(e.id)
        if event["event_type"] == "acceptance.check.completed"
    ]
    assert len(evidence_events) == 1
    assert evidence_events[0]["memorial_id"] == memorial.id
    assert evidence_events[0]["payload"] == {
        "name": "candidate.gate",
        "status": "passed",
        "started_at": evidence_events[0]["payload"]["started_at"],
        "completed_at": evidence_events[0]["payload"]["completed_at"],
    }


@pytest.mark.integration
async def test_l0_to_l1_on_same_issue(storage, bus):
    ctx = _make_ctx(
        storage,
        bus,
        _agent(["v1", "v2", "v3"]),
        [
            {"verdict": "fail", "issue_class": "factual_error", "feedback": "wrong fact"},
            {"verdict": "fail", "issue_class": "factual_error", "feedback": "still wrong"},
            {"verdict": "pass", "feedback": "ok now"},
        ],
    )
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    # 第二轮后升 L1（streak=2 == threshold），第三轮在 L1 通过
    assert any(rec.level == "L1" for rec in r.state.history)


@pytest.mark.integration
async def test_streak_resets_on_different_issue(storage, bus):
    ctx = _make_ctx(
        storage,
        bus,
        _agent(["v1", "v2", "v3"]),
        [
            {"verdict": "fail", "issue_class": "factual_error", "feedback": "f1"},
            {"verdict": "fail", "issue_class": "tone_mismatch", "feedback": "f2"},
            {"verdict": "pass", "feedback": "ok"},
        ],
    )
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    # 不同 issue_class → streak 重置，未升 L1
    assert all(rec.level == "L0" for rec in r.state.history)


@pytest.mark.integration
async def test_exhausted_best_effort(storage, bus):
    ctx = _make_ctx(
        storage,
        bus,
        _agent(["v1", "v2", "v3"]),
        [
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        ],
    )
    e = _edict(
        max_outer_iterations=3,
        on_exhaustion="best_effort",
        escalation=EscalationSpec(enabled_levels=[]),
    )  # 关闭升级
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED  # best_effort 视为成功
    assert r.final_output == "v3"


@pytest.mark.integration
async def test_checks_failed_skips_critic(storage, bus):
    # checks 不过 → critic 不调用
    ctx = _make_ctx(storage, bus, _agent(["v1"]), [])  # critic 0 calls
    e = _edict(
        checks=[CheckSpec(kind="bash", name="must_fail", command="exit 1")],
        max_outer_iterations=1,
        on_exhaustion="fail",
    )
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.FAILED
    assert ctx.critic_llm.chat.call_count == 0


@pytest.mark.integration
async def test_failed_keeps_last_actor_output_for_postmortem(storage, bus):
    """on_exhaustion=fail 时 final_output 应保留最后一轮 actor_output 供尸检。"""
    ctx = _make_ctx(
        storage,
        bus,
        _agent(["draft v1", "draft v2", "draft v3"]),
        [
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        ],
    )
    e = _edict(
        max_outer_iterations=3, on_exhaustion="fail", escalation=EscalationSpec(enabled_levels=[])
    )
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
    critic_llm.chat = AsyncMock(
        side_effect=[
            RuntimeError("critic down"),  # critic attempt 0
            RuntimeError("critic down"),  # critic attempt 1 (max_retries=2)
        ]
    )
    # actor_llm 处理 audit 调用（critic-skip 退化路径）
    actor_llm.chat = AsyncMock(return_value=MagicMock(content=_AUDIT_PASS_JSON))
    ctx = OrchestratorContext(
        agent=actor,
        storage=storage,
        bus=bus,
        actor_llm=actor_llm,
        critic_llm=critic_llm,
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
    ctx = _make_ctx(
        storage,
        bus,
        _agent(["v1", "v2", "v3"]),
        [
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        ],
    )
    approvals = MagicMock()
    approvals.wait_for_outer_loop_decision = AsyncMock(
        return_value=HumanDecision(action="accept_as_is")
    )
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
    ctx = _make_ctx(
        storage,
        bus,
        _agent(["v1", "v2", "v3"]),
        [
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
            {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        ],
    )
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
    ctx = _make_ctx(
        storage,
        bus,
        _agent(["v1", "v2", "v3", "v4"]),
        [
            {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
            {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
            {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
            {"verdict": "pass", "feedback": "ok with looser criteria"},
        ],
    )
    new_acceptance = AcceptanceCriteria(
        max_outer_iterations=10,
        critic=CriticSpec(same_issue_threshold=5),
    )
    approvals = MagicMock()
    approvals.wait_for_outer_loop_decision = AsyncMock(
        return_value=HumanDecision(
            action="modify_acceptance",
            new_acceptance=new_acceptance,
        )
    )
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

    ctx = _make_ctx(
        storage,
        bus,
        _agent(["draft v1"]),
        [
            {"verdict": "pass", "feedback": "ok"},
        ],
    )
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
async def test_pause_then_edict_deleted_returns_cancelled(storage, bus, monkeypatch):
    """paused 状态下 edict 被删除（如用户误删），应干净 CANCELLED 退出，不崩溃。"""
    import asyncio

    monkeypatch.setattr(
        "tianshu.executor.orchestrator.loop.PAUSE_POLL_INTERVAL_SECONDS",
        0.01,
    )

    ctx = _make_ctx(storage, bus, _agent(["never run"]), [])
    e = _edict()
    storage.save_edict(e)
    storage.update_edict_lifecycle_phase(e.id, "paused")

    async def _delayed_delete():
        await asyncio.sleep(0.1)
        storage.delete_edict(e.id)

    delete_task = asyncio.create_task(_delayed_delete())
    r = await run(e, _memorial(e.id), ctx)
    await delete_task

    assert r.status == TaskStatus.CANCELLED
    assert r.error == "edict deleted while paused"
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

    ctx = _make_ctx(
        storage,
        bus,
        _agent(["v1"]),
        [
            {"verdict": "pass", "feedback": "ok"},
        ],
    )
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
        ev for ev in storage.get_events(e.id) if ev["event_type"] == "outer_loop.paused"
    ]
    assert len(paused_events) == 1, f"应只发一次入场事件，实际 {len(paused_events)} 次"
    # 续跑后应有恢复事件
    resumed_events = [
        ev for ev in storage.get_events(e.id) if ev["event_type"] == "outer_loop.resumed"
    ]
    assert len(resumed_events) == 1


# ---- B4-T1 行为锚点：拆分 run() 前补齐的主循环控制流场景 ----


@pytest.mark.integration
async def test_budget_hard_limit_forces_early_finalize(storage, bus):
    """已处于 winding_down 后仍触发硬上限 → 强制 finalize FAILED，不再跑 actor/critic（提前收工路径）。"""
    ctx = _make_ctx(storage, bus, _agent(["should not be called"]), [])  # critic 不应被调用
    e = _edict().model_copy(
        update={"runtime": EdictRuntime(token_budget=1000, lifecycle_phase="winding_down")}
    )
    storage.save_edict(e)
    m = _memorial(e.id).model_copy(update={"usage": UsageSummary(total_tokens=1000)})

    r = await run(e, m, ctx)

    assert r.status == TaskStatus.FAILED
    assert r.error is not None and r.error.startswith("budget_exhausted")
    assert ctx.agent.execute.call_count == 0, "预算耗尽应在 actor 调用前提前收工"
    assert ctx.critic_llm.chat.call_count == 0


@pytest.mark.integration
async def test_budget_hard_limit_includes_non_actor_llm_usage(storage, bus):
    """Rubric/audit/consultation 等旁路调用也必须进入下一轮预算熔断。"""
    cost_manager = CostManager(storage)
    ctx = _make_ctx(storage, bus, _agent(["should not be called"]), [])
    ctx.cost_manager = cost_manager
    e = _edict().model_copy(
        update={"runtime": EdictRuntime(token_budget=1000, lifecycle_phase="winding_down")}
    )
    storage.save_edict(e)
    m = _memorial(e.id)
    cost_manager.observe_llm_usage(
        UsageSummary(prompt_tokens=1000, total_tokens=1000),
        LLMUsageContext(
            edict_id=e.id,
            memorial_id=m.id,
            operation="completion_audit",
        ),
        "provider",
        "model",
    )

    r = await run(e, m, ctx)

    assert r.status == TaskStatus.FAILED
    assert r.error is not None and r.error.startswith("budget_exhausted")
    assert ctx.agent.execute.call_count == 0


@pytest.mark.integration
async def test_checks_failed_carries_feedback_to_next_iteration(storage, bus):
    """checks 未过 → 不进 critic，advance 后把 checks_failed 反馈带进下一轮 actor 调用。"""
    actor_llm = MagicMock()
    actor_llm.chat = AsyncMock(
        side_effect=[
            MagicMock(content=json.dumps({"score": 0.3, "reasoning": "meh"})),  # iter0 rubric 不过
            MagicMock(content=json.dumps({"score": 0.9, "reasoning": "good"})),  # iter1 rubric 过
        ]
    )
    critic_llm = MagicMock()
    critic_llm.chat = AsyncMock(
        side_effect=[
            MagicMock(content=json.dumps({"verdict": "pass", "feedback": "ok"})),  # iter1 critic
            MagicMock(content=_AUDIT_PASS_JSON),  # iter1 completion audit
        ]
    )
    agent = _agent(["v1", "v2"])
    ctx = OrchestratorContext(
        agent=agent, storage=storage, bus=bus, actor_llm=actor_llm, critic_llm=critic_llm
    )
    e = _edict(
        checks=[CheckSpec(kind="rubric", name="tone", rubric="be nice", pass_threshold=0.8)],
        max_outer_iterations=3,
    )
    storage.save_edict(e)

    r = await run(e, _memorial(e.id), ctx)

    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "v2"
    assert agent.execute.call_count == 2
    # 第一轮 checks 失败记为 issue_class=checks_failed，critic 未被调用
    assert r.state.history[0].critic_result.issue_class == "checks_failed"
    # 第二轮 actor 调用的 user_content 应带上第一轮的 checks 失败反馈
    second_call_content = agent.execute.call_args_list[1].kwargs["user_content"]
    assert "issue_class=checks_failed" in second_call_content
    assert "checks 未通过" in second_call_content
