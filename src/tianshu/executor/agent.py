"""Agent ReAct Loop - core execution engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

from pydantic import BaseModel, Field

from tianshu.config_manager import ConfigManager
from tianshu.executor.hooks import HookRegistry, HookResult, HookType
from tianshu.llm import LLMClient
from tianshu.models import Edict, TaskStatus, UsageSummary
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.registry import ToolRegistry

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


class Agent:
    def __init__(
        self,
        config_manager: ConfigManager,
        tools: ToolRegistry,
        skills: SkillsLoader,
        hook_registry: HookRegistry | None = None,
        prompt_builder: PromptBuilder | None = None,
        provider_manager: object | None = None,
    ) -> None:
        self._config_manager = config_manager
        self._tools = tools
        self._skills = skills
        self._hooks = hook_registry
        self._prompt_builder = prompt_builder
        self._provider_manager = provider_manager
        self._shutdown_event = asyncio.Event()

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def execute(
        self,
        edict: Edict,
        on_event: Callable[[dict], None] | None = None,
        history: list[dict] | None = None,
        user_content: str | None = None,
        tool_filter: list[str] | None = None,
        persona: object | None = None,
    ) -> AgentResult:
        # Read runtime config at execution start
        state = self._config_manager.state
        if not state.enabled:
            return AgentResult(
                status=TaskStatus.FAILED,
                error="LLM is currently disabled",
            )

        if self._provider_manager and hasattr(self._provider_manager, "get_client"):
            llm = self._provider_manager.get_client()
        else:
            llm = LLMClient(
                model=state.model,
                api_key=state.api_key,
                api_base=state.api_base,
                max_retries=state.max_retries,
                temperature=state.temperature,
                top_p=state.top_p,
                max_tokens=state.max_tokens,
            )

        agent_cfg = self._config_manager.agent_config

        # Use PromptBuilder if available, else fallback to legacy
        if self._prompt_builder:
            system_prompt = self._prompt_builder.build(
                edict,
                persona=persona,
                skills_char_budget=agent_cfg.skills_char_budget,
            )
        else:
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

        # Context window budget — estimate 4 chars per token, compact at 80%
        token_budget = edict.runtime.token_budget
        context_limit = token_budget if token_budget else 128000
        compact_threshold = int(context_limit * 0.8)

        for iteration in range(max_iterations):
            if self._shutdown_event.is_set():
                return AgentResult(
                    status=TaskStatus.CANCELLED,
                    error="Shutdown requested",
                    usage=usage,
                    events=events,
                )

            # Context compaction: estimate tokens and compress if near limit
            estimated_tokens = sum(len(str(m.get("content", ""))) // 4 for m in messages)
            if estimated_tokens > compact_threshold and len(messages) > 4:
                # Keep system prompt + last 4 messages, summarize middle
                preserved_head = messages[:1]  # system prompt
                preserved_tail = messages[-4:]  # recent context
                middle = messages[1:-4]
                if middle:
                    summary_parts = []
                    for m in middle:
                        role = m.get("role", "")
                        content = str(m.get("content", ""))[:200]
                        summary_parts.append(f"[{role}] {content}")
                    compacted_msg = {
                        "role": "system",
                        "content": (
                            "[Context compacted] Previous conversation summary:\n"
                            + "\n".join(summary_parts[-10:])
                        ),
                    }
                    messages = preserved_head + [compacted_msg] + preserved_tail
                    _emit({
                        "type": "context.compacted",
                        "iteration": iteration,
                        "original_messages": len(middle) + len(preserved_head) + len(preserved_tail),
                        "compacted_to": len(messages),
                    })

            _emit({"type": "iteration.started", "iteration": iteration})

            # Before iteration hook (budget check, audit, etc.)
            if self._hooks:
                iter_hook = await self._hooks.run(
                    HookType.BEFORE_ITERATION,
                    edict=edict,
                    iteration=iteration,
                    usage=usage,
                )
                if iter_hook.block:
                    return AgentResult(
                        status=TaskStatus.FAILED,
                        error=f"Blocked at iteration {iteration}: {iter_hook.reason}",
                        usage=usage,
                        events=events,
                    )

            openai_tools = self._tools.get_openai_tools() or None
            # Apply tool filter if specified (DAG node tool trimming)
            if tool_filter and openai_tools:
                openai_tools = [
                    t for t in openai_tools
                    if t.get("function", {}).get("name") in tool_filter
                ] or None

            # LLM input hook — can modify messages
            if self._hooks:
                input_hook = await self._hooks.run(
                    HookType.LLM_INPUT,
                    messages=messages,
                    edict=edict,
                    iteration=iteration,
                )
                if input_hook.modified_args and "messages" in input_hook.modified_args:
                    messages = input_hook.modified_args["messages"]

            response = await llm.chat(messages, tools=openai_tools)
            usage = self._accumulate_usage(usage, response.usage)

            # LLM output hook — includes usage for cost tracking
            if self._hooks:
                await self._hooks.run(
                    HookType.LLM_OUTPUT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                    iteration=iteration,
                    usage=response.usage,
                    edict=edict,
                    config_state=state,
                )

            if response.tool_calls:
                # Append assistant message with tool_calls
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
                messages.append(assistant_msg)

                # Execute each tool call sequentially
                for tc in response.tool_calls:
                    # Before tool call hook
                    if self._hooks:
                        hook_result = await self._hooks.run(
                            HookType.BEFORE_TOOL_CALL,
                            tool_name=tc["name"],
                            tool_args=tc["args"],
                            iteration=iteration,
                        )
                        if hook_result.block:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": f"Tool blocked: {hook_result.reason}",
                            })
                            _emit({
                                "type": "tool.blocked",
                                "tool": tc["name"],
                                "iteration": iteration,
                                "reason": hook_result.reason,
                            })
                            continue

                    tool_result = await self._tools.execute(tc["name"], tc["args"])
                    content = tool_result.content
                    if len(content) > 8000:
                        content = content[:8000] + "\n[... truncated]"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": content,
                    })

                    # After tool call hook
                    if self._hooks:
                        await self._hooks.run(
                            HookType.AFTER_TOOL_CALL,
                            tool_name=tc["name"],
                            tool_args=tc["args"],
                            tool_result=tool_result,
                            iteration=iteration,
                        )

                    args_str = tc["args"] if isinstance(tc["args"], str) else json.dumps(tc["args"])
                    _emit({
                        "type": "tool.failed" if tool_result.is_error else "tool.completed",
                        "tool": tc["name"],
                        "iteration": iteration,
                        "args_preview": args_str[:200],
                        "result_preview": tool_result.content[:200],
                        "is_error": tool_result.is_error,
                        "details": tool_result.details,
                    })
            else:
                # No tool calls = final answer
                return AgentResult(
                    status=TaskStatus.COMPLETED,
                    summary=response.content,
                    result=response.content,
                    usage=usage,
                    events=events,
                )

        return AgentResult(
            status=TaskStatus.FAILED,
            error=f"Max iterations ({max_iterations}) reached",
            usage=usage,
            events=events,
        )

    def _build_system_prompt(self, edict: Edict, skills_char_budget: int) -> str:
        parts = [_SYSTEM_IDENTITY]

        self._skills.set_char_budget(skills_char_budget)
        skills_text = self._skills.load_all()
        if skills_text:
            parts.append(skills_text)

        parts.append(f"Current task ID: {edict.id}")
        return "\n\n".join(parts)

    @staticmethod
    def _accumulate_usage(total: UsageSummary, delta: UsageSummary) -> UsageSummary:
        return UsageSummary(
            prompt_tokens=total.prompt_tokens + delta.prompt_tokens,
            completion_tokens=total.completion_tokens + delta.completion_tokens,
            total_tokens=total.total_tokens + delta.total_tokens,
        )
