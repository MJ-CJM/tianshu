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


def _make_ctx(storage, bus, agent, critic_responses):
    """critic_responses = list[dict] 按调用次序返回 verdict/issue_class/feedback。"""
    actor_llm = MagicMock()
    critic_llm = MagicMock()
    critic_llm.chat = AsyncMock(side_effect=[
        MagicMock(content=json.dumps(r)) for r in critic_responses
    ])
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
async def test_critic_unavailable_skip(storage, bus):
    actor = _agent(["v1"])
    actor_llm = MagicMock()
    critic_llm = MagicMock()
    critic_llm.chat = AsyncMock(side_effect=RuntimeError("critic down"))
    ctx = OrchestratorContext(
        agent=actor, storage=storage, bus=bus,
        actor_llm=actor_llm, critic_llm=critic_llm,
    )
    e = _edict()  # on_critic_unavailable 默认 skip
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "v1"


@pytest.mark.integration
async def test_l3_approval_accept_as_is(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
    ])
    approvals = MagicMock()
    approvals.wait = AsyncMock(return_value=HumanDecision(action="accept_as_is"))
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
    approvals.wait = AsyncMock(return_value=HumanDecision(action="abort"))
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
    approvals.wait = AsyncMock(return_value=HumanDecision(
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
