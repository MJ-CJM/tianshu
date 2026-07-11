"""Tests for SandboxRunner."""

import asyncio
from pathlib import Path

import pytest

from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.universe.execution import UniverseExecutionContextFactory
from tianshu.universe.sandbox import SandboxError, SandboxRunner


def _runner(*, security_mode="trusted-local", **kwargs) -> SandboxRunner:
    return SandboxRunner(
        ExecutionGateway(),
        context_factory=UniverseExecutionContextFactory(security_mode=security_mode),
        **kwargs,
    )


def _values(policy) -> dict[str, str]:
    return {item.name: item.value for item in policy.values}


def test_free_port_returns_int():
    p = SandboxRunner._free_port()
    assert isinstance(p, int) and 1024 < p < 65536


def test_build_env_injects_isolation(tmp_path: Path):
    r = _runner()
    env = _values(r._build_environment(tmp_path / "wt", tmp_path / "iso.db", 12345))
    assert env["TIANSHU_DB_PATH"] == str(tmp_path / "iso.db")
    assert env["TIANSHU_PORT"] == "12345"
    assert env["TIANSHU_EVAL_MODE"] == "1"
    assert env["PYTHONPATH"] == str((tmp_path / "wt" / "src").resolve())


def test_build_env_extra_env_overrides():
    runner = _runner()
    policy = runner._build_environment(
        Path("/tmp/wt"),
        Path("/tmp/db.sqlite"),
        12345,
        extra_env={
            "TIANSHU_RUNTIME_PERSONAS_DIR": "/tmp/personas",
            "TIANSHU_LLM_API_KEY": "${settings:eval_llm_api_key}",
        },
    )
    env = _values(policy)
    assert env["TIANSHU_RUNTIME_PERSONAS_DIR"] == "/tmp/personas"
    assert policy.secret_refs[0].env_name == "TIANSHU_LLM_API_KEY"
    assert policy.secret_refs[0].ref == "settings:eval_llm_api_key"
    assert env["TIANSHU_EVAL_MODE"] == "1"  # 原有注入不受影响
    assert env["TIANSHU_DB_PATH"] == "/tmp/db.sqlite"


def test_build_env_extra_env_cannot_unset_eval_mode():
    with pytest.raises(SandboxError, match="cannot be overridden"):
        _runner()._build_environment(
            Path("/tmp/wt"),
            Path("/tmp/db.sqlite"),
            12345,
            extra_env={"TIANSHU_EVAL_MODE": "0"},
        )


def test_build_env_rejects_literal_secret():
    with pytest.raises(SandboxError, match="secret reference"):
        _runner()._build_environment(
            Path("/tmp/wt"),
            Path("/tmp/db.sqlite"),
            12345,
            extra_env={"TIANSHU_LLM_API_KEY": "sk-must-not-be-literal"},
        )


@pytest.mark.slow
@pytest.mark.asyncio
async def test_sandbox_boots_real_app_and_health_ok(tmp_path: Path):
    """真实子进程冒烟：从主仓启动 app 到临时端口 + 隔离 DB，/health 返回 200，再销毁。"""
    repo_root = Path(__file__).resolve().parents[2]
    runner = _runner(startup_timeout_s=90)
    db = tmp_path / "iso.db"
    async with runner.session(repo_root, db_path=db) as h:
        import urllib.request

        def health_status() -> int:
            with urllib.request.urlopen(f"{h.base_url}/health", timeout=5) as response:
                return response.status

        assert await asyncio.to_thread(health_status) == 200
    assert h.receipt is not None
    assert h.receipt.status == "cancelled"
    assert h.execution.returncode is not None


@pytest.mark.asyncio
async def test_secure_remote_denial_removes_isolated_database(tmp_path: Path):
    db = tmp_path / "isolated.db"
    db.write_text("temporary")

    with pytest.raises(Exception, match="sandbox"):
        await _runner(security_mode="secure-remote").start(tmp_path, db_path=db)

    assert not db.exists()
