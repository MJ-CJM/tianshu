"""Tool: grep — content search (ripgrep preferred, Python fallback)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path

from tianshu.tools.path_utils import safe_path
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
_MAX_LINE_LEN = 500
_MAX_OUTPUT_BYTES = 50_000


def register_grep(registry: ToolRegistry, workspace: Path) -> None:
    async def grep(
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        ignore_case: bool = False,
        literal: bool = False,
        context: int = 0,
        limit: int = 100,
    ) -> ToolResult:
        search_path = safe_path(workspace, path)
        if not search_path.exists():
            return error_result(f"Error: path '{path}' does not exist")

        rg = shutil.which("rg")
        context = min(context, 20)

        if rg:
            return await _rg_search(
                rg,
                pattern,
                search_path,
                glob,
                ignore_case,
                literal,
                context,
                limit,
            )
        return await asyncio.to_thread(
            _python_search,
            pattern,
            search_path,
            glob,
            ignore_case,
            literal,
            context,
            limit,
        )

    async def _rg_search(
        rg: str,
        pattern: str,
        search_path: Path,
        glob_pat: str | None,
        ignore_case: bool,
        literal: bool,
        context: int,
        limit: int,
    ) -> ToolResult:
        cmd = [
            rg,
            "--json",
            "--line-number",
            "--color=never",
            "--hidden",
            f"--max-count={limit}",
        ]
        if ignore_case:
            cmd.append("-i")
        if literal:
            cmd.append("-F")
        if context > 0:
            cmd.extend(["-C", str(context)])
        if glob_pat:
            cmd.extend(["--glob", glob_pat])
        cmd.extend([pattern, str(search_path)])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.communicate()
            return error_result("grep: search timed out")

        lines_out: list[str] = []
        match_count = 0
        for line in stdout.decode(errors="replace").splitlines():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != "match":
                continue
            data = msg["data"]
            file_path = data["path"]["text"]
            try:
                rel = os.path.relpath(file_path, workspace)
            except ValueError:
                rel = file_path
            line_num = data["line_number"]
            text = data["lines"]["text"].rstrip("\n")[:_MAX_LINE_LEN]
            lines_out.append(f"{rel}:{line_num}: {text}")
            match_count += 1

        limit_reached = match_count >= limit
        output = "\n".join(lines_out)[:_MAX_OUTPUT_BYTES]
        return ok_result(
            output or "No matches found.",
            details={"match_count": match_count, "limit_reached": limit_reached},
        )

    def _python_search(
        pattern: str,
        search_path: Path,
        glob_pat: str | None,
        ignore_case: bool,
        literal: bool,
        context: int,
        limit: int,
    ) -> ToolResult:
        flags = re.IGNORECASE if ignore_case else 0
        if literal:
            compiled = re.compile(re.escape(pattern), flags)
        else:
            try:
                compiled = re.compile(pattern, flags)
            except re.error as e:
                return error_result(f"Invalid regex pattern: {e}")

        lines_out: list[str] = []
        match_count = 0

        def _matches_glob(file_path: Path) -> bool:
            if not glob_pat:
                return True
            return file_path.match(glob_pat)

        if search_path.is_file():
            files = [search_path]
        else:
            files = []
            for root, dirs, filenames in os.walk(search_path):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for fname in filenames:
                    fp = Path(root) / fname
                    if _matches_glob(fp):
                        files.append(fp)

        for fp in sorted(files):
            if match_count >= limit:
                break
            try:
                file_lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            except (OSError, UnicodeDecodeError):
                continue

            try:
                rel = os.path.relpath(
                    fp, search_path.parent if search_path.is_file() else search_path
                )
            except ValueError:
                rel = str(fp)

            for i, line in enumerate(file_lines):
                if match_count >= limit:
                    break
                if compiled.search(line):
                    # Context lines
                    start = max(0, i - context)
                    end = min(len(file_lines), i + context + 1)
                    for j in range(start, end):
                        prefix = ">" if j == i else " "
                        text = file_lines[j][:_MAX_LINE_LEN]
                        lines_out.append(f"{prefix} {rel}:{j + 1}: {text}")
                    match_count += 1

        limit_reached = match_count >= limit
        output = "\n".join(lines_out)[:_MAX_OUTPUT_BYTES]
        return ok_result(
            output or "No matches found.",
            details={"match_count": match_count, "limit_reached": limit_reached},
        )

    registry.register(
        "grep",
        grep,
        ToolDefinition(
            name="grep",
            description=(
                "Search file contents for a regex pattern. "
                "Uses ripgrep if available, otherwise falls back to Python re. "
                "Returns matching lines with file paths and line numbers."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to search in (default: '.')",
                        "default": ".",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Glob pattern to filter files (e.g. '*.py')",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "Case insensitive search",
                        "default": False,
                    },
                    "literal": {
                        "type": "boolean",
                        "description": "Treat pattern as literal string",
                        "default": False,
                    },
                    "context": {
                        "type": "integer",
                        "description": "Number of context lines around matches",
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matches (default: 100)",
                        "default": 100,
                    },
                },
                "required": ["pattern"],
            },
            tier=ToolTier.T0_READONLY.value,
            max_result_chars=12000,
        ),
    )
