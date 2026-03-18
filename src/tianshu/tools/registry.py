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

    async def execute(self, name: str, args: str | dict) -> ToolResult:
        if name not in self._tools:
            return error_result(f"Error: tool '{name}' not found")

        defn, func = self._tools[name]

        try:
            if isinstance(args, str):
                args = json.loads(args)
        except json.JSONDecodeError as e:
            return error_result(f"Invalid JSON arguments: {e}")

        try:
            jsonschema.validate(instance=args, schema=defn.parameters)
        except jsonschema.ValidationError as e:
            return error_result(f"Parameter validation failed: {e.message}")

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

        # After hooks
        for hook in self._hooks:
            modified = await hook.after_tool_call(name, args, result)
            if modified is not None:
                result = modified

        return result
