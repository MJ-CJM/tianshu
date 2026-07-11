"""Tool: grep — content search (ripgrep preferred, Python fallback)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from tianshu.executor import execution_gateway as process_boundary
from tianshu.executor.workspace_context import resolve_workspace_root
from tianshu.security.clean_env import build_clean_env
from tianshu.tools.path_utils import safe_path
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
_MAX_LINE_LEN = 500
_MAX_OUTPUT_BYTES = 50_000

if TYPE_CHECKING:
    from tianshu.executor.execution_gateway import ExecutionGateway


def register_grep(
    registry: ToolRegistry,
    workspace: Path,
    *,
    execution_gateway: ExecutionGateway | None = None,
) -> None:
    process_gateway = execution_gateway or process_boundary.ExecutionGateway()

    async def grep(
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        ignore_case: bool = False,
        literal: bool = False,
        context: int = 0,
        limit: int = 100,
    ) -> ToolResult:
        active_workspace = resolve_workspace_root(workspace)
        search_path = safe_path(active_workspace, path)
        if not search_path.exists():
            return error_result(f"Error: path '{path}' does not exist")

        rg = process_boundary.resolve_system_adapter_executable(
            "grep",
            workspace_root=active_workspace,
        )
        context = max(0, min(context, 20))
        limit = max(1, min(limit, 1000))

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
                active_workspace,
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
            active_workspace,
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
        active_workspace: Path,
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
        cmd.extend(["--", pattern, str(search_path)])

        try:
            command = tuple(cmd)
            environment = process_boundary.EnvironmentPolicy(allow_names=tuple(build_clean_env("")))
            request = process_boundary.request_for_current_execution(
                purpose="grep",
                workspace_root=active_workspace,
                cwd=".",
                argv_command=process_boundary.ArgvCommand(argv=command),
                environment=environment,
                timeout_seconds=30,
                stdout_limit_bytes=1_000_000,
                stderr_limit_bytes=32_000,
                sandbox=process_boundary.SandboxRequirement(
                    trust_level="trusted-local",
                    mode="host",
                    allow_host=True,
                ),
                command_grant=process_boundary.issue_grep_command_grant(
                    command,
                    workspace_root=active_workspace,
                    environment=environment,
                ),
            )
            execution = await process_gateway.run(request)
        except process_boundary.ExecutionDenied as exc:
            if exc.guard == "network" and exc.code == "enforcement_unavailable":
                fallback = await asyncio.to_thread(
                    _python_search,
                    pattern,
                    search_path,
                    glob_pat,
                    ignore_case,
                    literal,
                    context,
                    limit,
                    active_workspace,
                )
                receipt = exc.receipt
                advisory: dict[str, object] = {
                    "status": "python_fallback",
                    "guard": exc.guard,
                    "code": exc.code,
                    "message": exc.detail,
                }
                if receipt is not None:
                    advisory["correlation_id"] = receipt.correlation_id
                    advisory["receipt"] = receipt.model_dump(mode="json")
                details = dict(fallback.details or {})
                details["execution_advisory"] = advisory
                return ToolResult(
                    content=fallback.content,
                    details=details,
                    is_error=fallback.is_error,
                )
            return error_result(f"grep: execution denied: {exc}")
        except process_boundary.ExecutionStartError as exc:
            return error_result(f"grep: unable to start ripgrep: {exc}")

        if execution.receipt.status == "timed_out":
            return error_result("grep: search timed out")
        if execution.receipt.stdout_truncated or execution.receipt.stdout_incomplete:
            return error_result("grep: search output was incomplete; narrow the path or pattern")
        if execution.returncode not in {0, 1}:
            detail = execution.stderr.strip() or execution.error or "ripgrep failed"
            return error_result(f"grep: {detail}")

        lines_out: list[str] = []
        match_count = 0
        for line in execution.stdout.splitlines():
            if match_count >= limit:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("type") != "match":
                continue
            data = msg["data"]
            file_path = data["path"]["text"]
            try:
                rel = os.path.relpath(file_path, active_workspace)
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
        active_workspace: Path,
    ) -> ToolResult:
        workspace_root = active_workspace.resolve()
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
                if fp.is_symlink() or not fp.resolve().is_relative_to(workspace_root):
                    continue
            except OSError:
                continue
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
                        "minimum": 1,
                        "maximum": 1000,
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
