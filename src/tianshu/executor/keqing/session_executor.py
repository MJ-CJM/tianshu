"""KeqingSessionExecutor —— pi RPC 会话档执行器(pi 客卿核心增量价值)。

与单发 KeqingExecutor 同外观(execute(edict,...) → AgentResult),但走 RPC 长连接:
spawn `pi --mode rpc` → stdin 写命令 → stdout 收事件流 → 结算只认 agent_settled →
跑 edict.acceptance 验收物 → 不合格同会话 follow_up 回灌整改(≤N 轮,上下文保留免重启)→
合格 get_session_stats 终账 → close_stdin 优雅收尾。

执行骨架照抄 tools/mcp/transport.py:_open_stdio(同一 gateway 上的双向 JSON-RPC 生产范例):
stdin_mode='pipe' + 独立 stdout_reader 持续消费(防 maxsize=8 背压死锁)+ wait 任务 +
cleanup 顺序(关写端→terminate→回收 reader)。process_backend 零改动。

治理沿用:issue_keqing_command_grant / clean-env(EnvironmentPolicy) / 出站脱敏 / 回执审计。
预算:P2 从 message_end usage 累加成本作**备份**熔断(触顶 terminate);硬熔断在 P3 网关 402。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from uuid import uuid4

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
from tianshu.executor.keqing.adapter import _as_int
from tianshu.executor.keqing.executor import _KEQING_ROOT, _USD_TO_CNY, parse_keqing_backend
from tianshu.executor.keqing.pi_adapter import PiSessionAdapter
from tianshu.executor.keqing.session import (
    KIND_MESSAGE,
    KIND_TOOL_END,
    KIND_TOOL_START,
    KeqingSessionAdapter,
)
from tianshu.executor.orchestrator.checks import ChecksResult, run_checks
from tianshu.executor.workspace_context import resolve_workspace_root
from tianshu.kernel.exit_reason import ExitReason
from tianshu.models import TaskStatus, UsageSummary
from tianshu.security.redact import redact_text

logger = logging.getLogger(__name__)

_SESSION_ADAPTERS: dict[str, type] = {"pi": PiSessionAdapter}
_STDIN_WRITE_LIMIT = 1024 * 1024  # pi 单条 RPC 消息上限(MCP 同款 1MB)
_MGMT_CMD_TIMEOUT = 10.0  # 管理类命令(get_session_stats)响应超时


def get_session_adapter(backend: str) -> KeqingSessionAdapter | None:
    cls = _SESSION_ADAPTERS.get(backend)
    return cls() if cls is not None else None


class _Accumulator:
    """会话运行的可变累加态(reader 写、主流程读)。"""

    def __init__(self) -> None:
        self.final_text: str = ""
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cost_usd: float | None = None
        self.tool_events: list[dict] = []
        self.is_error: bool = False
        self.error: str | None = None


class KeqingSessionExecutor:
    """把一个 edict 派给 pi RPC 会话客卿执行。签名对齐 KeqingExecutor.execute。"""

    def __init__(
        self,
        *,
        execution_gateway: ExecutionGateway | None = None,
        llm=None,
        follow_up_rounds: int = 3,
        gateway_base_url: str | None = None,
        token_ttl_seconds: float = 3600.0,
    ) -> None:
        self._execution_gateway = execution_gateway or ExecutionGateway()
        self._llm = llm  # 仅 acceptance 含 kind=rubric 时才需要
        self._follow_up_rounds = follow_up_rounds
        # 网关模式(OpenShell 凭证隔离):设了 base_url 则 spawn 前铸 scoped token 只注入
        # PI_GATEWAY_TOKEN,raw provider key 不进客卿 env;pi 侧由 P4 guard 的 registerProvider
        # 把 baseUrl 重定向到本 base_url + 用该 token。未设则直连档(auth_env_vars 放行 provider key)。
        self._gateway_base_url = gateway_base_url
        self._token_ttl = token_ttl_seconds

    def _build_environment(self, adapter, granted, edict, run_id: str):
        """构造客卿 env 策略 + 可选的 scoped token(网关模式)。

        返回 (EnvironmentPolicy, token_reset_handle|None, run_id_for_revoke|None)。
        """
        from tianshu.secrets.scoped_token import (
            get_scoped_token_store,
            set_current_run_token,
        )

        if self._gateway_base_url:
            allow = getattr(edict.runtime, "executor_model", None)
            model_allowlist = {allow} if allow else None
            budget_cny = getattr(edict.runtime, "cost_budget_cny", None)
            raw = get_scoped_token_store().mint(
                edict_id=edict.id,
                run_id=run_id,
                model_allowlist=model_allowlist,
                budget_cny=budget_cny,
                ttl_seconds=self._token_ttl,
            )
            handle = set_current_run_token(raw)
            # OpenShell:仅 PI_GATEWAY_TOKEN 走 secret_ref(→resolver→contextvar);raw key 一律不列
            env = EnvironmentPolicy(
                secret_refs=(
                    EnvironmentSecretRef(
                        env_name="PI_GATEWAY_TOKEN", ref="keqing-run:gateway-token"
                    ),
                )
            )
            return env, handle, run_id
        # 直连档:放行客卿自身的 provider 鉴权变量(仍受 contract.secret_refs 门控)
        env = EnvironmentPolicy(
            secret_refs=tuple(
                EnvironmentSecretRef(env_name=name, ref=name)
                for name in adapter.auth_env_vars
                if name in granted
            )
        )
        return env, None, None

    def work_dir(self, edict_id: str):
        return _KEQING_ROOT / edict_id

    async def execute(
        self,
        edict,
        memorial=None,
        on_event=None,
        model_override=None,
        **_ignored,
    ) -> AgentResult:
        backend = parse_keqing_backend(getattr(edict.runtime, "executor", None))
        adapter = get_session_adapter(backend) if backend else None
        if adapter is None:
            return AgentResult(
                status=TaskStatus.FAILED,
                error=f"no session adapter for keqing backend: {backend!r}",
                exit_reason=ExitReason.LLM_ERROR,
            )
        assert backend is not None

        work = resolve_workspace_root(self.work_dir(edict.id))
        work.mkdir(parents=True, exist_ok=True)
        session_dir = str(work / ".tianshu" / "sessions")
        model = model_override or getattr(edict.runtime, "executor_model", None)
        argv = adapter.build_session_argv(session_dir=session_dir, model=model)

        base_timeout = edict.runtime.timeout_seconds
        # 整场会话覆盖:基础时长 ×(1 + follow_up 轮数);受 budget.wall_clock_seconds 夹逼(request 内 min)
        session_timeout = base_timeout * (1 + self._follow_up_rounds)
        budget_cny = getattr(edict.runtime, "cost_budget_cny", None)

        context = get_execution_context()
        if context is None:
            return AgentResult(
                status=TaskStatus.FAILED,
                error="keqing session denied: missing governed execution context",
                exit_reason=ExitReason.LLM_ERROR,
            )
        granted = set(context.effective_contract.permissions.secret_refs)
        run_id = context.correlation_id
        environment, token_handle, token_run_id = self._build_environment(
            adapter, granted, edict, run_id
        )
        try:
            try:
                base_request = request_for_current_execution(
                    purpose="keqing",
                    workspace_root=work,
                    cwd=".",
                    argv_command=ArgvCommand(argv=tuple(argv)),
                    environment=environment,
                    timeout_seconds=session_timeout,
                    stdout_limit_bytes=8 * 1024 * 1024,
                    stderr_limit_bytes=256 * 1024,
                    sandbox=SandboxRequirement(
                        trust_level="trusted-local", mode="host", allow_host=True
                    ),
                    command_grant=issue_keqing_command_grant(
                        argv, backend=backend, workspace_root=work, environment=environment
                    ),
                )
                # 会话档需要双向管道(request_for_current_execution 默认 stdin_mode='null')
                request = base_request.model_copy(
                    update={"stdin_mode": "pipe", "stdin_write_limit_bytes": _STDIN_WRITE_LIMIT}
                )
                handle = await self._execution_gateway.start(request)
            except ExecutionDenied as exc:
                return AgentResult(
                    status=TaskStatus.FAILED,
                    error=f"keqing session denied: {exc}",
                    exit_reason=ExitReason.LLM_ERROR,
                )
            except ExecutionStartError:
                return AgentResult(
                    status=TaskStatus.FAILED,
                    error=(
                        f"pi CLI not found: {argv[0]!r}. Install pi "
                        "(@earendil-works/pi-coding-agent) or pick a different executor."
                    ),
                    exit_reason=ExitReason.LLM_ERROR,
                )
            return await self._drive(
                edict, adapter, handle, work, budget_cny, session_timeout, on_event
            )
        finally:
            self._revoke_run_token(token_handle, token_run_id)

    def _revoke_run_token(self, token_handle, token_run_id) -> None:
        """run 结束:即时吊销 scoped token + 清 contextvar(泄漏面 = 单次 run)。"""
        if token_handle is None and token_run_id is None:
            return
        from tianshu.secrets.scoped_token import (
            get_scoped_token_store,
            reset_current_run_token,
        )

        if token_run_id is not None:
            with suppress(Exception):
                get_scoped_token_store().revoke_run(token_run_id)
        if token_handle is not None:
            reset_current_run_token(token_handle)

    async def _drive(
        self, edict, adapter, handle, work, budget_cny, timeout, on_event
    ) -> AgentResult:
        acc = _Accumulator()
        settled = asyncio.Event()
        pending: dict[str, asyncio.Future] = {}
        loop = asyncio.get_running_loop()
        state = {"budget_hit": False, "spent_usd": 0.0}
        seq = iter(range(1, 1_000_000))

        def dispatch(frame: dict) -> None:
            if adapter.is_response(frame):
                fid = frame.get("id")
                fut = pending.pop(fid, None) if fid is not None else None
                if fut is not None and not fut.done():
                    fut.set_result(frame)
                return
            if frame.get("type") == "extension_ui_request":
                # P2 裸跑无 guard:反向通道请求一律 fail-closed 取消(P4 接批红裁决)。
                # 回复的 id 是**请求方 request 的 id**(rid),经 cmd_id 落进 "id" 字段。
                rid = frame.get("id")
                if rid is not None:
                    payload = adapter.encode_command(
                        "extension_ui_response", cmd_id=rid, cancelled=True
                    )
                    asyncio.create_task(_safe_write(handle, payload))
                return
            ev = adapter.parse_event(frame)
            if ev.kind == KIND_MESSAGE and ev.text:
                acc.final_text = ev.text
            elif ev.kind == KIND_TOOL_START:
                item = {"type": "tool.called", "tool": ev.tool_name or "?"}
                acc.tool_events.append(item)
                if on_event is not None:
                    on_event(item)
            elif ev.kind == KIND_TOOL_END and ev.is_error:
                acc.is_error = True
            # 备份预算熔断:从 message_end 原始 usage 累加成本
            if frame.get("type") == "message_end":
                msg = frame.get("message") or {}
                usage = msg.get("usage") or {} if isinstance(msg, dict) else {}
                acc.input_tokens += _as_int(usage.get("input"))
                acc.output_tokens += _as_int(usage.get("output"))
                c = (usage.get("cost") or {}).get("total")
                if isinstance(c, int | float):
                    state["spent_usd"] += float(c)
                    acc.cost_usd = state["spent_usd"]
                    if budget_cny and state["spent_usd"] * _USD_TO_CNY >= budget_cny:
                        state["budget_hit"] = True
                        settled.set()
            if adapter.is_settled(ev):
                settled.set()

        async def reader() -> None:
            buf = b""
            with suppress(Exception):
                async for chunk in handle.iter_stdout_bytes():
                    buf += chunk
                    parts = buf.split(b"\n")
                    buf = parts.pop()
                    for line in parts:
                        s = line.rstrip(b"\r").strip()
                        if not s:
                            continue
                        try:
                            dispatch(json.loads(s))
                        except json.JSONDecodeError:
                            continue
                tail = buf.rstrip(b"\r").strip()
                if tail:
                    with suppress(json.JSONDecodeError):
                        dispatch(json.loads(tail))

        reader_task = asyncio.create_task(reader(), name=f"pi-stdout-{edict.id}")
        wait_task = asyncio.create_task(handle.wait(), name=f"pi-wait-{edict.id}")
        exit_reason = ExitReason.COMPLETED
        err: str | None = None
        execution: ExecutionResult | None = None

        async def send(cmd_type, *, message=None, expect_response=False, **fields):
            cid = f"tianshu-{next(seq)}-{uuid4().hex[:8]}"
            fut = loop.create_future() if expect_response else None
            if fut is not None:
                pending[cid] = fut
            await handle.write_stdin(
                adapter.encode_command(cmd_type, cmd_id=cid, message=message, **fields)
            )
            return fut

        async def wait_settle() -> str:
            st = asyncio.ensure_future(settled.wait())
            done, _pending = await asyncio.wait(
                {st, wait_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
            if not st.done():
                st.cancel()
                with suppress(asyncio.CancelledError):
                    await st
            if st in done:
                return "settled"
            return "exited" if wait_task in done else "timeout"

        try:
            settled.clear()
            await send("prompt", message=edict.goal)
            outcome = await wait_settle()
            exit_reason, err = _classify_outcome(outcome, state, budget_cny)

            if exit_reason == ExitReason.COMPLETED:
                ok, acc_err = await self._acceptance_loop(
                    edict, adapter, acc, settled, send, wait_settle, work, state, budget_cny
                )
                if not ok and acc_err:
                    acc.is_error = True
                    err = acc_err
                    exit_reason, err = _classify_after_acceptance(state, budget_cny, err)

            if not state["budget_hit"] and exit_reason != ExitReason.TIMEOUT:
                await self._collect_stats(adapter, acc, send)

            with suppress(Exception):
                await handle.close_stdin()
        finally:
            execution = await self._cleanup(handle, wait_task, reader_task)

        return self._to_agent_result(acc, exit_reason, err, execution)

    async def _acceptance_loop(
        self, edict, adapter, acc, settled, send, wait_settle, work, state, budget_cny
    ) -> tuple[bool, str | None]:
        acceptance = getattr(edict, "acceptance", None)
        if not acceptance or not acceptance.checks:
            return True, None
        max_rounds = min(
            getattr(acceptance, "max_outer_iterations", self._follow_up_rounds) or 1,
            self._follow_up_rounds + 1,
        )
        for round_index in range(max_rounds):
            result: ChecksResult = await run_checks(
                list(acceptance.checks),
                acc.final_text or "",
                self._llm,
                execution_gateway=self._execution_gateway,
                workspace_root=work,
            )
            if result.all_passed:
                logger.info("[keqing-session] edict %s 验收通过(第 %d 轮)", edict.id, round_index + 1)
                return True, None
            if round_index == max_rounds - 1:
                break
            remediation = _format_remediation(result)
            logger.info(
                "[keqing-session] edict %s 验收未过,follow_up 回灌整改(第 %d 轮)",
                edict.id,
                round_index + 1,
            )
            settled.clear()
            await send("follow_up", message=remediation)
            if await wait_settle() != "settled":
                break
            if state["budget_hit"]:
                break
        return False, f"acceptance checks failed after {max_rounds} round(s)"

    async def _collect_stats(self, adapter, acc, send) -> None:
        fut = await send("get_session_stats", expect_response=True)
        if fut is None:
            return
        try:
            resp = await asyncio.wait_for(fut, _MGMT_CMD_TIMEOUT)
        except (TimeoutError, asyncio.TimeoutError):
            return
        if not resp.get("success"):
            return
        stats = adapter.extract_stats(resp.get("data") or {})
        # 会话聚合成本为权威口径(pi get_session_stats),覆盖逐条累加的备份值
        if stats.cost_usd is not None:
            acc.cost_usd = stats.cost_usd
        if stats.input_tokens:
            acc.input_tokens = stats.input_tokens
        if stats.output_tokens:
            acc.output_tokens = stats.output_tokens

    async def _cleanup(self, handle, wait_task, reader_task) -> ExecutionResult | None:
        # 顺序照抄 transport:先收口 wait,再 terminate 取回执,最后回收 reader。
        if not wait_task.done():
            wait_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await wait_task
        execution: ExecutionResult | None = None
        with suppress(Exception):
            execution = await handle.terminate()
        if not reader_task.done():
            with suppress(TimeoutError, asyncio.TimeoutError, Exception):
                await asyncio.wait_for(asyncio.shield(reader_task), timeout=1)
        if not reader_task.done():
            reader_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await reader_task
        return execution

    def _to_agent_result(self, acc, exit_reason, err, execution) -> AgentResult:
        cost_cny = round(acc.cost_usd * _USD_TO_CNY, 6) if acc.cost_usd is not None else 0.0
        usage = UsageSummary(
            prompt_tokens=acc.input_tokens,
            completion_tokens=acc.output_tokens,
            total_tokens=acc.input_tokens + acc.output_tokens,
            cost_cny=cost_cny,
        )
        failed = exit_reason != ExitReason.COMPLETED or acc.is_error
        final = redact_text(acc.final_text) if acc.final_text else acc.final_text
        return AgentResult(
            status=TaskStatus.FAILED if failed else TaskStatus.COMPLETED,
            result=final,
            summary=None,
            usage=usage,
            error=err or acc.error,
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


async def _safe_write(handle, payload: bytes) -> None:
    with suppress(Exception):
        await handle.write_stdin(payload)


def _classify_outcome(outcome: str, state: dict, budget_cny) -> tuple[ExitReason, str | None]:
    if state["budget_hit"]:
        return ExitReason.BUDGET_EXHAUSTED, f"keqing session budget exceeded (>{budget_cny} CNY)"
    if outcome == "settled":
        return ExitReason.COMPLETED, None
    if outcome == "timeout":
        return ExitReason.TIMEOUT, "pi session timed out before agent_settled"
    return ExitReason.LLM_ERROR, "pi session process exited before agent_settled"


def _classify_after_acceptance(state: dict, budget_cny, acc_err: str) -> tuple[ExitReason, str]:
    if state["budget_hit"]:
        return ExitReason.BUDGET_EXHAUSTED, f"keqing session budget exceeded (>{budget_cny} CNY)"
    return ExitReason.COMPLETED, acc_err


def _format_remediation(result: ChecksResult) -> str:
    failed = [o for o in result.outcomes if not o.passed]
    lines = [
        "验收未通过,请针对以下未达标项整改后继续(不要重头开始,在当前工作基础上修复):",
    ]
    for o in failed:
        detail = (o.detail or "").strip()
        lines.append(f"- [{o.name}] {detail[:800]}" if detail else f"- [{o.name}] 未通过")
    return "\n".join(lines)
