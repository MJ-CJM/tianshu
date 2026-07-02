"""Tool result types and hook protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolResult:
    """Structured result from a tool execution."""

    content: str
    details: dict[str, Any] | None = None
    is_error: bool = False


def ok_result(content: str, details: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(content=content, details=details)


def error_result(message: str) -> ToolResult:
    return ToolResult(content=message, is_error=True)


class ToolHook(Protocol):
    async def before_tool_call(self, name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Return modified args or None to keep original."""
        ...

    async def after_tool_call(
        self, name: str, args: dict[str, Any], result: ToolResult
    ) -> ToolResult | None:
        """Return modified result or None to keep original."""
        ...


class ToolTier(IntEnum):
    """工具权限 tier，数值越大越危险。

    与 PolicyEngine 协作：T0 直接快路径放行，T1+ 进入 hook chain
    由 PolicyEngine 决策。spec: Section 2 + 2026-04-21 web access。
    """

    T0_READONLY = 0  # 只读 / 无副作用
    T1_WORKSPACE = 1  # workspace 内写
    T2_NETWORK = 2  # 外部读（SSRF 风险）
    T3_WRITE = 3  # 外部写 / 可逆副作用（原 T2_WRITE）
    T4_DANGEROUS = 4  # 危险 / 不可逆（原 T3_DANGEROUS）
