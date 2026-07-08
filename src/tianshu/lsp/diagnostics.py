"""basedpyright 诊断封装(迭代 5「执行 2.0」)。

尽调选型(roadmap P2-G):第一步直接 `basedpyright --outputjson`(pip 单依赖、内嵌
Node runtime、PyrightJsonResults schema 稳定)——edit 落盘后对改动文件跑 CLI,解析
generalDiagnostics 回灌 agent,让 agent 立即看到类型/语义错误。同时作为代码变体
位面的快速 fitness 信号(变体改完立刻拿类型级信号)。

默认关(TIANSHU_LSP_ENABLED)。basedpyright 未装/非 py 文件/超时一律优雅降级返回空,
不阻断 edit(诊断是增值反馈,不是编辑前置)。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_SEVERITIES = ("error", "warning")


def is_enabled() -> bool:
    return os.environ.get("TIANSHU_LSP_ENABLED", "").strip().lower() in ("1", "true", "on")


def parse_diagnostics(output: str) -> list[dict]:
    """解析 basedpyright --outputjson 的 generalDiagnostics(可测,不依赖 CLI)。

    只保留 error/warning;行号从 0-based 转 1-based。坏 JSON 返回空。
    """
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return []
    diags: list[dict] = []
    for d in data.get("generalDiagnostics", []):
        if d.get("severity") not in _SEVERITIES:
            continue
        start = (d.get("range") or {}).get("start") or {}
        diags.append(
            {
                "line": int(start.get("line", 0)) + 1,
                "severity": d.get("severity", "error"),
                "message": d.get("message", ""),
                "rule": d.get("rule"),
            }
        )
    return diags


def format_diagnostics(diags: list[dict]) -> str:
    """诊断列表 → agent 可读文本(拼进 edit_file 工具结果)。"""
    if not diags:
        return ""
    lines = [f"⚠️ basedpyright 诊断({len(diags)} 条,请据此修正):"]
    for d in diags[:20]:
        rule = f" [{d['rule']}]" if d.get("rule") else ""
        lines.append(f"  L{d['line']} {d['severity']}: {d['message']}{rule}")
    return "\n".join(lines)


def run_diagnostics(file_path: Path) -> list[dict]:
    """对一个 .py 文件跑 basedpyright;未启用/未装/非 py/超时一律返回空(降级)。"""
    if not is_enabled():
        return []
    path = Path(file_path)
    if path.suffix != ".py":
        return []
    exe = shutil.which("basedpyright")
    if not exe:
        logger.debug("[lsp] TIANSHU_LSP_ENABLED 已开但 basedpyright 未装,跳过")
        return []
    try:
        proc = subprocess.run(
            [exe, "--outputjson", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return parse_diagnostics(proc.stdout)
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("[lsp] basedpyright run failed: %s", e)
        return []
