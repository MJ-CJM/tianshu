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

# 个别 hook 场景需要更长的 wait —— 特别是 BEFORE_TOOL_CALL 里 PolicyHook 的
# wait_for_approval 最多等 300s。默认 5s 会把审批等待打断并放行（fail-open bug）。
HOOK_TIMEOUTS: dict[HookType, float] = {}  # 在 HookType 定义后填


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


# 审批最长等 300s，留 10s 缓冲给前后置逻辑
HOOK_TIMEOUTS[HookType.BEFORE_TOOL_CALL] = 310.0


# BEFORE_TOOL_CALL 的 hook 超时必须 fail-secure（拦住工具），否则等于绕过审批/policy
_FAIL_SECURE_HOOKS: frozenset[HookType] = frozenset({HookType.BEFORE_TOOL_CALL})


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
        self._hooks[hook_type] = [e for e in entries if e.handler is not handler]

    def set_event_writer(self, writer: object) -> None:
        """Set a storage reference for writing hook execution events."""
        self._event_writer = writer

    async def run(self, hook_type: HookType, **context: Any) -> HookResult:
        """Run all handlers for a hook type. Returns first blocking result or empty."""
        combined = HookResult()
        timeout = HOOK_TIMEOUTS.get(hook_type, HOOK_TIMEOUT)
        fail_secure = hook_type in _FAIL_SECURE_HOOKS
        for entry in self._hooks.get(hook_type, []):
            handler_name = entry.handler.__qualname__
            try:
                result = await asyncio.wait_for(entry.handler(**context), timeout=timeout)

                # Write hook execution event for frontend visibility (8.2)
                self._write_hook_event(
                    hook_type, handler_name, context, blocked=bool(result and result.block)
                )

                logger.debug(
                    "[HOOK] %s: handler=%s, blocked=%s",
                    hook_type.value,
                    handler_name,
                    bool(result and result.block),
                )
                if result and result.block:
                    return result
                if result and result.modified_args:
                    combined.modified_args = result.modified_args
            except TimeoutError:
                logger.warning(
                    "Hook %s timed out (>%.1fs) for %s",
                    handler_name,
                    timeout,
                    hook_type.value,
                )
                self._write_hook_event(hook_type, handler_name, context, error="timeout")
                if fail_secure:
                    # 安全关键 hook（BEFORE_TOOL_CALL）超时 → 拦截，避免工具被绕过审批/policy 跑
                    return HookResult(
                        block=True,
                        reason=f"hook {handler_name} timed out after {timeout:.0f}s (fail-secure)",
                    )
            except Exception:
                logger.exception(
                    "Hook %s failed for %s",
                    handler_name,
                    hook_type.value,
                )
                self._write_hook_event(hook_type, handler_name, context, error="exception")
                if fail_secure:
                    return HookResult(
                        block=True,
                        reason=f"hook {handler_name} raised exception (fail-secure)",
                    )
        return combined

    def _write_hook_event(
        self,
        hook_type: HookType,
        handler_name: str,
        context: dict,
        blocked: bool = False,
        error: str | None = None,
    ) -> None:
        """Write hook execution record to events table."""
        writer = getattr(self, "_event_writer", None)
        if not writer:
            return
        edict = context.get("edict")
        memorial = context.get("memorial")
        edict_id = getattr(edict, "id", None) if edict else None
        memorial_id = getattr(memorial, "id", None) if memorial else None
        if not edict_id:
            return
        try:
            writer.append_event(
                edict_id,
                memorial_id,
                f"hook.{hook_type.value}",
                {
                    "handler": handler_name,
                    "blocked": blocked,
                    "error": error,
                },
            )
        except Exception:
            logger.debug("Failed to persist hook audit event", exc_info=True)
