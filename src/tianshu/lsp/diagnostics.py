"""basedpyright 诊断封装(迭代 5「执行 2.0」)。

尽调选型(roadmap P2-G):第一步直接 `basedpyright --outputjson`(pip 单依赖、内嵌
Node runtime、PyrightJsonResults schema 稳定)——edit 落盘后对改动文件跑 CLI,解析
generalDiagnostics 回灌 agent,让 agent 立即看到类型/语义错误。同时作为代码变体
位面的快速 fitness 信号(变体改完立刻拿类型级信号)。

默认关(TIANSHU_LSP_ENABLED)。诊断是增值反馈而非编辑前置，但启用后的不可用、
拒绝和超时会返回带 execution correlation 的结构化 advisory，不再静默消失。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ulid import ULID

from tianshu.executor import execution_gateway as process_boundary
from tianshu.security.clean_env import build_clean_env

if TYPE_CHECKING:
    from tianshu.executor.execution_gateway import ExecutionGateway

logger = logging.getLogger(__name__)

_SEVERITIES = ("error", "warning")


@dataclass(frozen=True)
class DiagnosticOutcome:
    status: Literal[
        "ok",
        "disabled",
        "not_applicable",
        "unavailable",
        "denied",
        "timed_out",
        "failed",
    ]
    diagnostics: tuple[dict, ...] = ()
    advisory: str | None = None
    correlation_id: str | None = None
    receipt: dict[str, Any] | None = None

    def advisory_details(self) -> dict[str, Any] | None:
        if self.advisory is None:
            return None
        details = {"status": self.status, "message": self.advisory}
        if self.correlation_id is not None:
            details["correlation_id"] = self.correlation_id
        if self.receipt is not None:
            details["receipt"] = self.receipt
        return details


def is_enabled() -> bool:
    return os.environ.get("TIANSHU_LSP_ENABLED", "").strip().lower() in ("1", "true", "on")


def _diagnostic_items(payload: object) -> list[dict] | None:
    if not isinstance(payload, dict):
        return None
    items = payload.get("generalDiagnostics", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        return None
    return items


def parse_diagnostics(output: str) -> list[dict]:
    """解析 basedpyright --outputjson 的 generalDiagnostics(可测,不依赖 CLI)。

    只保留 error/warning;行号从 0-based 转 1-based。坏 JSON 返回空。
    """
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return []
    items = _diagnostic_items(data)
    if items is None:
        return []
    diags: list[dict] = []
    for d in items:
        if d.get("severity") not in _SEVERITIES:
            continue
        raw_range = d.get("range")
        raw_start = raw_range.get("start") if isinstance(raw_range, dict) else None
        start = raw_start if isinstance(raw_start, dict) else {}
        try:
            line = int(start.get("line", 0)) + 1
        except (TypeError, ValueError):
            line = 1
        diags.append(
            {
                "line": line,
                "severity": d.get("severity", "error"),
                "message": d.get("message", ""),
                "rule": d.get("rule"),
            }
        )
    return diags


def format_diagnostics(diags: list[dict] | tuple[dict, ...]) -> str:
    """诊断列表 → agent 可读文本(拼进 edit_file 工具结果)。"""
    if not diags:
        return ""
    lines = [f"⚠️ basedpyright 诊断({len(diags)} 条,请据此修正):"]
    for d in diags[:20]:
        rule = f" [{d['rule']}]" if d.get("rule") else ""
        lines.append(f"  L{d['line']} {d['severity']}: {d['message']}{rule}")
    return "\n".join(lines)


def _correlation_id() -> str:
    context = process_boundary.get_execution_context()
    return context.correlation_id if context is not None else str(ULID())


def _advisory(
    status: Literal["unavailable", "denied", "timed_out", "failed"],
    message: str,
    correlation_id: str,
    receipt: process_boundary.ExecutionReceipt | None = None,
) -> DiagnosticOutcome:
    return DiagnosticOutcome(
        status=status,
        advisory=message,
        correlation_id=correlation_id,
        receipt=receipt.model_dump(mode="json") if receipt is not None else None,
    )


async def run_diagnostics_async(
    file_path: Path,
    *,
    execution_gateway: ExecutionGateway | None = None,
    workspace_root: Path | None = None,
) -> DiagnosticOutcome:
    """Run basedpyright through the governed async execution boundary."""
    correlation_id = _correlation_id()
    if not is_enabled():
        return DiagnosticOutcome(status="disabled", correlation_id=correlation_id)
    path = Path(file_path).resolve()
    if path.suffix != ".py":
        return DiagnosticOutcome(status="not_applicable", correlation_id=correlation_id)
    root = Path(workspace_root or path.parent).resolve()
    if not path.is_relative_to(root):
        return _advisory(
            "denied",
            "LSP target is outside the governed workspace",
            correlation_id,
        )
    exe = process_boundary.resolve_system_adapter_executable(
        "lsp",
        workspace_root=root,
    )
    if not exe:
        return _advisory(
            "unavailable",
            "basedpyright is not installed; diagnostics were not run",
            correlation_id,
        )
    command = (exe, "--outputjson", str(path))
    process_gateway = execution_gateway or process_boundary.ExecutionGateway()
    try:
        environment = process_boundary.EnvironmentPolicy(allow_names=tuple(build_clean_env("")))
        request = process_boundary.request_for_current_execution(
            purpose="lsp",
            workspace_root=root,
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
            command_grant=process_boundary.issue_lsp_command_grant(
                command,
                workspace_root=root,
                environment=environment,
            ),
        )
        execution = await process_gateway.run(request)
    except process_boundary.ExecutionDenied as exc:
        receipt = exc.receipt
        return _advisory(
            "denied",
            f"basedpyright execution was denied: {exc}",
            receipt.correlation_id if receipt is not None else correlation_id,
            receipt,
        )
    except process_boundary.ExecutionStartError as exc:
        return _advisory(
            "unavailable",
            f"basedpyright could not start: {exc}",
            exc.receipt.correlation_id,
            exc.receipt,
        )

    correlation_id = execution.receipt.correlation_id
    if execution.receipt.status == "timed_out":
        return _advisory(
            "timed_out",
            "basedpyright exceeded the 30 second diagnostic timeout",
            correlation_id,
            execution.receipt,
        )
    if execution.receipt.stdout_truncated or execution.receipt.stdout_incomplete:
        return _advisory(
            "failed",
            "basedpyright returned incomplete diagnostic output",
            correlation_id,
            execution.receipt,
        )
    try:
        payload = json.loads(execution.stdout)
    except (json.JSONDecodeError, TypeError):
        detail = execution.stderr.strip() or execution.error or "invalid JSON output"
        return _advisory(
            "failed",
            f"basedpyright diagnostics failed: {detail}",
            correlation_id,
            execution.receipt,
        )
    if _diagnostic_items(payload) is None:
        return _advisory(
            "failed",
            "basedpyright returned an invalid diagnostic JSON schema",
            correlation_id,
            execution.receipt,
        )
    if execution.returncode not in {0, 1}:
        detail = execution.stderr.strip() or execution.error or "unknown error"
        return _advisory(
            "failed",
            f"basedpyright diagnostics failed: {detail}",
            correlation_id,
            execution.receipt,
        )
    return DiagnosticOutcome(
        status="ok",
        diagnostics=tuple(parse_diagnostics(execution.stdout)),
        correlation_id=correlation_id,
        receipt=execution.receipt.model_dump(mode="json"),
    )


def run_diagnostics(
    file_path: Path,
    *,
    execution_gateway: ExecutionGateway | None = None,
    workspace_root: Path | None = None,
) -> DiagnosticOutcome:
    """Synchronous compatibility wrapper, forbidden inside an active event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            run_diagnostics_async(
                file_path,
                execution_gateway=execution_gateway,
                workspace_root=workspace_root,
            )
        )
    raise RuntimeError(
        "run_diagnostics cannot run inside an active event loop; await run_diagnostics_async"
    )
