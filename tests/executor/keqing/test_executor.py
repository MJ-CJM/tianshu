"""KeqingExecutor(迭代 3.5)——drop-in AgentResult / clean-env / 预算熔断 / 脱敏。

用一个 fake CLI(python 脚本发 Claude Code 形状的 stream-json)替代真 claude/codex,
测试不依赖外部 CLI 安装。
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from tianshu.executor.keqing import KeqingExecutor, parse_keqing_backend
from tianshu.executor.keqing.adapter import ClaudeCodeAdapter
from tianshu.kernel.exit_reason import ExitReason
from tianshu.models import TaskStatus

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
    script = tmp_path / "fake_cli.py"
    script.write_text(_FAKE_CLI)
    return [sys.executable, str(script)]


def _edict(executor="keqing:claude-code", timeout=30, budget=None):
    return SimpleNamespace(
        id="e1",
        goal="do the task",
        runtime=SimpleNamespace(executor=executor, timeout_seconds=timeout, cost_budget_cny=budget),
    )


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
        res = await ke.execute(_edict(), on_event=events.append)
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
        res = await ke.execute(_edict())
        # fake CLI 回显 leaked=[] 证明 TIANSHU_* 没进子进程
        assert "leaked=[]" in res.result

    async def test_outbound_redaction(self, tmp_path, fake_cli, monkeypatch):
        monkeypatch.setattr(ClaudeCodeAdapter, "build_argv", lambda self, p, model=None: fake_cli)
        ke = KeqingExecutor(root=tmp_path / "kq")
        res = await ke.execute(_edict())
        # 客卿回显的 key 被出站脱敏
        assert "sk-abcdefghij" not in res.result
        assert "[REDACTED API KEY]" in res.result

    async def test_outer_timeout_returns_explicit_failed_result(self, tmp_path, monkeypatch):
        sleeping_cli = [sys.executable, "-c", "import time; time.sleep(5)"]
        monkeypatch.setattr(
            ClaudeCodeAdapter,
            "build_argv",
            lambda self, p, model=None: sleeping_cli,
        )
        ke = KeqingExecutor(root=tmp_path / "kq")

        res = await ke.execute(_edict(timeout=0.01))

        assert res.status == TaskStatus.FAILED
        assert res.exit_reason == ExitReason.TIMEOUT
        assert res.error == "keqing timed out after 0.01s"

    async def test_unknown_backend_fails(self, tmp_path):
        ke = KeqingExecutor(root=tmp_path / "kq")
        res = await ke.execute(_edict(executor="keqing:nonexistent"))
        assert res.status == TaskStatus.FAILED
        assert "unknown keqing backend" in res.error

    async def test_missing_cli_fails_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            ClaudeCodeAdapter,
            "build_argv",
            lambda self, p, model=None: ["definitely-not-a-real-cli-xyz"],
        )
        ke = KeqingExecutor(root=tmp_path / "kq")
        res = await ke.execute(_edict())
        assert res.status == TaskStatus.FAILED
        assert "not found" in res.error
