"""KeqingSessionExecutor 驱动循环测试:用响应式 FakePiHandle 端到端验证
prompt→agent_settled→验收→follow_up 回灌→get_session_stats→优雅收尾的并发编排,
以及预算熔断、进程早退、验收耗尽等分支。无需真 pi 二进制。"""

import json
from contextlib import suppress
from types import SimpleNamespace

from tianshu.executor.keqing import session_executor as se_mod
from tianshu.executor.keqing.pi_adapter import PiSessionAdapter
from tianshu.executor.keqing.session_executor import KeqingSessionExecutor
from tianshu.executor.orchestrator.checks import CheckOutcome, ChecksResult
from tianshu.kernel.exit_reason import ExitReason
from tianshu.models import TaskStatus
from tianshu.models.acceptance import AcceptanceCriteria, CheckSpec


class FakePiHandle:
    """响应式假 pi RPC 进程:按 stdin 命令脚本化 emit LF-JSONL 事件到 stdout。"""

    def __init__(self, rounds, *, crash_after_prompt=False, reject_error=None, emit_settled=True):
        import asyncio

        self._rounds = rounds
        self._crash = crash_after_prompt
        self._reject_error = reject_error
        self._emit_settled = emit_settled  # False 模拟 pi 0.79.3(只发 agent_end,无 agent_settled)
        self._out: asyncio.Queue = asyncio.Queue()
        self._exited = asyncio.Event()
        self.stdin_cmds: list[dict] = []
        self._round = 0
        self._stats = {"tokens": {"input": 0, "output": 0}, "cost": 0.0}

    async def _emit(self, frame: dict) -> None:
        await self._out.put((json.dumps(frame) + "\n").encode())

    async def write_stdin(self, data: bytes) -> None:
        for raw in data.split(b"\n"):
            raw = raw.strip()
            if not raw:
                continue
            cmd = json.loads(raw)
            self.stdin_cmds.append(cmd)
            await self._react(cmd)

    async def _react(self, cmd: dict) -> None:
        t = cmd.get("type")
        if t in ("prompt", "follow_up"):
            if self._reject_error is not None:
                # 模拟客卿拒绝命令(如缺 provider key):失败 response,不发 agent_settled
                await self._emit(
                    {
                        "id": cmd.get("id"),
                        "type": "response",
                        "command": t,
                        "success": False,
                        "error": self._reject_error,
                    }
                )
                return
            await self._emit({"type": "agent_start"})
            if self._crash:
                # 模拟 pi 早退:发了 agent_start 就崩,不发 agent_settled
                await self._out.put(None)
                self._exited.set()
                return
            spec = self._rounds[min(self._round, len(self._rounds) - 1)]
            self._round += 1
            for tool in spec.get("tools", []):
                await self._emit({"type": "tool_execution_start", "toolName": tool})
                await self._emit({"type": "tool_execution_end", "toolName": tool, "isError": False})
            await self._emit(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": spec["text"]}],
                        "usage": {
                            "input": spec.get("input", 0),
                            "output": spec.get("output", 0),
                            "cost": {"total": spec.get("cost", 0.0)},
                        },
                    },
                }
            )
            self._stats["tokens"]["input"] += spec.get("input", 0)
            self._stats["tokens"]["output"] += spec.get("output", 0)
            self._stats["cost"] += spec.get("cost", 0.0)
            await self._emit({"type": "agent_end", "messages": [], "willRetry": False})
            if self._emit_settled:
                await self._emit({"type": "agent_settled"})
        elif t == "get_session_stats":
            tokens = {**self._stats["tokens"]}
            tokens["total"] = tokens["input"] + tokens["output"]
            await self._emit(
                {
                    "id": cmd.get("id"),
                    "type": "response",
                    "command": "get_session_stats",
                    "success": True,
                    "data": {"tokens": tokens, "cost": self._stats["cost"]},
                }
            )

    async def iter_stdout_bytes(self):
        while True:
            item = await self._out.get()
            if item is None:
                return
            yield item

    async def wait(self):
        await self._exited.wait()
        return None

    async def close_stdin(self):
        await self._out.put(None)
        self._exited.set()

    async def terminate(self):
        self._exited.set()
        with suppress(Exception):
            self._out.put_nowait(None)
        return None


def _edict(*, acceptance=None, goal="do X"):
    return SimpleNamespace(id="e-test", goal=goal, acceptance=acceptance)


def _outcome(name, passed, detail=""):
    return CheckOutcome(name=name, passed=passed, detail=detail, duration_ms=1)


async def _drive(handle, *, edict, budget_cny=None, timeout=30.0, llm=None, follow_up_rounds=3):
    ex = KeqingSessionExecutor(
        execution_gateway=object(), llm=llm, follow_up_rounds=follow_up_rounds
    )
    return await ex._drive(  # noqa: SLF001 - drive loop is the unit under test
        edict, PiSessionAdapter(), handle, tmp_work(), budget_cny, timeout, None
    )


def tmp_work():
    from pathlib import Path

    return Path("/tmp/keqing-session-test")  # 仅在有 acceptance bash check 时才被 run_checks 用


class TestHappyPath:
    async def test_prompt_settle_stats(self):
        handle = FakePiHandle([{"text": "done", "input": 100, "output": 20, "cost": 0.003}])
        result = await _drive(handle, edict=_edict())
        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert "done" in (result.result or "")
        # 成本来自 get_session_stats 聚合(权威口径),×7.2 汇率
        assert result.usage.cost_cny == round(0.003 * 7.2, 6)
        assert result.usage.total_tokens == 120
        # 发过 prompt 与 get_session_stats
        types = [c["type"] for c in handle.stdin_cmds]
        assert "prompt" in types and "get_session_stats" in types
        assert "follow_up" not in types

    async def test_agent_end_without_settled_completes(self):
        # pi 0.79.3 不发 agent_settled,以 agent_end(willRetry=False)为完成信号;天枢须据此
        # settle,否则 send prompt 后会一直等到 session timeout(前端「办理中」一直转)。
        handle = FakePiHandle(
            [{"text": "done", "input": 10, "output": 5, "cost": 0.001}], emit_settled=False
        )
        result = await _drive(handle, edict=_edict())
        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert "done" in (result.result or "")

    async def test_rejected_prompt_fails_fast_with_error(self):
        # 客卿拒绝命令(如缺 anthropic key):不再干等 timeout,快速 FAILED 且透出可操作原因
        handle = FakePiHandle([], reject_error="No API key found for anthropic.")
        result = await _drive(handle, edict=_edict(), timeout=30.0)
        assert result.status is TaskStatus.FAILED
        assert result.exit_reason is ExitReason.LLM_ERROR
        assert "No API key" in (result.error or "")


class TestAcceptanceLoop:
    async def test_fail_then_pass_via_follow_up(self, monkeypatch):
        calls = {"n": 0}

        async def fake_run_checks(specs, actor_output, llm, **kw):
            calls["n"] += 1
            passed = calls["n"] >= 2  # 第一轮不过,第二轮过
            return ChecksResult(
                all_passed=passed,
                outcomes=(_outcome("pytest", passed, "boom" if not passed else ""),),
            )

        monkeypatch.setattr(se_mod, "run_checks", fake_run_checks)
        acc = AcceptanceCriteria(
            checks=[CheckSpec(kind="bash", name="pytest", command="pytest")],
            max_outer_iterations=3,
        )
        handle = FakePiHandle([{"text": "attempt 1"}, {"text": "attempt 2 fixed"}])
        result = await _drive(handle, edict=_edict(acceptance=acc))
        assert result.status == TaskStatus.COMPLETED
        assert calls["n"] == 2  # 跑了两轮验收
        types = [c["type"] for c in handle.stdin_cmds]
        assert "follow_up" in types  # 第一轮不过 → 回灌整改
        assert "attempt 2 fixed" in (result.result or "")

    async def test_exhausts_marks_failed(self, monkeypatch):
        calls = {"n": 0}

        async def always_fail(specs, actor_output, llm, **kw):
            calls["n"] += 1
            return ChecksResult(
                all_passed=False, outcomes=(_outcome("pytest", False, "still broken"),)
            )

        monkeypatch.setattr(se_mod, "run_checks", always_fail)
        acc = AcceptanceCriteria(
            checks=[CheckSpec(kind="bash", name="pytest", command="pytest")],
            max_outer_iterations=2,
        )
        handle = FakePiHandle([{"text": "attempt"}])
        result = await _drive(handle, edict=_edict(acceptance=acc))
        assert result.status == TaskStatus.FAILED
        assert "acceptance" in (result.error or "").lower()
        # max_outer_iterations=2 → 跑 2 轮验收,发 1 次 follow_up
        assert calls["n"] == 2
        assert sum(1 for c in handle.stdin_cmds if c["type"] == "follow_up") == 1


class TestBudgetAndFailure:
    async def test_budget_hit_terminates(self):
        handle = FakePiHandle([{"text": "expensive", "cost": 1.0}])
        result = await _drive(handle, edict=_edict(), budget_cny=1.0)  # 1.0*7.2 >= 1.0 → 熔断
        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.BUDGET_EXHAUSTED

    async def test_process_exit_before_settle(self):
        handle = FakePiHandle([{"text": "never"}], crash_after_prompt=True)
        result = await _drive(handle, edict=_edict())
        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.LLM_ERROR
