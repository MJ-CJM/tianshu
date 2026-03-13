"""Agent ReAct Loop - core execution engine."""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from tianshu.config import TianshuSettings
from tianshu.llm import LLMClient
from tianshu.models import Edict, TaskStatus, UsageSummary
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
        llm: LLMClient,
        tools: ToolRegistry,
        skills: SkillsLoader,
        settings: TianshuSettings,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._skills = skills
        self._settings = settings
        self._shutdown_event = asyncio.Event()

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    async def execute(self, edict: Edict) -> AgentResult:
        system_prompt = self._build_system_prompt(edict)
        user_content = edict.goal
        if edict.context:
            user_content += f"\n\nAdditional context: {edict.context}"

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        usage = UsageSummary()
        events: list[dict] = []

        for iteration in range(self._settings.agent_max_iterations):
            if self._shutdown_event.is_set():
                return AgentResult(
                    status=TaskStatus.CANCELLED,
                    error="Shutdown requested",
                    usage=usage,
                    events=events,
                )

            openai_tools = self._tools.get_openai_tools() or None
            response = await self._llm.chat(messages, tools=openai_tools)
            usage = self._accumulate_usage(usage, response.usage)

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
                    result = await self._tools.execute(tc["name"], tc["args"])
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": result[:500],  # Truncate per NanoBot pattern
                    })
                    events.append({
                        "type": "tool.completed",
                        "tool": tc["name"],
                        "iteration": iteration,
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
            error=f"Max iterations ({self._settings.agent_max_iterations}) reached",
            usage=usage,
            events=events,
        )

    def _build_system_prompt(self, edict: Edict) -> str:
        parts = [_SYSTEM_IDENTITY]

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
