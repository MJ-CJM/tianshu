"""KeqingExecutor(迭代 3.5)——drop-in AgentResult / clean-env / 预算熔断 / 脱敏。

用一个 fake CLI(python 脚本发 Claude Code 形状的 stream-json)替代真 claude/codex,
测试不依赖外部 CLI 安装。
"""

from __future__ import annotations

import math
import sys
from types import SimpleNamespace

import pytest

from tianshu.executor.capabilities import (
    get_executor_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import (
    ExecutionContext,
    bind_execution_context,
)
from tianshu.executor.keqing import KeqingExecutor, parse_keqing_backend
from tianshu.executor.keqing.adapter import ClaudeCodeAdapter
from tianshu.kernel.exit_reason import ExitReason
from tianshu.models import TaskStatus
from tianshu.models.governance_contract import (
    BudgetPolicyV1,
    ExecutorSelectionV1,
    NetworkPolicyV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind

_FAKE_CLI = r"""
import json, os, sys
# 回显能看到的 TIANSHU_ 变量以验证 clean-env;并发 Claude Code 形状的 result
leaked = [k for k in os.environ if k.startswith("TIANSHU_")]
print(json.dumps({"type":"assistant","message":{"content":[{"type":"tool_use","name":"bash"}]}}))
print(json.dumps({"type":"result","subtype":"success",
    "result": f"leaked={leaked}; my key is sk-abcdefghij0123456789xyz",
    "usage":{"input_tokens":200,"output_tokens":50},"total_cost_usd":0.02}))
"""


@pytest.fixture
def fake_cli(tmp_path):
    script = tmp_path / "claude"
    script.write_text(f"#!{sys.executable}\n{_FAKE_CLI.lstrip()}")
    script.chmod(0o700)
    return [str(script), "-p", "do the task", "--output-format", "stream-json", "--verbose"]


def _edict(executor="keqing:claude-code", timeout=30, budget=None):
    return SimpleNamespace(
        id="e1",
        goal="do the task",
        runtime=SimpleNamespace(executor=executor, timeout_seconds=timeout, cost_budget_cny=budget),
    )


async def _execute(keqing: KeqingExecutor, edict, **kwargs):
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal=edict.goal),
        executor=ExecutorSelectionV1(adapter_id=edict.runtime.executor),
        budget=BudgetPolicyV1(wall_clock_seconds=max(1, math.ceil(edict.runtime.timeout_seconds))),
        network=NetworkPolicyV1(mode="unrestricted_requested"),
    )
    effective = resolve_governance_contract(
        requested,
        get_executor_manifest(edict.runtime.executor),
        probe_host_capabilities(),
    )
    context = ExecutionContext(
        correlation_id="keqing-test",
        actor=Principal(
            id="keqing-test-principal",
            kind=PrincipalKind.SERVICE,
            display_name="Keqing Test",
        ),
        effective_contract=effective,
        workspace_lease_id="keqing-test-workspace",
    )
    with bind_execution_context(context):
        return await keqing.execute(edict, **kwargs)


class TestParseBackend:
    def test_parse(self):
        assert parse_keqing_backend("keqing:claude-code") == "claude-code"
        assert parse_keqing_backend("keqing:codex") == "codex"
        assert parse_keqing_backend("native") is None
        assert parse_keqing_backend(None) is None
        assert parse_keqing_backend("keqing:") is None


class TestKeqingExecutor:
    async def test_drop_in_agent_result(self, tmp_path, fake_cli, monkeypatch):
        monkeypatch.setattr(ClaudeCodeAdapter, "build_argv", lambda self, p, model=None: fake_cli)
        ke = KeqingExecutor(root=tmp_path / "kq")
        events: list[dict] = []
        res = await _execute(ke, _edict(), on_event=events.append)
        assert res.status == TaskStatus.COMPLETED
        assert res.usage.total_tokens == 250
        assert abs(res.usage.cost_cny - 0.02 * 7.2) < 1e-6
        # 工具事件入账本
        assert events and events[0]["tool"] == "bash"
        # 隔离工作区已建
        assert (tmp_path / "kq" / "e1").exists()

    async def test_clean_env_no_secret_leak(self, tmp_path, fake_cli, monkeypatch):
        monkeypatch.setenv("TIANSHU_LLM_API_KEY", "sk-should-not-leak")
        monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", "master-secret")
        monkeypatch.setattr(ClaudeCodeAdapter, "build_argv", lambda self, p, model=None: fake_cli)
        ke = KeqingExecutor(root=tmp_path / "kq")
        res = await _execute(ke, _edict())
        # fake CLI 回显 leaked=[] 证明 TIANSHU_* 没进子进程
        assert "leaked=[]" in res.result

    async def test_outbound_redaction(self, tmp_path, fake_cli, monkeypatch):
        monkeypatch.setattr(ClaudeCodeAdapter, "build_argv", lambda self, p, model=None: fake_cli)
        ke = KeqingExecutor(root=tmp_path / "kq")
        res = await _execute(ke, _edict())
        # 客卿回显的 key 被出站脱敏
        assert "sk-abcdefghij" not in res.result
        assert "[REDACTED API KEY]" in res.result

    async def test_outer_timeout_returns_explicit_failed_result(self, tmp_path, monkeypatch):
        sleeping_executable = tmp_path / "claude"
        sleeping_executable.write_text(f"#!{sys.executable}\nimport time; time.sleep(5)\n")
        sleeping_executable.chmod(0o700)
        sleeping_cli = [
            str(sleeping_executable),
            "-p",
            "sleep",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        monkeypatch.setattr(
            ClaudeCodeAdapter,
            "build_argv",
            lambda self, p, model=None: sleeping_cli,
        )
        ke = KeqingExecutor(root=tmp_path / "kq")

        res = await _execute(ke, _edict(timeout=0.01))

        assert res.status == TaskStatus.FAILED
        assert res.exit_reason == ExitReason.TIMEOUT
        assert res.error == "keqing timed out after 0.01s"

    async def test_unknown_backend_fails(self, tmp_path):
        ke = KeqingExecutor(root=tmp_path / "kq")
        res = await ke.execute(_edict(executor="keqing:nonexistent"))
        assert res.status == TaskStatus.FAILED
        assert "unknown keqing backend" in res.error

    async def test_executor_model_is_forwarded_to_cli_adapter(
        self,
        tmp_path,
        fake_cli,
        monkeypatch,
    ):
        seen = {}

        def build_argv(_self, _prompt, model=None):
            seen["model"] = model
            return fake_cli

        monkeypatch.setattr(ClaudeCodeAdapter, "build_argv", build_argv)
        edict = _edict()
        edict.runtime.executor_model = "claude-opus-4"

        result = await _execute(KeqingExecutor(root=tmp_path / "kq"), edict)

        assert result.status == TaskStatus.COMPLETED
        assert seen["model"] == "claude-opus-4"

    async def test_missing_cli_fails_gracefully(self, tmp_path, monkeypatch):
        missing_cli = [
            str(tmp_path / "claude"),
            "-p",
            "do the task",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        monkeypatch.setattr(
            ClaudeCodeAdapter,
            "build_argv",
            lambda self, p, model=None: missing_cli,
        )
        ke = KeqingExecutor(root=tmp_path / "kq")
        res = await _execute(ke, _edict())
        assert res.status == TaskStatus.FAILED
        assert "not found" in res.error
