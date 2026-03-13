"""Built-in tools: shell_exec, read_file, write_file."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from tianshu.tools.registry import ToolDefinition, ToolRegistry


def _safe_path(workspace: Path, path_str: str) -> Path:
    """Ensure path does not escape the workspace."""
    resolved = (workspace / path_str).resolve()
    workspace_prefix = str(workspace.resolve()) + os.sep
    if resolved != workspace.resolve() and not str(resolved).startswith(workspace_prefix):
        raise PermissionError(f"Path '{path_str}' is outside workspace")
    return resolved


def register_builtins(registry: ToolRegistry, workspace_dir: str) -> None:
    workspace = Path(workspace_dir).resolve()

    async def shell_exec(command: str, cwd: str | None = None) -> str:
        work_dir = workspace
        if cwd:
            work_dir = _safe_path(workspace, cwd)
            if not work_dir.is_dir():
                return f"Error: directory '{cwd}' does not exist"
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.communicate()
            raise
        output = stdout.decode(errors="replace")
        if stderr:
            output += "\nSTDERR:\n" + stderr.decode(errors="replace")
        return output[:2000]

    registry.register(
        "shell_exec",
        shell_exec,
        ToolDefinition(
            name="shell_exec",
            description="Execute a shell command in the workspace. Returns stdout and stderr.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory relative to workspace (optional)",
                    },
                },
                "required": ["command"],
            },
            tier=2,
        ),
    )

    async def read_file(path: str) -> str:
        file_path = _safe_path(workspace, path)
        if not file_path.is_file():
            return f"Error: file '{path}' does not exist"
        content = file_path.read_text(encoding="utf-8", errors="replace")
        return content[:10000]

    registry.register(
        "read_file",
        read_file,
        ToolDefinition(
            name="read_file",
            description="Read the contents of a file in the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace",
                    },
                },
                "required": ["path"],
            },
            tier=0,
        ),
    )

    async def write_file(path: str, content: str) -> str:
        file_path = _safe_path(workspace, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Successfully wrote {len(content)} characters to {path}"

    registry.register(
        "write_file",
        write_file,
        ToolDefinition(
            name="write_file",
            description="Write content to a file in the workspace. Creates parent directories if needed.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
            tier=1,
        ),
    )
