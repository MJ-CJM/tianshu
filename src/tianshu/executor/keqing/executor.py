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
from pathlib import Path

from tianshu.executor.agent import AgentResult
from tianshu.executor.keqing.adapter import KeqingRunResult, get_adapter
from tianshu.kernel.exit_reason import ExitReason
from tianshu.models import TaskStatus, UsageSummary
from tianshu.security.clean_env import SAFE_ENV_VARS
from tianshu.security.redact import redact_text

logger = logging.getLogger(__name__)

_KEQING_ROOT = Path("~/.tianshu/keqing").expanduser()
_USD_TO_CNY = 7.2  # 与 cost/tracker.py 同款汇率


def parse_keqing_backend(executor: str | None) -> str | None:
    """`keqing:claude-code` → `claude-code`;非客卿 backend 返回 None。"""
    if executor and executor.startswith("keqing:"):
        return executor.split(":", 1)[1].strip() or None
    return None


def _keqing_env(auth_env_vars: tuple[str, ...]) -> dict[str, str]:
    """clean-env:安全白名单 + 客卿自身鉴权变量;不含 TIANSHU_* secrets。"""
    import os

    allowed = set(SAFE_ENV_VARS) | set(auth_env_vars)
    return {k: os.environ[k] for k in allowed if k in os.environ}


class KeqingExecutor:
    """把一个 edict 派给外部 CLI 客卿执行。签名对齐 Agent.execute 的关键参数。"""

    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root or _KEQING_ROOT

    def work_dir(self, edict_id: str) -> Path:
        return self._root / edict_id

    async def execute(
        self,
        edict,
        memorial=None,
        on_event=None,
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

        work = self.work_dir(edict.id)
        work.mkdir(parents=True, exist_ok=True)
        argv = adapter.build_argv(edict.goal, model=None)
        env = _keqing_env(adapter.auth_env_vars)
        timeout = edict.runtime.timeout_seconds
        budget_cny = getattr(edict.runtime, "cost_budget_cny", None)

        logger.info(
            "[keqing] dispatch edict %s → %s (cwd=%s, timeout=%ds)",
            edict.id,
            backend,
            work,
            timeout,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(work),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return AgentResult(
                status=TaskStatus.FAILED,
                error=(
                    f"keqing CLI not found: {argv[0]!r}. "
                    f"Install the {backend} CLI or pick a different executor."
                ),
                exit_reason=ExitReason.LLM_ERROR,
            )

        lines, exit_reason, err = await self._pump(proc, timeout, budget_cny, adapter, on_event)
        run = adapter.parse_stream(lines)
        return self._to_agent_result(run, exit_reason, err)

    async def _pump(self, proc, timeout, budget_cny, adapter, on_event):
        """读 stdout 行流:转发工具事件 + 逐行估成本触顶即杀;超时/取消收敛终态。"""
        lines: list[str] = []
        exit_reason = ExitReason.COMPLETED
        err: str | None = None
        seen_tools = 0
        try:

            async def _read() -> None:
                nonlocal seen_tools
                assert proc.stdout is not None
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    line = raw.decode(errors="replace")
                    lines.append(line)
                    # 逐行增量解析,转发新增工具事件到账本(客卿全程可审计)
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

            await asyncio.wait_for(_read(), timeout=timeout)
            await proc.wait()
        except _BudgetHit:
            exit_reason = ExitReason.BUDGET_EXHAUSTED
            err = f"keqing budget exceeded (>{budget_cny} CNY)"
            _kill(proc)
        except TimeoutError:
            exit_reason = ExitReason.TIMEOUT
            err = f"keqing timed out after {timeout}s"
            _kill(proc)
        except asyncio.CancelledError:
            _kill(proc)
            raise
        return lines, exit_reason, err

    def _to_agent_result(
        self, run: KeqingRunResult, exit_reason: ExitReason, err: str | None
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
        )


class _BudgetHit(Exception):
    pass


def _kill(proc) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
