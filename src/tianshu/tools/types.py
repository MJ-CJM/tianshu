"""Tool result types and hook protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolResult:
    """Structured result from a tool execution."""

    content: str
    details: dict[str, Any] | None = None
    is_error: bool = False


def ok_result(
    content: str, details: dict[str, Any] | None = None
) -> ToolResult:
    return ToolResult(content=content, details=details)


def error_result(message: str) -> ToolResult:
    return ToolResult(content=message, is_error=True)


class ToolHook(Protocol):
    async def before_tool_call(
        self, name: str, args: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return modified args or None to keep original."""
        ...

    async def after_tool_call(
        self, name: str, args: dict[str, Any], result: ToolResult
    ) -> ToolResult | None:
        """Return modified result or None to keep original."""
        ...
