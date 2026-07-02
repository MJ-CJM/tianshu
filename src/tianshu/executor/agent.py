"""Agent ReAct Loop - core execution engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

import litellm
from pydantic import BaseModel, Field

from tianshu.config_manager import ConfigManager
from tianshu.executor.ambient import bind_edict, bind_persona
from tianshu.executor.compaction.auto import auto_compact, should_auto_compact
from tianshu.executor.compaction.micro import micro_compact
from tianshu.executor.compaction.reactive import reactive_compact
from tianshu.executor.exit_reason import ExitReason
from tianshu.executor.hooks import HookRegistry, HookType
from tianshu.executor.loop_state import LoopState
from tianshu.executor.streaming import StreamCallback
from tianshu.llm import LLMClient
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.types import ToolResult

logger = logging.getLogger(__name__)

_SYSTEM_IDENTITY = (
    "You are Tianshu, an AI execution assistant. "
    "Follow the user's instructions and use available tools to complete tasks. "
    "When done, summarize the result concisely. "
    "If you cannot complete the task, explain why."
)


class AgentResult(BaseModel):
    status: TaskStatus
    summary: str | None = None
    result: str | None = None
    usage: UsageSummary = Field(default_factory=UsageSummary)
    error: str | None = None
    events: list[dict] = Field(default_factory=list)
    # Phase 1 new fields
    exit_reason: ExitReason = ExitReason.COMPLETED
    iteration_count: int = 0
    compact_count: int = 0
    recovery_attempts: dict = Field(default_factory=dict)
    # 2026-04-30: DeepSeek reasoner / 新版 thinking-mode 模型 follow_up 时
    # 必须把上一轮 reasoning_content 一起回传；executor 写入 memorial 持久化。
    reasoning_content: str | None = None


#: 仅"助手 persona"（飞书里跟用户对话的那个）能调用的工具集。
#: 业务执行 persona（被指派去做事的 tbh/wy/ys 等）即使 toggle 开了
#: 也看不到这些工具 —— 防递归颁敕（执行人把"每日推送"当成"再造一道敕令"）。
ASSISTANT_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "submit_edict",
        "list_edicts",
        "get_edict_status",
        "list_personas",
    }
)


class Agent:
    def __init__(
        self,
        config_manager: ConfigManager,
        tools: ToolRegistry,
        skills: SkillsLoader,
        hook_registry: HookRegistry | None = None,
        prompt_builder: PromptBuilder | None = None,
        provider_manager: object | None = None,
        metrics_store: object | None = None,
        assistant_persona_id_provider: Callable[[], str | None] | None = None,
    ) -> None:
        self._config_manager = config_manager
        self._tools = tools
        self._skills = skills
        self._hooks = hook_registry
        self._prompt_builder = prompt_builder
        self._provider_manager = provider_manager
        self._metrics_store = metrics_store
        self._shutdown_event = asyncio.Event()
        # 实时读取（每次 execute 调一次），避免 toggle / persona 切换需重启
        self._assistant_persona_id_provider = assistant_persona_id_provider

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def execute(
        self,
        edict: Edict,
        memorial: Memorial | None = None,
        on_event: Callable[[dict], None] | None = None,
        history: list[dict] | None = None,
        user_content: str | None = None,
        tool_filter: list[str] | None = None,
        persona: object | None = None,
        stream_callback: StreamCallback | None = None,
        cancellation_token: object | None = None,
    ) -> AgentResult:
        # Read runtime config at execution start
        config_state = self._config_manager.state
        if not config_state.enabled:
            return AgentResult(
                status=TaskStatus.FAILED,
                error="LLM is currently disabled",
            )

        # Extract persona-level LLM config override
        persona_config_name = getattr(persona, "llm_config_name", None) if persona else None

        if self._provider_manager and hasattr(self._provider_manager, "get_client"):
            llm = self._provider_manager.get_client(config_name_override=persona_config_name)
        else:
            # Direct LLMClient path: apply persona config if available
            if persona_config_name:
                named = self._config_manager.get_config(persona_config_name)
                if named and named.enabled:
                    config_state = named
            llm = LLMClient(
                model=config_state.model,
                api_key=config_state.api_key,
                api_base=config_state.api_base,
                max_retries=config_state.max_retries,
                temperature=config_state.temperature,
                top_p=config_state.top_p,
                max_tokens=config_state.max_tokens,
            )

        agent_cfg = self._config_manager.agent_config

        # Use PromptBuilder if available, else fallback to legacy
        if self._prompt_builder:
            system_prompt = await self._prompt_builder.build(
                edict,
                persona=persona,
                skills_char_budget=agent_cfg.skills_char_budget,
            )
        else:
            if persona and hasattr(persona, "id"):
                logger.warning(
                    "[AGENT] Edict %s: prompt_builder unavailable, persona %s context will be lost",
                    edict.id,
                    getattr(persona, "id", "unknown"),
                )
            system_prompt = self._build_system_prompt(edict, agent_cfg.skills_char_budget)

        if user_content is None:
            user_content = edict.goal
            if edict.context:
                user_content += f"\n\nAdditional context: {edict.context}"

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_content})

        usage = UsageSummary()
        events: list[dict] = []

        def _emit(event: dict) -> None:
            events.append(event)
            if on_event is not None:
                on_event(event)

        max_iterations = edict.runtime.max_iterations or agent_cfg.agent_max_iterations
        # Context window size for compaction thresholds (separate from spending budget)
        context_limit = 128000  # TODO: derive from model config / provider metadata

        # --- New: LoopState replaces mutable messages list ---
        state = LoopState(messages=tuple(messages), iteration=0)
        recovery_attempts: dict[str, int] = {}
        # 连续同错熔断：(tool_name, error_signature) → 连续次数。
        # 任一签名连续 3 次 → 主动 break，避免 LLM 拿着同样的 schema/args bug 死磕
        # 烧满 max_iterations 的 token（如 read_file 的 limit/offset 幻觉）。
        repeated_failures: dict[tuple[str, str], int] = {}
        REPEATED_FAILURE_LIMIT = 3

        while state.iteration < max_iterations:
            if self._shutdown_event.is_set():
                return self._build_result(
                    state,
                    ExitReason.CANCELLED,
                    usage=usage,
                    events=events,
                    recovery=recovery_attempts,
                    error="Shutdown requested",
                )

            # Phase 1: micro compact (per-turn tool result cleanup, zero LLM cost)
            state = micro_compact(state)

            # Phase 2: auto compact (threshold-triggered LLM summarization)
            if should_auto_compact(state.messages, context_limit) and not state.compact_attempted:
                try:
                    state = await auto_compact(state, llm, context_limit)
                    _emit(
                        {
                            "type": "context.compacted",
                            "iteration": state.iteration,
                            "strategy": "auto",
                            "message_count": len(state.messages),
                        }
                    )
                except Exception:
                    logger.warning("Auto compact failed", exc_info=True)

            logger.debug(
                "[AGENT] Edict %s: iteration %d/%d, messages=%d",
                edict.id,
                state.iteration,
                max_iterations,
                len(state.messages),
            )
            _emit({"type": "iteration.started", "iteration": state.iteration})

            # Before iteration hook (budget check, audit, etc.)
            if self._hooks:
                iter_hook = await self._hooks.run(
                    HookType.BEFORE_ITERATION,
                    edict=edict,
                    iteration=state.iteration,
                    usage=usage,
                )
                if iter_hook.block:
                    return self._build_result(
                        state,
                        ExitReason.HOOK_BLOCKED,
                        usage=usage,
                        events=events,
                        recovery=recovery_attempts,
                        error=f"Blocked at iteration {state.iteration}: {iter_hook.reason}",
                    )

            openai_tools = self._tools.get_openai_tools() or None
            if tool_filter and openai_tools:
                openai_tools = [
                    t for t in openai_tools if t.get("function", {}).get("name") in tool_filter
                ] or None

            # ASSISTANT_ONLY_TOOLS 过滤：非助手 persona 不应看见 submit_edict 等
            # 颁敕工具，否则 cron 触发的执行 persona（tbh/wy/...）会把"每日推送"
            # 误解为"再造一道敕令"无限套娃。每轮重新读 provider 以反映 toggle / persona 切换。
            if openai_tools and self._assistant_persona_id_provider is not None:
                persona_id = getattr(persona, "id", None) if persona else None
                try:
                    assistant_id = self._assistant_persona_id_provider()
                except Exception:
                    logger.warning(
                        "[AGENT] assistant_persona_id_provider raised; "
                        "skipping ASSISTANT_ONLY filter (fail-open by design)",
                        exc_info=True,
                    )
                    assistant_id = None
                if assistant_id and persona_id != assistant_id:
                    before = len(openai_tools)
                    openai_tools = [
                        t
                        for t in openai_tools
                        if t.get("function", {}).get("name") not in ASSISTANT_ONLY_TOOLS
                    ] or None
                    after = len(openai_tools or [])
                    if before != after:
                        logger.debug(
                            "[AGENT] persona %r is not assistant %r — filtered "
                            "%d ASSISTANT_ONLY tools (%d → %d)",
                            persona_id,
                            assistant_id,
                            before - after,
                            before,
                            after,
                        )

            # LLM input hook
            current_messages = list(state.messages)
            if self._hooks:
                input_hook = await self._hooks.run(
                    HookType.LLM_INPUT,
                    messages=current_messages,
                    edict=edict,
                    iteration=state.iteration,
                )
                if input_hook.modified_args and "messages" in input_hook.modified_args:
                    current_messages = input_hook.modified_args["messages"]

            # Check cancellation before LLM call
            if cancellation_token and getattr(cancellation_token, "is_cancelled", False):
                return self._build_result(
                    state,
                    ExitReason.CANCELLED,
                    usage=usage,
                    events=events,
                    recovery=recovery_attempts,
                )

            # Phase 3: LLM call with context overflow recovery + fallback
            try:
                if stream_callback:
                    final_response = None
                    async for chunk in llm.chat_stream(current_messages, tools=openai_tools):
                        if cancellation_token and getattr(
                            cancellation_token, "is_cancelled", False
                        ):
                            return self._build_result(
                                state,
                                ExitReason.CANCELLED,
                                usage=usage,
                                events=events,
                                recovery=recovery_attempts,
                            )
                        if chunk.content and not chunk.tool_calls:
                            await stream_callback.on_delta(chunk.content)
                        final_response = chunk
                    response = final_response
                else:
                    response = await llm.chat(current_messages, tools=openai_tools)
            except Exception as e:
                if _is_context_overflow(e):
                    recovered = await reactive_compact(state, llm=llm, context_limit=context_limit)
                    if recovered is not None and not state.compact_attempted:
                        state = recovered
                        recovery_attempts["context_overflow"] = (
                            recovery_attempts.get("context_overflow", 0) + 1
                        )
                        _emit(
                            {
                                "type": "context.compacted",
                                "iteration": state.iteration,
                                "strategy": "reactive",
                                "message_count": len(state.messages),
                            }
                        )
                        continue
                    return self._build_result(
                        state,
                        ExitReason.CONTEXT_OVERFLOW,
                        usage=usage,
                        events=events,
                        recovery=recovery_attempts,
                        error=str(e),
                    )

                # Attempt fallback model if configured
                fallback_name = agent_cfg.fallback_llm_config_name
                if fallback_name and "fallback" not in recovery_attempts:
                    fallback_cfg = self._config_manager.get_config(fallback_name)
                    if fallback_cfg and fallback_cfg.enabled:
                        logger.warning(
                            "[AGENT] Primary LLM failed, switching to fallback '%s': %s",
                            fallback_name,
                            e,
                        )
                        fallback_llm = LLMClient(
                            model=fallback_cfg.model,
                            api_key=fallback_cfg.api_key,
                            api_base=fallback_cfg.api_base,
                            max_retries=fallback_cfg.max_retries,
                            temperature=fallback_cfg.temperature,
                            top_p=fallback_cfg.top_p,
                            max_tokens=fallback_cfg.max_tokens,
                        )
                        try:
                            response = await fallback_llm.chat(current_messages, tools=openai_tools)
                            recovery_attempts["fallback"] = fallback_name
                        except Exception as fallback_err:
                            return self._build_result(
                                state,
                                ExitReason.LLM_ERROR,
                                usage=usage,
                                events=events,
                                recovery=recovery_attempts,
                                error=f"Primary: {e}; Fallback: {fallback_err}",
                            )
                    else:
                        return self._build_result(
                            state,
                            ExitReason.LLM_ERROR,
                            usage=usage,
                            events=events,
                            recovery=recovery_attempts,
                            error=str(e),
                        )
                else:
                    logger.exception(
                        "[AGENT] Edict %s: iter %d LLM call failed (no fallback configured): %s",
                        edict.id,
                        state.iteration,
                        e,
                    )
                    return self._build_result(
                        state,
                        ExitReason.LLM_ERROR,
                        usage=usage,
                        events=events,
                        recovery=recovery_attempts,
                        error=str(e),
                    )

            usage = self._accumulate_usage(usage, response.usage)
            state = state.accumulate_usage(
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )

            # LLM output hook
            if self._hooks:
                await self._hooks.run(
                    HookType.LLM_OUTPUT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                    iteration=state.iteration,
                    usage=response.usage,
                    edict=edict,
                    config_state=config_state,
                    provider_name=getattr(llm, "provider_name", None),
                )

            logger.debug(
                "[AGENT] Edict %s: iter %d LLM response, tool_calls=%d, has_content=%s, finish_reason=%s",
                edict.id,
                state.iteration,
                len(response.tool_calls or []),
                bool(response.content),
                response.finish_reason,
            )

            if response.tool_calls:
                # Build assistant message with tool_calls
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": tc["args"],
                            },
                        }
                        for tc in response.tool_calls
                    ],
                }
                # DeepSeek thinking 模式要求 reasoning_content 必须随 tool_calls 一起回传
                # （否则下一轮请求会报 "reasoning_content in the thinking mode must be passed back"）
                if response.reasoning_content:
                    assistant_msg["reasoning_content"] = response.reasoning_content
                new_messages = list(state.messages) + [assistant_msg]

                # Execute each tool call sequentially
                for tc in response.tool_calls:
                    # Tier fast-path: T0_READONLY bypasses HookRegistry at agent layer.
                    # Registry has its own T0 fast path too — defense in depth, avoids
                    # emitting noise hook events for readonly tools. Spec Section 2.
                    from tianshu.tools.types import ToolTier

                    tool_defn = self._tools.get_definition(tc["name"])
                    tool_tier = tool_defn.tier if tool_defn else ToolTier.T4_DANGEROUS.value
                    is_fast_path = tool_tier == ToolTier.T0_READONLY.value

                    if self._hooks and not is_fast_path:
                        hook_result = await self._hooks.run(
                            HookType.BEFORE_TOOL_CALL,
                            tool_name=tc["name"],
                            tool_args=tc["args"],
                            iteration=state.iteration,
                            edict=edict,
                            memorial=memorial,
                        )
                        if hook_result.block:
                            new_messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": f"Tool blocked: {hook_result.reason}",
                                }
                            )
                            _emit(
                                {
                                    "type": "tool.blocked",
                                    "tool": tc["name"],
                                    "iteration": state.iteration,
                                    "reason": hook_result.reason,
                                }
                            )
                            continue

                    logger.debug(
                        "[AGENT] Edict %s: iter %d tool=%s, args=%.200s",
                        edict.id,
                        state.iteration,
                        tc["name"],
                        str(tc["args"])[:200],
                    )
                    if stream_callback:
                        await stream_callback.on_tool_call_start(tc["name"])
                    try:
                        with bind_edict(edict), bind_persona(persona):
                            tool_result = await self._tools.execute(
                                tc["name"],
                                tc["args"],
                                lifecycle_phase=edict.runtime.lifecycle_phase,
                            )
                    except Exception as tool_err:
                        tool_result = ToolResult(content=f"Tool error: {tool_err}", is_error=True)
                    if stream_callback:
                        await stream_callback.on_tool_call_end(tc["name"], tool_result)

                    content = tool_result.content
                    max_chars = tool_defn.max_result_chars if tool_defn else 8000
                    if len(content) > max_chars:
                        content = content[:max_chars] + "\n[... truncated]"
                    new_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": content,
                        }
                    )

                    if self._hooks and not is_fast_path:
                        await self._hooks.run(
                            HookType.AFTER_TOOL_CALL,
                            tool_name=tc["name"],
                            tool_args=tc["args"],
                            tool_result=tool_result,
                            iteration=state.iteration,
                            edict=edict,
                            memorial=memorial,
                        )

                    args_str = tc["args"] if isinstance(tc["args"], str) else json.dumps(tc["args"])
                    _emit(
                        {
                            "type": "tool.failed" if tool_result.is_error else "tool.completed",
                            "tool": tc["name"],
                            "iteration": state.iteration,
                            "args_preview": args_str[:200],
                            "result_preview": tool_result.content[:200],
                            "is_error": tool_result.is_error,
                            "details": tool_result.details,
                        }
                    )

                    # 连续同错熔断：失败计数 +1；成功清掉该 tool 的所有计数
                    if tool_result.is_error:
                        # 错误签名：取前 200 字符消除噪音（行号/时间戳等）
                        err_sig = (tool_result.content or "")[:200]
                        key = (tc["name"], err_sig)
                        repeated_failures[key] = repeated_failures.get(key, 0) + 1
                        if repeated_failures[key] >= REPEATED_FAILURE_LIMIT:
                            logger.warning(
                                "[AGENT] Edict %s: tool %r failed %d times with same error, breaking loop. error=%.150s",
                                edict.id,
                                tc["name"],
                                repeated_failures[key],
                                err_sig,
                            )
                            return self._build_result(
                                state.next_turn(new_messages),
                                ExitReason.REPEATED_TOOL_FAILURE,
                                usage=usage,
                                events=events,
                                recovery=recovery_attempts,
                                error=(
                                    f"工具 {tc['name']!r} 连续 {repeated_failures[key]} "
                                    f"次失败，错误一致：{err_sig[:120]}"
                                ),
                            )
                    else:
                        # 任一次成功 → 清掉该 tool 的所有失败计数
                        repeated_failures = {
                            k: v for k, v in repeated_failures.items() if k[0] != tc["name"]
                        }

                # State replacement: advance to next turn
                state = state.next_turn(new_messages)
            else:
                # No tool calls — check for output truncation recovery
                if response.finish_reason == "length" and state.output_recovery_count < 3:
                    continuation = {
                        "role": "user",
                        "content": "你的输出被截断了。请从中断处直接继续，不要重复已输出的内容。",
                    }
                    new_msgs = list(state.messages) + [
                        {"role": "assistant", "content": response.content or ""},
                        continuation,
                    ]
                    state = LoopState(
                        messages=tuple(new_msgs),
                        iteration=state.iteration,
                        transition_reason="output_continuation",
                        output_recovery_count=state.output_recovery_count + 1,
                        compact_attempted=state.compact_attempted,
                        total_compact_count=state.total_compact_count,
                        total_prompt_tokens=state.total_prompt_tokens,
                        total_completion_tokens=state.total_completion_tokens,
                    )
                    recovery_attempts["output_continuation"] = (
                        recovery_attempts.get("output_continuation", 0) + 1
                    )
                    continue

                exit_reason = (
                    ExitReason.OUTPUT_TRUNCATED
                    if response.finish_reason == "length"
                    else ExitReason.COMPLETED
                )
                rc = response.reasoning_content
                return self._build_result(
                    state,
                    exit_reason,
                    summary=response.content,
                    usage=usage,
                    events=events,
                    recovery=recovery_attempts,
                    reasoning_content=rc if isinstance(rc, str) and rc else None,
                )

        # Loop exhausted
        return self._build_result(
            state,
            ExitReason.MAX_ITERATIONS,
            usage=usage,
            events=events,
            recovery=recovery_attempts,
            error=f"Max iterations ({max_iterations}) reached",
        )

    def _build_result(
        self,
        state: LoopState,
        exit_reason: ExitReason,
        *,
        usage: UsageSummary | None = None,
        events: list[dict] | None = None,
        summary: str | None = None,
        error: str | None = None,
        recovery: dict | None = None,
        reasoning_content: str | None = None,
    ) -> AgentResult:
        if exit_reason == ExitReason.COMPLETED:
            status = TaskStatus.COMPLETED
        elif exit_reason == ExitReason.CANCELLED:
            status = TaskStatus.CANCELLED
        else:
            status = TaskStatus.FAILED
        # Update skill metrics based on exit reason
        try:
            from tianshu.tools.skill_tools import clear_active_skills, get_active_skills

            if self._metrics_store:
                active = get_active_skills()
                for skill_name in active:
                    if exit_reason == ExitReason.COMPLETED:
                        self._metrics_store.increment_success(skill_name)
                    else:
                        self._metrics_store.increment_failure(skill_name)
                clear_active_skills()
        except Exception:
            logger.debug("Skill metrics update failed", exc_info=True)

        return AgentResult(
            status=status,
            summary=summary,
            result=summary,
            usage=usage or UsageSummary(),
            error=error,
            events=events or [],
            exit_reason=exit_reason,
            iteration_count=state.iteration,
            compact_count=state.total_compact_count,
            recovery_attempts=recovery or {},
            reasoning_content=reasoning_content,
        )

    def _build_system_prompt(self, edict: Edict, skills_char_budget: int) -> str:
        parts = [_SYSTEM_IDENTITY]

        self._skills.set_char_budget(skills_char_budget)

        # Progressive loading: index + always-on skills
        index_text = self._skills.load_index(metrics_store=self._metrics_store)
        if index_text:
            parts.append(index_text)

        always_text = self._skills.load_always()
        if always_text:
            parts.append(always_text)

        parts.append(f"Current task ID: {edict.id}")
        return "\n\n".join(parts)

    @staticmethod
    def _accumulate_usage(total: UsageSummary, delta: UsageSummary) -> UsageSummary:
        # actual_model / upstream_provider 取最新非空值：
        # 多轮 tool round-trip 通常一致；fallback 切换模型时保留切后值更能反映实际消耗。
        return UsageSummary(
            prompt_tokens=total.prompt_tokens + delta.prompt_tokens,
            completion_tokens=total.completion_tokens + delta.completion_tokens,
            total_tokens=total.total_tokens + delta.total_tokens,
            cache_read_tokens=total.cache_read_tokens + delta.cache_read_tokens,
            cost_cny=total.cost_cny + delta.cost_cny,
            actual_model=delta.actual_model or total.actual_model,
            upstream_provider=delta.upstream_provider or total.upstream_provider,
        )


def _is_context_overflow(e: Exception) -> bool:
    # Prefer LiteLLM's typed exception for reliable detection
    if isinstance(e, litellm.ContextWindowExceededError):
        return True
    msg = str(e).lower()
    return any(k in msg for k in ("context_length", "prompt_too_long", "context window"))
