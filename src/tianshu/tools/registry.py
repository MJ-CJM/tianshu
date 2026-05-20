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
    side_effect: bool = False  # True = modifies state; intercepted in winding_down phase


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[
            str, tuple[ToolDefinition, Callable[..., Awaitable[ToolResult]]]
        ] = {}
        self._hooks: list[ToolHook] = []
        self._disabled: set[str] = set()

    def register(
        self,
        name: str,
        func: Callable[..., Awaitable[ToolResult]],
        definition: ToolDefinition,
    ) -> None:
        self._tools[name] = (definition, func)

    def add_hook(self, hook: ToolHook) -> None:
        self._hooks.append(hook)

    def disable(self, name: str) -> None:
        self._disabled.add(name)

    def enable(self, name: str) -> None:
        self._disabled.discard(name)

    def is_disabled(self, name: str) -> bool:
        return name in self._disabled

    def list_disabled(self) -> set[str]:
        return set(self._disabled)

    def apply_disabled(self, names: "set[str] | list[str]") -> None:
        """批量应用禁用列表（startup 后从 DB 读回时调用）。"""
        self._disabled = set(names)

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
            if defn.name not in self._disabled
        ]

    def list_definitions(self) -> list[ToolDefinition]:
        """Return all registered ToolDefinition objects."""
        return [defn for defn, _ in self._tools.values()]

    def get_definition(self, name: str) -> ToolDefinition | None:
        """返回 name 对应的 ToolDefinition，未注册时返回 None。"""
        entry = self._tools.get(name)
        return entry[0] if entry else None

    async def execute(
        self, name: str, args: str | dict, lifecycle_phase: str = "active"
    ) -> ToolResult:
        # Spec Section 2: function-local import to avoid top-level circular dependency
        from tianshu.tools.types import ToolTier

        if name not in self._tools:
            return error_result(f"Error: tool '{name}' not found")

        if name in self._disabled:
            return error_result(f"Tool '{name}' is disabled by admin")

        defn, func = self._tools[name]

        # Spec Section 2: 未声明 tier 的工具 runtime 视为 T4_DANGEROUS
        if defn.tier is None or defn.tier not in (0, 1, 2, 3, 4):
            logger.error(
                "[TOOL] %s has invalid tier=%r, downgrading to T4_DANGEROUS",
                name, defn.tier,
            )
            # 动态覆盖这一次调用的 tier（不改 registry 里的定义，避免副作用）
            defn = defn.model_copy(update={"tier": ToolTier.T4_DANGEROUS.value})

        # winding_down gate: block side-effect tools when lifecycle is winding down
        if lifecycle_phase == "winding_down" and defn.side_effect:
            return ToolResult(
                content=(
                    f"工具 '{name}' 被 winding_down 阶段拦截：本任务已进入收尾阶段，"
                    f"不允许副作用工具调用。请改用只读工具完成总结/交接。"
                ),
                is_error=True,
            )

        try:
            if isinstance(args, str):
                args = json.loads(args)
        except json.JSONDecodeError as e:
            return error_result(f"Invalid JSON arguments: {e}")

        try:
            jsonschema.validate(instance=args, schema=defn.parameters)
        except jsonschema.ValidationError as e:
            return error_result(f"Parameter validation failed: {e.message}")

        # 过滤掉 schema 未声明的字段 —— 防 LLM 幻觉额外参数（例如基于训练数据
        # 想象 read_file 有 limit/offset），原生 Python 函数 strict 收 kwargs
        # 会抛 TypeError 让 LLM 死循环重试。jsonschema.validate 默认
        # additionalProperties=True，不会拦这种字段，所以这里手动过滤+warn。
        if isinstance(args, dict):
            declared = (defn.parameters or {}).get("properties", {})
            extra = [k for k in args if k not in declared]
            if extra:
                logger.warning(
                    "[TOOL] %s: dropping unexpected args %s "
                    "(LLM may be hallucinating params from training data)",
                    name, extra,
                )
                args = {k: v for k, v in args.items() if k in declared}

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
