"""Tool registry and execution center."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

import jsonschema
from pydantic import BaseModel

from tianshu.models.side_effect import SideEffectSemantics
from tianshu.tools.types import ToolHook, ToolResult, error_result

logger = logging.getLogger(__name__)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict
    tier: int = 0  # T0-T3, Phase 0: label only, no runtime interception
    max_result_chars: int = 8000  # Per-tool result truncation limit
    side_effect: bool = False  # True = modifies state; intercepted in winding_down phase
    managed_effect_semantics: SideEffectSemantics | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDefinition, Callable[..., Awaitable[ToolResult]]]] = {}
        self._hooks: list[ToolHook] = []
        self._disabled: set[str] = set()
        # 锦衣卫·分级急停(迭代 3):非 None 时每次 execute 入口先过 check()
        self._estop: object | None = None
        self._managed_effect_executor: object | None = None
        self._managed_receipt_lookups: dict[str, Callable[[str], Awaitable[object | None]]] = {}

    def set_estop(self, estop: object) -> None:
        """注入 EstopManager(security.estop);置于 execute 最前,含 T0 快路径。"""
        self._estop = estop

    def set_managed_effect_executor(self, executor: object) -> None:
        """Install the production durable side-effect adapter."""

        self._managed_effect_executor = executor

    def set_managed_receipt_lookup(
        self,
        name: str,
        lookup: Callable[[str], Awaitable[object | None]],
    ) -> None:
        """Install the provider receipt lookup for one managed tool."""

        if name not in self._tools:
            raise ValueError(f"managed receipt lookup tool '{name}' is not registered")
        self._managed_receipt_lookups[name] = lookup

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

    def apply_disabled(self, names: set[str] | list[str]) -> None:
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
        self,
        name: str,
        args: str | dict,
        lifecycle_phase: str = "active",
        *,
        invocation_id: str | None = None,
    ) -> ToolResult:
        # Spec Section 2: function-local import to avoid top-level circular dependency
        from tianshu.tools.types import ToolTier

        if name not in self._tools:
            return error_result(f"Error: tool '{name}' not found")

        # 急停最优先:先于 disabled/tier/快路径,kill_all 下 T0 只读也拒
        if self._estop is not None:
            estop_reason = self._estop.check(name)  # type: ignore[attr-defined]
            if estop_reason:
                logger.warning("[TOOL] %s rejected by estop: %s", name, estop_reason)
                return error_result(f"Tool '{name}' rejected: {estop_reason}")

        if name in self._disabled:
            return error_result(f"Tool '{name}' is disabled by admin")

        defn, func = self._tools[name]

        from tianshu.executor.workspace_context import (
            WorkspaceBindingError,
            validate_current_workspace_binding,
        )

        try:
            bound_workspace = validate_current_workspace_binding()
        except WorkspaceBindingError as exc:
            return error_result(f"Tool '{name}' rejected: {exc}")
        from tianshu.tools.mcp.naming import is_mcp_tool

        if bound_workspace is not None and is_mcp_tool(name):
            return error_result(
                f"Tool '{name}' rejected: MCP tools are not isolated to the bound workspace"
            )

        # Spec Section 2: 未声明 tier 的工具 runtime 视为 T4_DANGEROUS
        if defn.tier is None or defn.tier not in (0, 1, 2, 3, 4):
            logger.error(
                "[TOOL] %s has invalid tier=%r, downgrading to T4_DANGEROUS",
                name,
                defn.tier,
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
        if not isinstance(args, dict):
            return error_result("Invalid arguments: expected a JSON object")

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
                    name,
                    extra,
                )
                args = {k: v for k, v in args.items() if k in declared}

        logger.debug(
            "[TOOL] execute: name=%s, tier=%d, args_keys=%s",
            name,
            defn.tier,
            list(args.keys()) if isinstance(args, dict) else "raw",
        )

        from tianshu.executor.managed_tools import get_managed_attempt_authority

        authority = get_managed_attempt_authority()
        if defn.side_effect and authority is not None:
            if self._managed_effect_executor is None:
                return error_result(
                    f"Tool '{name}' rejected: managed side-effect adapter is unavailable"
                )
            return await self._managed_effect_executor.execute(  # type: ignore[attr-defined]
                authority=authority,
                tool_name=name,
                arguments=args,
                invocation_id=invocation_id,
                semantics=defn.managed_effect_semantics,
                lookup_receipt=self._managed_receipt_lookups.get(name),
                invoke=lambda provider_key: self._invoke_bound(
                    name,
                    args,
                    defn,
                    func,
                    provider_key,
                    bound_workspace,
                ),
            )
        if defn.managed_effect_semantics is not None:
            return error_result(f"Tool '{name}' rejected: managed attempt authority is unavailable")
        return await self._invoke_bound(
            name,
            args,
            defn,
            func,
            invocation_id,
            bound_workspace,
        )

    async def _invoke_bound(
        self,
        name: str,
        args: dict,
        defn: ToolDefinition,
        func: Callable[..., Awaitable[ToolResult]],
        invocation_id: str | None,
        bound_workspace: object | None,
    ) -> ToolResult:
        from tianshu.kernel.ambient import bind_tool_invocation_id

        with bind_tool_invocation_id(invocation_id):
            if bound_workspace is not None:
                async with bound_workspace.tool_lock:  # type: ignore[attr-defined]
                    bound_workspace.validate_identity()  # type: ignore[attr-defined]
                    return await self._invoke(name, args, defn, func)
            return await self._invoke(name, args, defn, func)

    async def _invoke(
        self,
        name: str,
        args: dict,
        defn: ToolDefinition,
        func: Callable[..., Awaitable[ToolResult]],
    ) -> ToolResult:
        from tianshu.tools.types import ToolTier

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
            modified_args = await hook.before_tool_call(name, args)
            if modified_args is not None:
                args = modified_args

        try:
            result = await func(**args)
        except Exception as e:
            logger.exception("Tool '%s' raised an unexpected exception", name)
            return error_result(f"Error executing {name}: {e}")

        logger.debug(
            "[TOOL] result: name=%s, success=%s, output_len=%d",
            name,
            not result.is_error,
            len(result.content or ""),
        )

        # After hooks
        for hook in self._hooks:
            modified_result = await hook.after_tool_call(name, args, result)
            if modified_result is not None:
                result = modified_result

        return result
