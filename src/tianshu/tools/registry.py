"""Tool registry and execution center."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict
    tier: int = 0  # T0-T3, Phase 0: label only, no runtime interception


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDefinition, Callable[..., Awaitable[str]]]] = {}

    def register(
        self,
        name: str,
        func: Callable[..., Awaitable[str]],
        definition: ToolDefinition,
    ) -> None:
        self._tools[name] = (definition, func)

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

    async def execute(self, name: str, args: str | dict) -> str:
        if name not in self._tools:
            return f"Error: tool '{name}' not found"
        _, func = self._tools[name]
        try:
            if isinstance(args, str):
                args = json.loads(args)
            return await func(**args)
        except Exception as e:
            logger.warning("Tool '%s' failed: %s", name, e)
            return f"Error executing {name}: {e}"
