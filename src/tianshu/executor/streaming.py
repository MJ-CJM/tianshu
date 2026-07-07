"""Streaming callback for agent execution."""

from __future__ import annotations

from typing import Protocol

from tianshu.tools.types import ToolResult


class StreamCallback(Protocol):
    """Protocol for receiving streaming events from agent execution."""

    async def on_delta(self, text: str) -> None:
        """Called for each text token from the LLM."""
        ...

    async def on_tool_call_start(self, name: str) -> None:
        """Called when a tool execution begins."""
        ...

    async def on_tool_call_end(self, name: str, result: ToolResult) -> None:
        """Called when a tool execution completes."""
        ...
