"""Tests for SandboxRunner."""

import os
from pathlib import Path

import pytest

from tianshu.universe.sandbox import SandboxRunner


def test_free_port_returns_int():
    p = SandboxRunner._free_port()
    assert isinstance(p, int) and 1024 < p < 65536


def test_build_env_injects_isolation(tmp_path: Path):
    r = SandboxRunner()
    env = r._build_env(tmp_path / "wt", tmp_path / "iso.db", 12345)
    assert env["TIANSHU_DB_PATH"] == str(tmp_path / "iso.db")
    assert env["TIANSHU_PORT"] == "12345"
    assert env["TIANSHU_EVAL_MODE"] == "1"
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(tmp_path / "wt" / "src")


def test_build_env_extra_env_overrides():
    from tianshu.universe.sandbox import SandboxRunner

    runner = SandboxRunner()
    env = runner._build_env(
        Path("/tmp/wt"),
        Path("/tmp/db.sqlite"),
        12345,
        extra_env={
            "TIANSHU_RUNTIME_PERSONAS_DIR": "/tmp/personas",
            "TIANSHU_LLM_API_KEY": "sk-eval",
        },
    )
    assert env["TIANSHU_RUNTIME_PERSONAS_DIR"] == "/tmp/personas"
    assert env["TIANSHU_LLM_API_KEY"] == "sk-eval"
    assert env["TIANSHU_EVAL_MODE"] == "1"  # 原有注入不受影响
    assert env["TIANSHU_DB_PATH"] == "/tmp/db.sqlite"


def test_build_env_extra_env_cannot_unset_eval_mode():
    from tianshu.universe.sandbox import SandboxRunner

    env = SandboxRunner()._build_env(
        Path("/tmp/wt"),
        Path("/tmp/db.sqlite"),
        12345,
        extra_env={"TIANSHU_EVAL_MODE": "0"},
    )
    assert env["TIANSHU_EVAL_MODE"] == "1"  # 安全围栏字段不可被覆盖


@pytest.mark.slow
def test_sandbox_boots_real_app_and_health_ok(tmp_path: Path):
    """真实子进程冒烟：从主仓启动 app 到临时端口 + 隔离 DB，/health 返回 200，再销毁。"""
    repo_root = Path(__file__).resolve().parents[2]
    runner = SandboxRunner(startup_timeout_s=90)
    db = tmp_path / "iso.db"
    with runner.session(repo_root, db_path=db) as h:
        import urllib.request

        with urllib.request.urlopen(f"{h.base_url}/health", timeout=5) as resp:
            assert resp.status == 200
    assert h.proc.poll() is not None
