"""Checks runner — 跑 bash / lint / rubric 三类指标。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from tianshu.executor.execution_gateway import (
    CommandGrant,
    EnvironmentPolicy,
    ExecutionDenied,
    ExecutionGateway,
    ExecutionStartError,
    SandboxRequirement,
    ShellCommand,
    get_execution_context,
    request_for_current_execution,
)
from tianshu.executor.orchestrator.state import CheckOutcome, ChecksResult
from tianshu.llm import LLMClient
from tianshu.models.acceptance import CheckSpec
from tianshu.security.clean_env import build_clean_env

logger = logging.getLogger(__name__)


class ChecksConfigError(Exception):
    """check 命令本身错（如 command not found），整个 outer loop 应 abort。"""


async def _run_bash(
    spec: CheckSpec,
    *,
    execution_gateway: ExecutionGateway,
    workspace_root: Path,
) -> CheckOutcome:
    if not spec.command:
        raise ChecksConfigError(f"check {spec.name}: kind=bash 需要 command 字段")
    context = get_execution_context()
    if context is None:
        raise ChecksConfigError(f"check {spec.name}: missing governed execution context")
    frozen = next(
        (
            check
            for check in context.effective_contract.acceptance.checks
            if check.name == spec.name and check.kind == spec.kind
        ),
        None,
    )
    if (
        frozen is None
        or frozen.command != spec.command
        or frozen.timeout_seconds != spec.timeout_seconds
    ):
        raise ChecksConfigError(f"check {spec.name}: command is not frozen in effective contract")
    start = time.monotonic()
    try:
        request = request_for_current_execution(
            purpose="acceptance",
            workspace_root=workspace_root,
            cwd=".",
            shell_command=ShellCommand(script=spec.command),
            environment=EnvironmentPolicy(allow_names=tuple(build_clean_env())),
            timeout_seconds=spec.timeout_seconds,
            stdout_limit_bytes=1000,
            stderr_limit_bytes=1000,
            sandbox=SandboxRequirement(
                trust_level="trusted-local",
                mode="host",
                allow_host=True,
            ),
            command_grant=CommandGrant.for_shell(
                spec.command,
                source="acceptance-contract",
            ),
        )
        execution = await execution_gateway.run(request)
    except (ExecutionDenied, ExecutionStartError) as exc:
        raise ChecksConfigError(f"check {spec.name}: execution denied: {exc}") from exc

    if execution.receipt.status == "timed_out":
        return CheckOutcome(
            name=spec.name,
            passed=False,
            detail=f"timeout after {spec.timeout_seconds}s",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    passed = execution.receipt.exit_code == 0
    detail = None
    if not passed:
        detail = (execution.stderr or execution.stdout)[:1000]
    return CheckOutcome(
        name=spec.name,
        passed=passed,
        detail=detail,
        duration_ms=duration_ms,
    )


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> dict:
    """从 LLM 输出中提取第一个 JSON object。允许前后有解释文字。"""
    if not text:
        raise ValueError("empty LLM output")
    # 先试整体解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 再退化到 regex 找 {...}
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise ValueError(f"no JSON object found in output: {text[:200]!r}")
    return json.loads(match.group(0))


async def _run_rubric(
    spec: CheckSpec,
    actor_output: str,
    llm: LLMClient,
) -> CheckOutcome:
    if not spec.rubric:
        raise ChecksConfigError(f"check {spec.name}: kind=rubric 需要 rubric 字段")
    prompt = (
        f"Rubric:\n{spec.rubric}\n\n"
        f"Output to evaluate:\n{actor_output}\n\n"
        f"Reply with JSON only (no extra text): "
        f'{{"score": <0.0-1.0 float>, "reasoning": "<short string>"}}'
    )
    start = time.monotonic()
    try:
        resp = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning("rubric LLM call failed for check %s: %s", spec.name, e)
        return CheckOutcome(
            name=spec.name,
            passed=False,
            detail=f"rubric LLM 调用失败: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    duration_ms = int((time.monotonic() - start) * 1000)
    try:
        data = _extract_json(resp.content or "")
        score = float(data.get("score", 0.0))
        reasoning = str(data.get("reasoning", ""))
    except (ValueError, json.JSONDecodeError, TypeError) as e:
        return CheckOutcome(
            name=spec.name,
            passed=False,
            detail=f"rubric LLM 输出解析失败: {e}",
            duration_ms=duration_ms,
        )
    return CheckOutcome(
        name=spec.name,
        passed=score >= spec.pass_threshold,
        score=score,
        detail=reasoning[:500],
        duration_ms=duration_ms,
    )


async def run_checks(
    specs: list[CheckSpec],
    actor_output: str,
    llm: LLMClient | None,
    *,
    execution_gateway: ExecutionGateway | None = None,
    workspace_root: Path | None = None,
) -> ChecksResult:
    """并发跑所有 checks，返回汇总。配置错（command not found / 字段缺）会冒泡。

    llm 仅在有 kind=rubric check 时才需要；否则可传 None。
    """
    if not specs:
        return ChecksResult(all_passed=True, outcomes=())

    async def _dispatch(spec: CheckSpec) -> CheckOutcome:
        if spec.kind in ("bash", "lint"):
            if execution_gateway is None or workspace_root is None:
                raise ChecksConfigError(
                    f"check {spec.name}: ExecutionGateway and workspace root are required"
                )
            return await _run_bash(
                spec,
                execution_gateway=execution_gateway,
                workspace_root=workspace_root,
            )
        if spec.kind == "rubric":
            if llm is None:
                raise ChecksConfigError(
                    f"check {spec.name}: kind=rubric 需要 LLMClient，但 llm=None"
                )
            return await _run_rubric(spec, actor_output, llm)
        raise ChecksConfigError(f"unknown check kind: {spec.kind}")

    outcomes = await asyncio.gather(*[_dispatch(s) for s in specs])
    all_passed = all(o.passed for o in outcomes)
    return ChecksResult(all_passed=all_passed, outcomes=tuple(outcomes))
