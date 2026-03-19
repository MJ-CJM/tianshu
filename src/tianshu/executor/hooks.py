"""Agent lifecycle hooks — before/after interception points."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

HOOK_TIMEOUT = 5.0


class HookType(str, Enum):
    BEFORE_AGENT_START = "before_agent_start"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    AGENT_END = "agent_end"
    BEFORE_ITERATION = "before_iteration"
    BEFORE_COMPACTION = "before_compaction"
    SESSION_START = "session_start"
    SESSION_END = "session_end"


class HookResult(BaseModel):
    block: bool = False
    reason: str | None = None
    modified_args: dict[str, Any] | None = None


HookHandler = Callable[..., Coroutine[Any, Any, HookResult | None]]


class _HookEntry:
    __slots__ = ("handler", "priority")

    def __init__(self, handler: HookHandler, priority: int) -> None:
        self.handler = handler
        self.priority = priority


class HookRegistry:
    """Registry for agent lifecycle hooks with priority ordering."""

    def __init__(self) -> None:
        self._hooks: dict[HookType, list[_HookEntry]] = defaultdict(list)

    def register(
        self,
        hook_type: HookType,
        handler: HookHandler,
        priority: int = 100,
    ) -> None:
        entries = self._hooks[hook_type]
        entries.append(_HookEntry(handler, priority))
        entries.sort(key=lambda e: e.priority)

    def unregister(self, hook_type: HookType, handler: HookHandler) -> None:
        entries = self._hooks.get(hook_type, [])
        self._hooks[hook_type] = [
            e for e in entries if e.handler is not handler
        ]

    async def run(self, hook_type: HookType, **context: Any) -> HookResult:
        """Run all handlers for a hook type. Returns first blocking result or empty."""
        combined = HookResult()
        for entry in self._hooks.get(hook_type, []):
            try:
                result = await asyncio.wait_for(
                    entry.handler(**context), timeout=HOOK_TIMEOUT
                )
                if result and result.block:
                    return result
                if result and result.modified_args:
                    combined.modified_args = result.modified_args
            except asyncio.TimeoutError:
                logger.warning(
                    "Hook %s timed out for %s",
                    entry.handler.__qualname__,
                    hook_type.value,
                )
            except Exception:
                logger.exception(
                    "Hook %s failed for %s",
                    entry.handler.__qualname__,
                    hook_type.value,
                )
        return combined
