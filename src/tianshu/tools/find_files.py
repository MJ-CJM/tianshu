"""Tool: find_files — file name search using pathlib.glob."""

from __future__ import annotations

import os
from pathlib import Path

from tianshu.tools.path_utils import safe_path
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, error_result, ok_result

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
_MAX_OUTPUT_BYTES = 50_000


def register_find_files(registry: ToolRegistry, workspace: Path) -> None:
    async def find_files(
        pattern: str, path: str = ".", limit: int = 1000
    ) -> ToolResult:
        search_path = safe_path(workspace, path)
        if not search_path.is_dir():
            return error_result(f"Error: '{path}' is not a directory")

        # Choose glob vs rglob based on pattern
        if "**" in pattern:
            matches_iter = search_path.glob(pattern)
        else:
            matches_iter = search_path.rglob(pattern)

        # Collect up to limit entries without materializing the full iterator
        results: list[str] = []
        for match in matches_iter:
            # Skip hidden/ignored directories
            parts = match.relative_to(search_path).parts
            if any(p in _SKIP_DIRS for p in parts):
                continue

            try:
                rel = os.path.relpath(match, search_path)
            except ValueError:
                rel = str(match)

            if match.is_dir():
                results.append(rel + "/")
            else:
                results.append(rel)

            if len(results) >= limit:
                break

        results.sort()

        limit_reached = len(results) >= limit
        output = "\n".join(results)[:_MAX_OUTPUT_BYTES]

        return ok_result(
            output or "No files found.",
            details={
                "result_count": len(results),
                "limit_reached": limit_reached,
            },
        )

    registry.register(
        "find_files",
        find_files,
        ToolDefinition(
            name="find_files",
            description=(
                "Search for files by name pattern using glob. "
                "Supports wildcards like '*.py', '**/*.ts'. "
                "Returns matching file paths relative to the search directory."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match file names (e.g. '*.py', '**/*.ts')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (default: '.')",
                        "default": ".",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 1000)",
                        "default": 1000,
                    },
                },
                "required": ["pattern"],
            },
            tier=0,
        ),
    )
