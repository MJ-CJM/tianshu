"""Tool: list_dir — directory listing."""

from __future__ import annotations

import os
from pathlib import Path

from tianshu.executor.workspace_context import resolve_workspace_root
from tianshu.tools.path_utils import safe_path
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

_MAX_ENTRIES = 500


def register_list_dir(registry: ToolRegistry, workspace: Path) -> None:
    async def list_dir(path: str = ".") -> ToolResult:
        dir_path = safe_path(resolve_workspace_root(workspace), path)
        if not dir_path.is_dir():
            return error_result(f"Error: '{path}' is not a directory")

        try:
            entries = os.listdir(dir_path)
        except PermissionError:
            return error_result(f"Error: permission denied for '{path}'")

        # Classify and format entries
        lines: list[str] = []
        for name in sorted(entries, key=str.lower):
            entry_path = dir_path / name
            try:
                if entry_path.is_dir():
                    lines.append(name + "/")
                else:
                    lines.append(name)
            except OSError:
                lines.append(name)

        total = len(lines)
        truncated = total > _MAX_ENTRIES
        lines = lines[:_MAX_ENTRIES]
        listing = "\n".join(lines)

        return ok_result(
            listing,
            details={"entry_count": total, "truncated": truncated},
        )

    registry.register(
        "list_dir",
        list_dir,
        ToolDefinition(
            name="list_dir",
            description="List files and directories in the given path. Directories end with /.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to workspace (default: '.')",
                        "default": ".",
                    },
                },
            },
            tier=ToolTier.T0_READONLY.value,
        ),
    )
