"""Tool registry and execution center."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

import jsonschema
from pydantic import BaseModel

from tianshu.tools.types import ToolHook, ToolResult, error_result

logger = logging.getLogger(__name__)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict
    tier: int = 0  # T0-T3, Phase 0: label only, no runtime interception
    max_result_chars: int = 8000  # Per-tool result truncation limit


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[
            str, tuple[ToolDefinition, Callable[..., Awaitable[ToolResult]]]
        ] = {}
        self._hooks: list[ToolHook] = []

    def register(
        self,
        name: str,
        func: Callable[..., Awaitable[ToolResult]],
        definition: ToolDefinition,
    ) -> None:
        self._tools[name] = (definition, func)

    def add_hook(self, hook: ToolHook) -> None:
        self._hooks.append(hook)

    def get_openai_tools(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": defn.name,
                    "description": defn.description,
                    "parameters": defn.parameters,
                },
            }
            for defn, _ in self._tools.values()
        ]

    def list_definitions(self) -> list[ToolDefinition]:
        """Return all registered ToolDefinition objects."""
        return [defn for defn, _ in self._tools.values()]

    def get_definition(self, name: str) -> ToolDefinition | None:
        """返回 name 对应的 ToolDefinition，未注册时返回 None。"""
        entry = self._tools.get(name)
        return entry[0] if entry else None

    async def execute(self, name: str, args: str | dict) -> ToolResult:
        # Spec Section 2: function-local import to avoid top-level circular dependency
        from tianshu.tools.types import ToolTier

        if name not in self._tools:
            return error_result(f"Error: tool '{name}' not found")

        defn, func = self._tools[name]

        # Spec Section 2: 未声明 tier 的工具 runtime 视为 T3_DANGEROUS
        if defn.tier is None or defn.tier not in (0, 1, 2, 3):
            logger.error(
                "[TOOL] %s has invalid tier=%r, downgrading to T3_DANGEROUS",
                name, defn.tier,
            )
            # 动态覆盖这一次调用的 tier（不改 registry 里的定义，避免副作用）
            defn = defn.model_copy(update={"tier": ToolTier.T3_DANGEROUS.value})

        try:
            if isinstance(args, str):
                args = json.loads(args)
        except json.JSONDecodeError as e:
            return error_result(f"Invalid JSON arguments: {e}")

        try:
            jsonschema.validate(instance=args, schema=defn.parameters)
        except jsonschema.ValidationError as e:
            return error_result(f"Parameter validation failed: {e.message}")

        logger.debug(
            "[TOOL] execute: name=%s, tier=%d, args_keys=%s",
            name, defn.tier, list(args.keys()) if isinstance(args, dict) else "raw",
        )

        # Spec Section 2: T0_READONLY 工具走快路径 — 跳过 _hooks 链
        # 仍然 validate schema + 日志，但不经过 ToolHook 的 before/after 回调。
        if defn.tier == ToolTier.T0_READONLY:
            logger.debug("[TOOL] fast-path T0: name=%s", name)
            try:
                return await func(**args)
            except Exception as e:
                logger.exception("Tool '%s' raised in fast path", name)
                return error_result(f"Error executing {name}: {e}")

        # Before hooks
        for hook in self._hooks:
            modified = await hook.before_tool_call(name, args)
            if modified is not None:
                args = modified

        try:
            result = await func(**args)
        except Exception as e:
            logger.exception("Tool '%s' raised an unexpected exception", name)
            return error_result(f"Error executing {name}: {e}")

        logger.debug(
            "[TOOL] result: name=%s, success=%s, output_len=%d",
            name, not result.is_error, len(result.content or ""),
        )

        # After hooks
        for hook in self._hooks:
            modified = await hook.after_tool_call(name, args, result)
            if modified is not None:
                result = modified

        return result
