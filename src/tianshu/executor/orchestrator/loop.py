"""Outer loop 主编排 —— actor → checks → critic → 升级判断。"""

from __future__ import annotations

import logging
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
        actor_result = await ctx.agent.execute(edict, memorial=memorial)
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
