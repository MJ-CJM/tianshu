"""Outer loop 主编排 —— actor → checks → critic → 升级判断。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from tianshu.bus.event_bus import EventBus
from tianshu.executor.orchestrator.checks import ChecksConfigError, run_checks
from tianshu.executor.orchestrator.critic import CriticUnavailable, review
from tianshu.executor.orchestrator.escalation import decide_escalation
from tianshu.executor.orchestrator.persistence import emit_audit, persist_iteration
from tianshu.executor.orchestrator.state import (
    CriticResult,
    IterationRecord,
    OuterLoopState,
)
from tianshu.llm import LLMClient
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActorOverride:
    """L1 升级时给 actor 的配置覆盖（v1 仅用 extra_system_msg；thinking_budget/model 是 TODO）。"""

    thinking_budget: int | None = None
    model: str | None = None
    extra_system_msg: str | None = None  # critic feedback / consultation advice 注入


def derive_actor_override(
    state: OuterLoopState,
    edict: Edict,
) -> ActorOverride:
    """根据当前 level 计算 actor 配置覆盖。"""
    acceptance = edict.acceptance
    assert acceptance is not None
    esc = acceptance.escalation

    # 拼 critic feedback 注入消息
    extra_msg_parts: list[str] = []
    if state.history:
        last_record = state.history[-1]
        if last_record.critic_result and last_record.critic_result.verdict == "fail":
            extra_msg_parts.append(
                f"上一轮 critic 反馈（issue_class={last_record.critic_result.issue_class}）：\n"
                f"{last_record.critic_result.feedback}"
            )
            if last_record.critic_result.suggested_fix:
                extra_msg_parts.append(f"建议修复：{last_record.critic_result.suggested_fix}")
    if state.consultation_advice:
        extra_msg_parts.append(f"九卿会议建议：\n{state.consultation_advice}")

    extra = "\n\n".join(extra_msg_parts) if extra_msg_parts else None

    if state.current_level == "L1":
        return ActorOverride(
            thinking_budget=esc.l1_thinking_budget,
            model=esc.l1_model_upgrade,
            extra_system_msg=extra,
        )
    return ActorOverride(extra_system_msg=extra)


class OrchestratorContext:
    """聚合 orchestrator 运行所需的依赖（避免 run() 参数爆炸）。"""

    def __init__(
        self,
        agent: object,                        # 现有 Agent 实例
        storage: Storage,
        bus: EventBus,
        actor_llm: LLMClient,
        critic_llm: LLMClient,
        critic_fallback_llm: LLMClient | None = None,
        consultation_session: object | None = None,
        notifier: object | None = None,
        approvals: object | None = None,
    ) -> None:
        self.agent = agent
        self.storage = storage
        self.bus = bus
        self.actor_llm = actor_llm
        self.critic_llm = critic_llm
        self.critic_fallback_llm = critic_fallback_llm
        self.consultation_session = consultation_session
        self.notifier = notifier
        self.approvals = approvals


class OrchestratorResult:
    """outer loop 终态。"""

    def __init__(
        self,
        status: TaskStatus,
        final_output: str | None,
        state: OuterLoopState,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.final_output = final_output
        self.state = state
        self.error = error


async def run(
    edict: Edict,
    memorial: Memorial,
    ctx: OrchestratorContext,
) -> OrchestratorResult:
    """outer loop 主入口。要求 edict.acceptance is not None。"""
    assert edict.acceptance is not None, "orchestrator.run 要求 acceptance 不为 None"
    acceptance = edict.acceptance
    state = OuterLoopState(edict_id=edict.id)

    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.started", {"max_outer": acceptance.max_outer_iterations},
    )

    while state.iteration < acceptance.max_outer_iterations:
        iter_started = datetime.now(UTC)
        await emit_audit(
            ctx.bus, ctx.storage, edict.id, memorial.id,
            "outer_loop.iteration.started",
            {"iteration": state.iteration, "level": state.current_level},
        )

        # 1. actor
        override = derive_actor_override(state, edict)
        # 把 critic feedback / consultation advice 注入到下一轮 user_content
        augmented_content = edict.goal
        if edict.context:
            augmented_content += f"\n\nAdditional context: {edict.context}"
        if override.extra_system_msg:
            augmented_content += f"\n\n## 上一轮反馈与建议\n{override.extra_system_msg}"

        # v1：thinking_budget / model 不传给 Agent（Agent 层暂不支持）
        actor_result = await ctx.agent.execute(
            edict,
            memorial=memorial,
            user_content=augmented_content,
        )
        actor_output = actor_result.result or actor_result.summary or ""
        actor_cost = float(getattr(actor_result.usage, "cost_cny", 0.0) or 0.0)

        # 2. checks
        try:
            checks_result = await run_checks(
                acceptance.checks, actor_output, ctx.actor_llm,
            )
        except ChecksConfigError as e:
            return OrchestratorResult(
                status=TaskStatus.FAILED,
                final_output=None,
                state=state,
                error=f"checks 配置错: {e}",
            )

        # 3. critic（仅当 checks 全过才跑）
        critic_result: CriticResult | None = None
        if checks_result.all_passed:
            try:
                critic_result = await review(
                    actor_output, edict, acceptance, ctx.critic_llm,
                    fallback_llm=ctx.critic_fallback_llm,
                )
            except CriticUnavailable as e:
                if acceptance.on_critic_unavailable == "skip":
                    critic_result = CriticResult(verdict="pass", feedback=f"critic 不可用，skip: {e}")
                else:
                    # 升级到人 —— Task 12 实现；当前先返回 fail
                    critic_result = CriticResult(
                        verdict="fail", issue_class="other",
                        feedback=f"critic 不可用: {e}",
                    )
        else:
            critic_result = CriticResult(
                verdict="fail",
                issue_class="checks_failed",
                feedback=f"checks 未通过: {[o.name for o in checks_result.outcomes if not o.passed]}",
            )

        record = IterationRecord(
            iteration=state.iteration,
            level=state.current_level,
            actor_output=actor_output,
            checks_result=checks_result,
            critic_result=critic_result,
            started_at=iter_started,
            finished_at=datetime.now(UTC),
            cost_cny=actor_cost,
        )
        persist_iteration(ctx.storage, edict.id, record)

        await emit_audit(
            ctx.bus, ctx.storage, edict.id, memorial.id,
            "outer_loop.iteration.finished",
            {
                "iteration": state.iteration,
                "level": state.current_level,
                "checks_passed": checks_result.all_passed,
                "critic_verdict": critic_result.verdict if critic_result else None,
            },
        )

        # 4. PASS → 收工
        if critic_result and critic_result.verdict == "pass":
            state = state.advance(record)
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.completed",
                {"iterations": state.iteration, "total_cost": state.total_cost_cny},
            )
            return OrchestratorResult(
                status=TaskStatus.COMPLETED,
                final_output=actor_output,
                state=state,
            )

        # 5. FAIL → advance + 升级（暂不实现 L1/L2/L3 的实际 escalate 动作，Task 10-12 加）
        state = state.advance(record)
        decision = decide_escalation(
            state, edict, acceptance, last_critic_passed=False,
        )
        if decision == "EXHAUSTED":
            return await _handle_exhaustion(state, edict, ctx, memorial)
        if decision != state.current_level:
            state = state.with_level(decision)  # type: ignore[arg-type]
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.escalated",
                {"to": decision, "iteration": state.iteration},
            )
            if decision == "L2" and ctx.consultation_session is not None:
                try:
                    advice = await _run_consultation(
                        edict, state, ctx, memorial,
                    )
                    if advice:
                        state = state.with_consultation_advice(advice)
                except Exception as e:
                    logger.exception("consultation 调用失败，跳过 L2 直接升 L3")
                    await emit_audit(
                        ctx.bus, ctx.storage, edict.id, memorial.id,
                        "outer_loop.escalated",
                        {"from": "L2", "to": "L3", "reason": f"consultation failed: {e}"},
                    )
                    state = state.with_level("L3")

    return await _handle_exhaustion(state, edict, ctx, memorial)


async def _handle_exhaustion(
    state: OuterLoopState,
    edict: Edict,
    ctx: OrchestratorContext,
    memorial: Memorial,
) -> OrchestratorResult:
    """iteration / 预算 / 截止时间耗尽 —— 按 on_exhaustion 决策。"""
    acceptance = edict.acceptance
    assert acceptance is not None
    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.exhausted",
        {
            "iterations": state.iteration,
            "total_cost": state.total_cost_cny,
            "on_exhaustion": acceptance.on_exhaustion,
        },
    )
    last_output = state.history[-1].actor_output if state.history else None
    if acceptance.on_exhaustion == "best_effort":
        return OrchestratorResult(
            status=TaskStatus.COMPLETED,
            final_output=last_output,
            state=state,
            error="exhausted, returning best effort",
        )
    if acceptance.on_exhaustion == "fail":
        return OrchestratorResult(
            status=TaskStatus.FAILED,
            final_output=None,
            state=state,
            error="outer loop exhausted",
        )
    # escalate → Task 12 加；当前 fallback 到 fail
    return OrchestratorResult(
        status=TaskStatus.FAILED,
        final_output=last_output,
        state=state,
        error="exhausted, escalation not yet wired (Task 12)",
    )


async def _run_consultation(
    edict: Edict,
    state: OuterLoopState,
    ctx: OrchestratorContext,
    memorial: Memorial,
) -> str | None:
    """触发跨部协商 —— 复用现有 ConsultationSession，仅返回建议文本。"""
    if ctx.consultation_session is None:
        return None
    acceptance = edict.acceptance
    assert acceptance is not None

    last = state.history[-1] if state.history else None
    last_output = last.actor_output[:2000] if last else "(no history)"
    last_feedback = (
        last.critic_result.feedback
        if last and last.critic_result else "(no critic feedback)"
    )

    topic = (
        f"长任务 outer loop 升级到 L2，请协助审视：\n\n"
        f"# Edict goal\n{edict.goal}\n\n"
        f"# 上一轮 actor 输出\n{last_output}\n\n"
        f"# critic 反馈\n{last_feedback}\n\n"
        f"# 同类问题已连续打回 {state.same_issue_streak} 轮"
    )

    from tianshu.consultation.models import ConsultationRequest
    req = ConsultationRequest(
        topic=topic,
        edict_id=edict.id,
        persona_ids=acceptance.escalation.l2_consultation_personas,
    )
    resp = await ctx.consultation_session.start(req)

    if resp is None:
        return None
    if getattr(resp, "synthesis", None):
        return resp.synthesis
    opinions = getattr(resp, "opinions", None)
    if opinions:
        return "\n\n".join(
            f"- {op.persona_id}: {op.opinion}" for op in opinions
        )
    return None
