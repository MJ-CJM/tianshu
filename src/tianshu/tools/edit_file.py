"""Tool: edit_file — precise text replacement with unified diff."""

from __future__ import annotations

import difflib
from pathlib import Path

from tianshu.tools.path_utils import safe_path
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result


def register_edit_file(registry: ToolRegistry, workspace: Path) -> None:
    async def edit_file(path: str, old_text: str, new_text: str) -> ToolResult:
        file_path = safe_path(workspace, path)
        if not file_path.is_file():
            return error_result(f"Error: file '{path}' does not exist")

        # Read in binary to preserve original line endings
        raw = file_path.read_bytes()
        original_ending = b"\r\n" if b"\r\n" in raw else b"\n"
        content = raw.decode("utf-8", errors="replace")

        # Normalize to LF for matching
        normalized = content.replace("\r\n", "\n")
        old_normalized = old_text.replace("\r\n", "\n")
        new_normalized = new_text.replace("\r\n", "\n")

        count = normalized.count(old_normalized)
        if count == 0:
            return error_result(f"Error: old_text not found in '{path}'")
        if count > 1:
            return error_result(
                f"Error: old_text matches {count} locations in '{path}'. "
                "Provide more context to make the match unique."
            )

        new_content = normalized.replace(old_normalized, new_normalized, 1)

        # Generate diff
        old_lines = normalized.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = "".join(difflib.unified_diff(old_lines, new_lines, fromfile=path, tofile=path))

        # Find first changed line number
        first_changed_line = 1
        for i, (a, b) in enumerate(
            zip(
                normalized.splitlines(),
                new_content.splitlines(),
                strict=False,
            )
        ):
            if a != b:
                first_changed_line = i + 1
                break

        # Restore original line endings and write back
        if original_ending == b"\r\n":
            new_content = new_content.replace("\n", "\r\n")
        file_path.write_text(new_content, encoding="utf-8")

        # LSP 诊断(迭代 5):编辑 .py 落盘即跑 basedpyright,类型/语义错误回灌 agent。
        # 默认关 + 优雅降级(未装/非 py/超时返回空),不阻断编辑。
        from tianshu.lsp.diagnostics import format_diagnostics, run_diagnostics

        diags = run_diagnostics(file_path)
        details: dict = {"diff": diff, "first_changed_line": first_changed_line}
        content = f"Edited {path}"
        if diags:
            details["diagnostics"] = diags
            content = f"{content}\n\n{format_diagnostics(diags)}"
        return ok_result(content, details=details)

    registry.register(
        "edit_file",
        edit_file,
        ToolDefinition(
            name="edit_file",
            description=(
                "Replace an exact occurrence of old_text with new_text in a file. "
                "old_text must match exactly one location. Returns a unified diff."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "The exact text to find and replace",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The replacement text",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
            tier=ToolTier.T1_WORKSPACE.value,
            side_effect=True,
        ),
    )
