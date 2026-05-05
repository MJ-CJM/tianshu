"""Outer loop 主编排 —— actor → checks → critic → 升级判断。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from tianshu.bus.event_bus import EventBus
from tianshu.executor.checkpoint import OuterLoopCheckpoint
from tianshu.executor.orchestrator.audit import (
    format_gaps_for_continuation,
    run_completion_audit,
)
from tianshu.executor.orchestrator.budget import (
    BudgetSnapshot,
    HARD_LIMIT,
    SOFT_LANDING_THRESHOLD,
    compute_usage_ratio,
    dominant_dimension,
)
from tianshu.executor.orchestrator.checks import ChecksConfigError, run_checks
from tianshu.executor.orchestrator.critic import CriticUnavailable, review
from tianshu.executor.orchestrator.escalation import decide_escalation
from tianshu.executor.orchestrator.lifecycle import apply_transition
from tianshu.executor.orchestrator.persistence import emit_audit, persist_iteration
from tianshu.executor.orchestrator.supervision import generate_supervision_report
from tianshu.executor.orchestrator.state import (
    CriticResult,
    IterationRecord,
    OuterLoopState,
)
from tianshu.executor.orchestrator.templates import (
    TemplateName,
    render_template,
)
from tianshu.llm import LLMClient
from tianshu.models.common import TaskStatus, UsageSummary

# pause 等待轮询间隔。短一点用户体感快、长一点 DB 压力小。
PAUSE_POLL_INTERVAL_SECONDS: float = 2.0
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
        persona_loader: object | None = None,    # PersonaLoader (rsv 循环 import)
        provider_manager: object | None = None,  # ProviderManager
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
        self.persona_loader = persona_loader
        self.provider_manager = provider_manager


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


def _state_to_dict(state: OuterLoopState) -> dict:
    """frozen dataclass → JSON 友好的 dict。注意 history 不存（已通过 outer_loop_iterations 表持久化）。"""
    return {
        "edict_id": state.edict_id,
        "iteration": state.iteration,
        "current_level": state.current_level,
        "same_issue_streak": state.same_issue_streak,
        "last_critic_issue_class": state.last_critic_issue_class,
        "l1_rounds_used": state.l1_rounds_used,
        "l2_rounds_used": state.l2_rounds_used,
        "consultation_advice": state.consultation_advice,
        "total_cost_cny": state.total_cost_cny,
    }


def _state_from_dict(d: dict) -> OuterLoopState:
    return OuterLoopState(
        edict_id=d["edict_id"],
        iteration=d["iteration"],
        current_level=d["current_level"],
        same_issue_streak=d["same_issue_streak"],
        last_critic_issue_class=d.get("last_critic_issue_class"),
        l1_rounds_used=d["l1_rounds_used"],
        l2_rounds_used=d["l2_rounds_used"],
        consultation_advice=d.get("consultation_advice"),
        total_cost_cny=d["total_cost_cny"],
        history=(),  # resume 时不重建 history
    )


def _save_checkpoint(ctx: "OrchestratorContext", state: OuterLoopState) -> None:
    cp = OuterLoopCheckpoint(
        edict_id=state.edict_id,
        state_dict=_state_to_dict(state),
        saved_at=datetime.now(UTC).isoformat(),
    )
    ctx.storage.save_outer_loop_checkpoint(
        state.edict_id, cp.to_json(), cp.saved_at,
    )


def _load_checkpoint(ctx: "OrchestratorContext", edict_id: str) -> OuterLoopState | None:
    raw = ctx.storage.get_outer_loop_checkpoint(edict_id)
    if not raw:
        return None
    cp = OuterLoopCheckpoint.from_json(raw)
    return _state_from_dict(cp.state_dict)


def _add_usage_to_memorial(
    storage: Storage, memorial: Memorial, delta: UsageSummary
) -> None:
    """累加 delta usage 到 memorial.usage 并持久化。

    memorial 是同一个对象引用（外环全程复用），就地刷新它的 usage 字段，
    避免后续读取到过期值；持久化只更新 usage_json 列。
    """
    cur = memorial.usage
    new_usage = UsageSummary(
        prompt_tokens=cur.prompt_tokens + delta.prompt_tokens,
        completion_tokens=cur.completion_tokens + delta.completion_tokens,
        total_tokens=cur.total_tokens + delta.total_tokens,
        cache_read_tokens=cur.cache_read_tokens + delta.cache_read_tokens,
        cost_cny=cur.cost_cny + delta.cost_cny,
    )
    memorial.usage = new_usage
    try:
        storage.update_memorial_usage(memorial.id, new_usage)
    except Exception as e:
        # 持久化失败不阻塞 outer loop —— 只是奏折页统计不准
        logger.warning(
            "update memorial usage failed for %s: %s", memorial.id, e,
        )


async def _finalize_with_supervision(
    state: OuterLoopState,
    edict: Edict,
    ctx: "OrchestratorContext",
    memorial: Memorial,
    status: TaskStatus,
    final_output: str | None,
    error: str | None = None,
) -> "OrchestratorResult":
    """终态包装：清 checkpoint + (可选) 生成监督报告 + 返回 OrchestratorResult。

    监督报告仅在 edict.acceptance.critic.persona_id 配了且 ctx.persona_loader 可用时生成。
    LLM 失败时吞异常不阻塞终态返回。
    """
    ctx.storage.clear_outer_loop_checkpoint(edict.id)

    acceptance = edict.acceptance
    persona_ids: list[str] = (
        acceptance.critic.effective_persona_ids()
        if acceptance and acceptance.critic
        else []
    )
    persona_loader = getattr(ctx, "persona_loader", None)
    if persona_ids and persona_loader is not None:
        from tianshu.executor.orchestrator.critic import _resolve_critic_llm

        for pid in persona_ids:
            persona = persona_loader.get(pid)
            if persona is None:
                logger.warning(
                    "supervision skipped for persona '%s' (not found) edict %s",
                    pid, edict.id,
                )
                continue
            try:
                llm = _resolve_critic_llm(persona, ctx, ctx.critic_llm)
                report = await generate_supervision_report(
                    edict, state, status, persona, llm, ctx.storage,
                    memorial_id=memorial.id,
                )
                ctx.storage.save_supervision_report({
                    "edict_id": report.edict_id,
                    "memorial_id": report.memorial_id,
                    "persona_id": report.persona_id,
                    "persona_name": report.persona_name,
                    "final_status": report.final_status.value,
                    "iterations_count": report.iterations_count,
                    "total_cost_cny": report.total_cost_cny,
                    "report_json": report.model_dump_json(),
                    "created_at": report.created_at.isoformat(),
                })
                await emit_audit(
                    ctx.bus, ctx.storage, edict.id, memorial.id,
                    "outer_loop.supervision_completed",
                    {"persona_id": report.persona_id, "final_status": status.value},
                )
            except Exception as e:
                logger.exception(
                    "supervision report failed for persona '%s' edict %s (non-fatal): %s",
                    pid, edict.id, e,
                )

    return OrchestratorResult(
        status=status, final_output=final_output, state=state, error=error,
    )


async def run(
    edict: Edict,
    memorial: Memorial,
    ctx: OrchestratorContext,
) -> OrchestratorResult:
    """outer loop 主入口。要求 edict.acceptance is not None。"""
    assert edict.acceptance is not None, "orchestrator.run 要求 acceptance 不为 None"
    acceptance = edict.acceptance

    # Resume：仅 checkpointed/background profile 启用
    if edict.execution_profile in ("checkpointed", "background"):
        resumed = _load_checkpoint(ctx, edict.id)
        state = resumed if resumed else OuterLoopState(edict_id=edict.id)
        if resumed:
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.resumed",
                {"iteration": state.iteration, "level": state.current_level},
            )
    else:
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

        # ---- NEW: 检查用户是否手动 pause —— 等待恢复后续跑，不退出 loop ----
        latest_edict = ctx.storage.get_edict(edict.id)
        if latest_edict and latest_edict.runtime.lifecycle_phase == "paused":
            # 入场：仅首次进入 paused 时发事件 + checkpoint
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.paused",
                {"iteration": state.iteration},
            )
            if edict.execution_profile in ("checkpointed", "background"):
                _save_checkpoint(ctx, state)
            # 等待循环：每 PAUSE_POLL_INTERVAL 秒重读 lifecycle，直到 active 或 cancelled
            while True:
                await asyncio.sleep(PAUSE_POLL_INTERVAL_SECONDS)
                latest_edict = ctx.storage.get_edict(edict.id)
                if not latest_edict:
                    # edict 被删除，按 cancelled 退出
                    return OrchestratorResult(
                        status=TaskStatus.CANCELLED,
                        final_output=None,
                        state=state,
                        error="edict deleted while paused",
                    )
                phase = latest_edict.runtime.lifecycle_phase
                if phase == "active":
                    # 续跑：发恢复事件，刷新本地 edict 引用，跳出等待
                    await emit_audit(
                        ctx.bus, ctx.storage, edict.id, memorial.id,
                        "outer_loop.resumed",
                        {"iteration": state.iteration},
                    )
                    edict = latest_edict
                    break
                if phase == "complete":
                    # 外部把 edict 终结（cancel / 其他），干净退出
                    return OrchestratorResult(
                        status=TaskStatus.CANCELLED,
                        final_output=None,
                        state=state,
                        error="edict ended while paused",
                    )
                # phase == "paused" 或 "winding_down" 继续等
        # ---- pause 检查结束 ----

        # ---- NEW: 预算检查（usage_ratio）+ 软着陆触发 ----
        budget_snap = BudgetSnapshot(
            tokens_used=memorial.usage.total_tokens if memorial.usage else 0,
            token_budget=edict.runtime.token_budget,
            cost_used_cny=float(memorial.usage.cost_cny) if memorial.usage else 0.0,
            cost_budget_cny=edict.runtime.cost_budget_cny,
            time_used_seconds=int(
                (datetime.now(UTC) - memorial.created_at).total_seconds()
            ) if getattr(memorial, "created_at", None) else 0,
            deadline_seconds=acceptance.deadline_seconds,
        )
        usage_ratio = compute_usage_ratio(budget_snap)
        cur_phase = edict.runtime.lifecycle_phase

        if usage_ratio >= HARD_LIMIT:
            if cur_phase != "winding_down":
                result = apply_transition(
                    ctx.storage, ctx.bus, edict.id, memorial.id,
                    cur_phase, "winding_down",
                    reason=f"hard_limit_reached usage_ratio={usage_ratio:.2f}",
                )
                if result is not None:
                    edict = edict.model_copy(update={
                        "runtime": edict.runtime.model_copy(update={"lifecycle_phase": "winding_down"})
                    })
                    wind_down_prompt = render_template(
                        TemplateName.WIND_DOWN, objective=edict.goal,
                    )
                    state = state.with_consultation_advice(wind_down_prompt)
                    await emit_audit(
                        ctx.bus, ctx.storage, edict.id, memorial.id,
                        "edict.wind_down.entered",
                        {
                            "usage_ratio": usage_ratio,
                            "trigger_field": dominant_dimension(budget_snap) or "unknown",
                            "threshold_type": "hard_limit",
                        },
                    )
                # else: 转移被拒（如 phase=paused），保持现状继续 actor turn
            else:
                # 已经 wind_down 过一次仍超额，强制 finalize
                result = apply_transition(
                    ctx.storage, ctx.bus, edict.id, memorial.id,
                    "winding_down", "complete",
                    reason="budget_exhausted",
                )
                if result is not None:
                    trigger = dominant_dimension(budget_snap) or "unknown"
                    last_output = state.history[-1].actor_output if state.history else None
                    return await _finalize_with_supervision(
                        state, edict, ctx, memorial,
                        TaskStatus.FAILED, last_output,
                        error=f"budget_exhausted: {trigger} (usage_ratio={usage_ratio:.2f})",
                    )
        elif usage_ratio >= SOFT_LANDING_THRESHOLD and cur_phase == "active":
            result = apply_transition(
                ctx.storage, ctx.bus, edict.id, memorial.id,
                cur_phase, "winding_down",
                reason=f"soft_landing_threshold usage_ratio={usage_ratio:.2f}",
            )
            if result is not None:
                edict = edict.model_copy(update={
                    "runtime": edict.runtime.model_copy(update={"lifecycle_phase": "winding_down"})
                })
                wind_down_prompt = render_template(
                    TemplateName.WIND_DOWN, objective=edict.goal,
                )
                state = state.with_consultation_advice(wind_down_prompt)
                await emit_audit(
                    ctx.bus, ctx.storage, edict.id, memorial.id,
                    "edict.wind_down.entered",
                    {
                        "usage_ratio": usage_ratio,
                        "trigger_field": dominant_dimension(budget_snap) or "unknown",
                        "threshold_type": "soft_landing",
                    },
                )
        # ---- 预算检查结束 ----

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
        # Actor 错误时把 error 信息暴露出来，让 critic + 监督报告能正确诊断（而非误判为 actor 偷懒）
        if not actor_output and getattr(actor_result, "error", None):
            actor_output = f"[ACTOR ERROR] {actor_result.error}"
            logger.warning(
                "[ORCH] edict %s iter %d: actor returned empty output with error: %s",
                edict.id, state.iteration, actor_result.error,
            )
        raw_actor_cost = getattr(actor_result.usage, "cost_cny", 0.0) if actor_result.usage else 0.0
        actor_cost = float(raw_actor_cost) if isinstance(raw_actor_cost, (int, float)) else 0.0
        # 注：critic 调用的 cost 在下面 critic_result.cost_cny 上，会一并加入 record.cost_cny

        # 2. checks
        try:
            checks_result = await run_checks(
                acceptance.checks, actor_output, ctx.actor_llm,
            )
        except ChecksConfigError as e:
            last_output = state.history[-1].actor_output if state.history else actor_output
            return await _finalize_with_supervision(
                state, edict, ctx, memorial,
                TaskStatus.FAILED, last_output,
                error=f"checks 配置错: {e}",
            )

        # 3. critic（仅当 checks 全过才跑）
        critic_result: CriticResult | None = None
        critic_skipped = False
        if checks_result.all_passed:
            try:
                critic_result = await review(
                    actor_output, edict, acceptance, ctx.critic_llm,
                    fallback_llm=ctx.critic_fallback_llm,
                    ctx=ctx,
                )
            except CriticUnavailable as e:
                if acceptance.on_critic_unavailable == "skip":
                    critic_result = CriticResult(verdict="pass", feedback=f"critic 不可用，skip: {e}")
                    critic_skipped = True
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
            cost_cny=actor_cost + (critic_result.cost_cny if critic_result else 0.0),
        )
        persist_iteration(ctx.storage, edict.id, record)

        # critic 用量回写 memorial.usage —— actor 部分已由 agent._accumulate_usage 累加，
        # 此处只补 critic LLM 的 cost / token，让单条奏折"花费"反映真实总成本。
        if critic_result and critic_result.usage is not None:
            _add_usage_to_memorial(ctx.storage, memorial, critic_result.usage)

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

        # 4. PASS → completion audit 门 → 收工（除非 min_outer_iterations 未满，进入"持续优化"模式）
        if critic_result and critic_result.verdict == "pass":
            state = state.advance(record)

            # ---- completion audit 门 ----
            # spec §7：critic-skip 时 audit 退化为 actor 自审
            audit_llm = ctx.actor_llm if critic_skipped else ctx.critic_llm
            audit_executor = "actor_self_audit" if critic_skipped else "critic_llm"
            audit_result = await run_completion_audit(
                actor_output=actor_output,
                objective=edict.goal,
                acceptance=acceptance,
                llm=audit_llm,
            )
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "edict.audit.executed",
                {
                    "passed": audit_result.passed,
                    "gaps_count": len(audit_result.gaps),
                    "iteration": state.iteration,
                    "executor_persona": audit_executor,  # "critic_llm" or "actor_self_audit"
                },
            )
            if not audit_result.passed:
                # 把 gaps 反哺为下一轮 actor 的续转 prompt
                gaps_text = format_gaps_for_continuation(audit_result.gaps)
                continuation = render_template(
                    TemplateName.CONTINUATION,
                    objective=edict.goal,
                    critic_feedback=None,
                    audit_gaps=gaps_text,
                )
                state = state.with_consultation_advice(continuation)
                await emit_audit(
                    ctx.bus, ctx.storage, edict.id, memorial.id,
                    "edict.continuation.injected",
                    {
                        "iteration": state.iteration,
                        "has_critic_feedback": False,
                        "has_audit_gaps": True,
                    },
                )
                if edict.execution_profile in ("checkpointed", "background"):
                    _save_checkpoint(ctx, state)
                continue  # 进入下一轮 outer iter
            # ---- audit 通过：保留原有"持续优化模式" + finalize 逻辑不变 ----

            min_iter = getattr(acceptance, "min_outer_iterations", 1) or 1
            if state.iteration < min_iter:
                # 持续优化：把 critic 的 improvement_hints 注入下一轮 actor
                hint = critic_result.improvement_hints
                if hint:
                    state = state.with_consultation_advice(
                        f"[持续优化模式] 上一轮已合格，但仍可优化：\n{hint}"
                    )
                await emit_audit(
                    ctx.bus, ctx.storage, edict.id, memorial.id,
                    "outer_loop.continued_for_optimization",
                    {
                        "iteration": state.iteration,
                        "min_iterations": min_iter,
                        "has_hints": bool(hint),
                    },
                )
                if edict.execution_profile in ("checkpointed", "background"):
                    _save_checkpoint(ctx, state)
                continue  # 进入下一轮 outer iter
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.completed",
                {"iterations": state.iteration, "total_cost": state.total_cost_cny},
            )
            return await _finalize_with_supervision(
                state, edict, ctx, memorial,
                TaskStatus.COMPLETED, actor_output,
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
                    decision = "L3"  # 让下面的 L3 分支接到

            if decision == "L3":
                human_decision = await _escalate_to_human(state, edict, ctx, memorial)
                state, edict, terminal = _apply_human_decision(state, human_decision, edict)
                if terminal == "abort":
                    last_output = state.history[-1].actor_output if state.history else None
                    return await _finalize_with_supervision(
                        state, edict, ctx, memorial,
                        TaskStatus.FAILED, last_output,
                        error="aborted by human",
                    )
                if terminal == "accept_as_is":
                    last = state.history[-1] if state.history else None
                    return await _finalize_with_supervision(
                        state, edict, ctx, memorial,
                        TaskStatus.COMPLETED,
                        last.actor_output if last else None,
                    )
                # else continue / modify_acceptance → 继续循环

        # checkpoint（仅 checkpointed/background）
        if edict.execution_profile in ("checkpointed", "background"):
            _save_checkpoint(ctx, state)

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
        return await _finalize_with_supervision(
            state, edict, ctx, memorial,
            TaskStatus.COMPLETED, last_output,
            error="exhausted, returning best effort",
        )
    if acceptance.on_exhaustion == "fail":
        return await _finalize_with_supervision(
            state, edict, ctx, memorial,
            TaskStatus.FAILED, last_output,
            error="outer loop exhausted",
        )
    if acceptance.on_exhaustion == "escalate":
        state = state.with_level("L3")
        decision = await _escalate_to_human(state, edict, ctx, memorial)
        state, edict, terminal = _apply_human_decision(state, decision, edict)
        if terminal == "abort":
            return await _finalize_with_supervision(
                state, edict, ctx, memorial,
                TaskStatus.FAILED, last_output,
                error="exhausted + aborted",
            )
        # accept_as_is or None（continue/modify 在 exhausted 后无意义，按 accept 处理）
        return await _finalize_with_supervision(
            state, edict, ctx, memorial,
            TaskStatus.COMPLETED, last_output,
        )
    # 不应到达（其他 on_exhaustion 已在前面 return）
    return await _finalize_with_supervision(
        state, edict, ctx, memorial,
        TaskStatus.FAILED, last_output,
        error="unhandled on_exhaustion",
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


from tianshu.executor.orchestrator.human_decision import HumanDecision  # noqa: E402


async def _escalate_to_human(
    state: OuterLoopState,
    edict: Edict,
    ctx: OrchestratorContext,
    memorial: Memorial,
) -> HumanDecision:
    """发推送 + 等审批 —— duck-typed notifier + approvals 接口。"""
    last = state.history[-1] if state.history else None
    payload = {
        "edict_id": edict.id,
        "iteration": state.iteration,
        "level": "L3",
        "best_output": last.actor_output if last else None,
        "critic_feedback": last.critic_result.feedback if last and last.critic_result else None,
        "history_length": len(state.history),
    }
    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.approval.requested", payload,
    )

    # 推送通知（如可用）
    if ctx.notifier is not None:
        try:
            await ctx.notifier.notify(
                channel="default",
                title=f"长任务待审批 — Edict {edict.id[:8]}",
                body=f"已迭代 {state.iteration} 轮仍未通过 critic，请审阅。",
            )
        except Exception:
            logger.exception("notifier 推送失败，继续等审批")

    # 等审批
    if ctx.approvals is None:
        # 无 approvals → 视为超时 → best_effort or abort（看 on_approval_timeout）
        on_timeout = (
            edict.acceptance.on_approval_timeout
            if edict.acceptance else "best_effort"
        )
        return HumanDecision(action="accept_as_is" if on_timeout == "best_effort" else "abort")

    timeout = (
        edict.acceptance.deadline_seconds
        if edict.acceptance and edict.acceptance.deadline_seconds
        else 86400  # 默认 24h
    )
    try:
        # 优先用 ApprovalManager 的 outer-loop 接口（真审批等待）；
        # 测试 mock 仍可走 .wait() 老接口
        if hasattr(ctx.approvals, "wait_for_outer_loop_decision"):
            raw = await ctx.approvals.wait_for_outer_loop_decision(
                edict_id=edict.id,
                payload=payload,
                timeout_seconds=float(timeout),
            )
        else:
            raw = await ctx.approvals.wait(
                edict_id=edict.id,
                timeout_seconds=timeout,
            )
        if raw is None:
            raise asyncio.TimeoutError("approval timeout / no decision")
        if isinstance(raw, dict):
            decision = HumanDecision.model_validate(raw)
        elif isinstance(raw, HumanDecision):
            decision = raw
        else:
            decision = HumanDecision.model_validate(raw if isinstance(raw, dict) else raw.__dict__)
    except Exception as e:
        logger.warning("approval 超时 / 失败: %s", e)
        on_timeout = (
            edict.acceptance.on_approval_timeout
            if edict.acceptance else "best_effort"
        )
        return HumanDecision(action="accept_as_is" if on_timeout == "best_effort" else "abort")

    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.approval.received", {"action": decision.action},
    )
    return decision


def _apply_human_decision(
    state: OuterLoopState,
    decision: HumanDecision,
    edict: Edict,
) -> tuple[OuterLoopState, Edict, str | None]:
    """根据 human decision 更新 state / edict，返回 (new_state, new_edict, terminal_action).
    terminal_action: 'accept_as_is' | 'abort' | None（None=继续）。
    """
    from dataclasses import replace as dc_replace
    if decision.action == "abort":
        return state, edict, "abort"
    if decision.action == "accept_as_is":
        return state, edict, "accept_as_is"
    if decision.action == "modify_acceptance":
        new_edict = edict.model_copy(update={"acceptance": decision.new_acceptance})
        new_state = state.with_level("L0")
        new_state = dc_replace(new_state, same_issue_streak=0, last_critic_issue_class=None)
        return new_state, new_edict, None
    # continue: 把 feedback 注入下一轮（通过 consultation_advice 字段复用）
    new_state = state.with_level("L0")
    if decision.feedback:
        new_state = new_state.with_consultation_advice(
            f"用户审批反馈：{decision.feedback}"
        )
    new_state = dc_replace(new_state, same_issue_streak=0)
    return new_state, edict, None
