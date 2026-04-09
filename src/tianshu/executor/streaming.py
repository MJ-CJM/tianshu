"""Streaming callback and cancellation token for agent execution."""

from __future__ import annotations

import asyncio
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


class CancellationToken:
    """Thread-safe cancellation signal for agent execution."""

    def __init__(self) -> None:
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    async def wait(self, timeout: float | None = None) -> bool:
        """Wait for cancellation. Returns True if cancelled."""
        try:
            await asyncio.wait_for(self._cancelled.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
