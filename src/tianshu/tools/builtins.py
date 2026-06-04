"""Built-in tools: shell_exec, read_file, write_file."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from tianshu.executor.ambient import get_current_edict
from tianshu.storage import Storage
from tianshu.tools.hongluisi.engine_registry import build_engines
from tianshu.tools.hongluisi.tools import register_hongluisi
from tianshu.tools.path_utils import safe_path
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus
    from tianshu.persona.loader import PersonaLoader


def register_builtins(
    registry: ToolRegistry,
    workspace_dir: str,
    storage: "Storage | None" = None,
    event_bus: "EventBus | None" = None,
    persona_loader: "PersonaLoader | None" = None,
) -> None:
    workspace = Path(workspace_dir).resolve()

    async def shell_exec(command: str, cwd: str | None = None) -> ToolResult:
        work_dir = workspace
        if cwd:
            work_dir = safe_path(workspace, cwd)
            if not work_dir.is_dir():
                return error_result(f"Error: directory '{cwd}' does not exist")
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.CancelledError:
            proc.kill()
            await asyncio.shield(proc.communicate())
            raise
        except asyncio.TimeoutError:
            proc.kill()
            await asyncio.shield(proc.communicate())
            return error_result("shell_exec: command timed out after 60s")
        output = stdout.decode(errors="replace")
        if stderr:
            output += "\nSTDERR:\n" + stderr.decode(errors="replace")
        truncated = len(output) > 2000
        output = output[:2000]
        is_err = proc.returncode != 0
        return ToolResult(
            content=output,
            details={
                "exit_code": proc.returncode,
                "truncated": truncated,
            },
            is_error=is_err,
        )

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
            tier=ToolTier.T4_DANGEROUS.value,
            max_result_chars=16000,
            side_effect=True,
        ),
    )

    async def read_file(
        path: str,
        offset: int | None = None,
        limit: int | None = None,
    ) -> ToolResult:
        """Read a file. Supports line-based slicing (1-indexed offset).

        Behavior aligned with Anthropic's Read tool:
        - 默认整体读取，超 10000 字符截断
        - 提供 offset/limit 时按行切片：从 offset 行开始读 limit 行
          （offset=1 表示第一行；limit=None 表示读到结尾）
        """
        file_path = safe_path(workspace, path)
        if not file_path.is_file():
            return error_result(f"Error: file '{path}' does not exist")

        size = file_path.stat().st_size
        if offset is not None or limit is not None:
            # 行切片模式
            start = max(0, (offset or 1) - 1)  # 1-indexed → 0-indexed
            with file_path.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            total_lines = len(lines)
            end = min(total_lines, start + limit) if limit is not None else total_lines
            sliced = "".join(lines[start:end])
            truncated = len(sliced) > 10000
            content = sliced[:10000]
            return ok_result(
                content,
                details={
                    "size": size,
                    "truncated": truncated,
                    "lines_returned": max(0, end - start),
                    "total_lines": total_lines,
                    "offset": start + 1,
                },
            )

        # 整体读模式（向后兼容原行为）
        content = file_path.read_text(encoding="utf-8", errors="replace")
        truncated = len(content) > 10000
        content = content[:10000]
        return ok_result(
            content,
            details={"size": size, "truncated": truncated},
        )

    registry.register(
        "read_file",
        read_file,
        ToolDefinition(
            name="read_file",
            description=(
                "Read the contents of a file in the workspace. "
                "For large files, use offset (1-indexed start line) "
                "and limit (max lines to read) to slice."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Optional: 1-indexed line number to start reading from. "
                            "Default: read from the beginning."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "Optional: max number of lines to read starting at offset. "
                            "Default: read to end of file."
                        ),
                    },
                },
                "required": ["path"],
            },
            tier=ToolTier.T0_READONLY.value,
            max_result_chars=12000,
        ),
    )

    async def write_file(path: str, content: str) -> ToolResult:
        file_path = safe_path(workspace, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return ok_result(
            f"Successfully wrote {len(content)} characters to {path}",
        )

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
            tier=ToolTier.T1_WORKSPACE.value,
            side_effect=True,
        ),
    )

    # Register new tools
    from tianshu.tools.edit_file import register_edit_file
    from tianshu.tools.find_files import register_find_files
    from tianshu.tools.grep import register_grep
    from tianshu.tools.list_dir import register_list_dir

    register_edit_file(registry, workspace)
    register_list_dir(registry, workspace)
    register_grep(registry, workspace)
    register_find_files(registry, workspace)

    # === hongluisi: 对外网络工具 ===
    # 所有 engine 构造（fetch / search / api / extract）+ vault/store 初始化都收敛到 engine_registry；
    # 启动期 build_engines(storage) 把 storage 引用存进 registry，供后续 rebuild_engines() 热更凭证。
    build_engines(storage=storage)
    register_hongluisi(registry, edict_getter=get_current_edict)

    # 飞书 lark-cli 透传工具（写操作经 LarkCliSafetyRule 升级审批）
    from tianshu.tools.lark_cli import register_lark_cli
    register_lark_cli(registry)

    # === 敕令管理工具集：让助手 LLM 在对话中颁敕、查阅、追踪 ===
    # 默认注册到 registry，但通政司 enable_edict_submission toggle 控制启用；
    # submit_edict（写）、list_edicts / get_edict_status（读）作为同一捆绑能力。
    if storage is not None and event_bus is not None:
        from tianshu.tools.submit_edict import register_submit_edict
        register_submit_edict(
            registry,
            storage=storage,
            event_bus=event_bus,
            persona_loader=persona_loader,
        )
    if storage is not None:
        from tianshu.tools.edict_query import register_edict_query
        register_edict_query(registry, storage=storage)
