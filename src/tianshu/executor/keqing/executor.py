"""KeqingExecutor —— 派客卿(外部 CLI)出工,产出 AgentResult 作 Agent 的 drop-in。

治理集成四件套(spec §四 v1):
1. **隔离工作区**:每个 edict 一个独立目录(``~/.tianshu/keqing/<edict_id>``),
   客卿改的是这里的文件,不碰主工作区;配合影子快照可一键回滚。
2. **clean-env**:白名单构造子进程 env——只放行客卿**自身**的鉴权变量
   (adapter.auth_env_vars),天枢的 TIANSHU_* secrets 一律不透传。
3. **预算熔断**:解析 stream-json 的 usage/cost 归因;超 cost_budget_cny 即杀进程。
4. **产出归一**:返回 AgentResult,后续照走 memorial → 审计 → 批红管线。

USD→CNY 按 tracker 同款 ×7.2(客卿 CLI 多自报 USD 成本)。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path

from tianshu.executor.agent import AgentResult
from tianshu.executor.execution_gateway import (
    ArgvCommand,
    EnvironmentPolicy,
    EnvironmentSecretRef,
    ExecutionDenied,
    ExecutionGateway,
    ExecutionResult,
    ExecutionStartError,
    SandboxRequirement,
    get_execution_context,
    issue_keqing_command_grant,
    request_for_current_execution,
)
from tianshu.executor.keqing.adapter import KeqingRunResult, get_adapter
from tianshu.executor.workspace_context import resolve_workspace_root
from tianshu.kernel.exit_reason import ExitReason
from tianshu.models import TaskStatus, UsageSummary
from tianshu.security.redact import redact_text

logger = logging.getLogger(__name__)

_KEQING_ROOT = Path("~/.tianshu/keqing").expanduser()
_USD_TO_CNY = 7.2  # 与 cost/tracker.py 同款汇率


def parse_keqing_backend(executor: str | None) -> str | None:
    """`keqing:claude-code` → `claude-code`;非客卿 backend 返回 None。"""
    if executor and executor.startswith("keqing:"):
        return executor.split(":", 1)[1].strip() or None
    return None


class KeqingExecutor:
    """把一个 edict 派给外部 CLI 客卿执行。签名对齐 Agent.execute 的关键参数。"""

    def __init__(
        self,
        *,
        root: Path | None = None,
        execution_gateway: ExecutionGateway | None = None,
        default_model_provider: Callable[[str], str | None] | None = None,
    ) -> None:
        self._root = root or _KEQING_ROOT
        self._execution_gateway = execution_gateway or ExecutionGateway()
        # 按客卿 backend 返回治理默认模型;敕令未指定 executor_model 时回退到它。
        self._default_model_provider = default_model_provider

    def work_dir(self, edict_id: str) -> Path:
        return self._root / edict_id

    async def execute(
        self,
        edict,
        memorial=None,
        on_event=None,
        model_override=None,
        **_ignored,
    ) -> AgentResult:
        backend = parse_keqing_backend(getattr(edict.runtime, "executor", None))
        adapter = get_adapter(backend) if backend else None
        if adapter is None:
            return AgentResult(
                status=TaskStatus.FAILED,
                error=f"unknown keqing backend: {backend!r}",
                exit_reason=ExitReason.LLM_ERROR,
            )
        assert backend is not None

        work = resolve_workspace_root(self.work_dir(edict.id))
        work.mkdir(parents=True, exist_ok=True)
        # 三级:敕令 executor_model > 治理默认[该客卿] > 客卿自身登录默认(model=None)
        model = model_override or getattr(edict.runtime, "executor_model", None)
        if not model and self._default_model_provider is not None:
            model = self._default_model_provider(backend)
        argv = adapter.build_argv(edict.goal, model=model)
        timeout = edict.runtime.timeout_seconds
        budget_cny = getattr(edict.runtime, "cost_budget_cny", None)

        logger.info(
            "[keqing] dispatch edict %s → %s (cwd=%s, timeout=%ds)",
            edict.id,
            backend,
            work,
            timeout,
        )
        context = get_execution_context()
        if context is None:
            return AgentResult(
                status=TaskStatus.FAILED,
                error="keqing execution denied: missing governed execution context",
                exit_reason=ExitReason.LLM_ERROR,
            )
        granted_secret_refs = set(context.effective_contract.permissions.secret_refs)
        environment = EnvironmentPolicy(
            secret_refs=tuple(
                EnvironmentSecretRef(env_name=name, ref=name)
                for name in adapter.auth_env_vars
                if name in granted_secret_refs
            )
        )
        try:
            request = request_for_current_execution(
                purpose="keqing",
                workspace_root=work,
                cwd=".",
                argv_command=ArgvCommand(argv=tuple(argv)),
                environment=environment,
                timeout_seconds=timeout,
                stdout_limit_bytes=4 * 1024 * 1024,
                stderr_limit_bytes=256 * 1024,
                sandbox=SandboxRequirement(
                    trust_level="trusted-local",
                    mode="host",
                    allow_host=True,
                ),
                command_grant=issue_keqing_command_grant(
                    argv,
                    backend=backend,
                    workspace_root=work,
                    environment=environment,
                ),
            )
            handle = await self._execution_gateway.start(request)
        except ExecutionDenied as exc:
            return AgentResult(
                status=TaskStatus.FAILED,
                error=f"keqing execution denied: {exc}",
                exit_reason=ExitReason.LLM_ERROR,
            )
        except ExecutionStartError:
            return AgentResult(
                status=TaskStatus.FAILED,
                error=(
                    f"keqing CLI not found: {argv[0]!r}. "
                    f"Install the {backend} CLI or pick a different executor."
                ),
                exit_reason=ExitReason.LLM_ERROR,
            )

        lines, exit_reason, err, execution = await self._pump(
            handle,
            timeout,
            budget_cny,
            adapter,
            on_event,
        )
        run = adapter.parse_stream(lines)
        return self._to_agent_result(run, exit_reason, err, execution)

    async def _pump(self, handle, timeout, budget_cny, adapter, on_event):
        """读 stdout 行流:转发工具事件 + 逐行估成本触顶即杀;超时/取消收敛终态。"""
        lines: list[str] = []
        exit_reason = ExitReason.COMPLETED
        err: str | None = None
        seen_tools = 0
        execution: ExecutionResult | None = None
        wait_task = asyncio.create_task(handle.wait())
        try:
            pending = ""
            async for chunk in handle.iter_stdout():
                pending += chunk
                complete = pending.splitlines(keepends=True)
                if complete and not complete[-1].endswith(("\n", "\r")):
                    pending = complete.pop()
                else:
                    pending = ""
                for line in complete:
                    lines.append(line)
                    if on_event is not None:
                        partial = adapter.parse_stream(lines)
                        for evt in partial.tool_events[seen_tools:]:
                            on_event(evt)
                        seen_tools = len(partial.tool_events)
                        # 预算熔断:CLI 自报成本触顶即杀
                        if (
                            budget_cny
                            and partial.cost_usd is not None
                            and partial.cost_usd * _USD_TO_CNY >= budget_cny
                        ):
                            raise _BudgetHit()
            if pending:
                lines.append(pending)
            execution = await wait_task
            if execution.receipt.status == "timed_out":
                exit_reason = ExitReason.TIMEOUT
                err = f"keqing timed out after {timeout}s"
            elif execution.receipt.status != "succeeded":
                exit_reason = ExitReason.LLM_ERROR
                err = execution.error or execution.stderr or "keqing CLI execution failed"
        except _BudgetHit:
            exit_reason = ExitReason.BUDGET_EXHAUSTED
            err = f"keqing budget exceeded (>{budget_cny} CNY)"
            wait_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await wait_task
            execution = await handle.wait()
        except asyncio.CancelledError:
            wait_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(wait_task)
            raise
        return lines, exit_reason, err, execution

    def _to_agent_result(
        self,
        run: KeqingRunResult,
        exit_reason: ExitReason,
        err: str | None,
        execution: ExecutionResult | None,
    ) -> AgentResult:
        cost_cny = round(run.cost_usd * _USD_TO_CNY, 6) if run.cost_usd is not None else 0.0
        usage = UsageSummary(
            prompt_tokens=run.input_tokens,
            completion_tokens=run.output_tokens,
            total_tokens=run.input_tokens + run.output_tokens,
            cost_cny=cost_cny,
        )
        failed = exit_reason != ExitReason.COMPLETED or run.is_error
        # 客卿产物出站脱敏(它可能在输出里回显读到的 secret)
        final = redact_text(run.final_text) if run.final_text else run.final_text
        return AgentResult(
            status=TaskStatus.FAILED if failed else TaskStatus.COMPLETED,
            result=final,
            summary=None,
            usage=usage,
            error=err or run.error,
            exit_reason=exit_reason,
            events=(
                [
                    {
                        "type": "execution.receipt",
                        "receipt": execution.receipt.model_dump(mode="json"),
                    }
                ]
                if execution is not None
                else []
            ),
        )


class _BudgetHit(Exception):
    pass
