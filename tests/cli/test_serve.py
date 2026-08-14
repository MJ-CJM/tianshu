"""tianshu serve CLI——绑定地址来源、命令行覆盖与安全边界不可绕过。"""

from __future__ import annotations

import os

import pytest
import typer
import uvicorn
from typer.testing import CliRunner

from tianshu.cli.commands.serve import serve

runner = CliRunner()

app = typer.Typer()
app.command()(serve)


@pytest.fixture
def captured_run(monkeypatch):
    """拦截 uvicorn.run:测试只验参数，不真的起服务。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        uvicorn, "run", lambda target, **kwargs: calls.append({"target": target, **kwargs})
    )
    return calls


def _clean_env(monkeypatch, tmp_path, **overrides):
    """清掉宿主机 TIANSHU_* 干扰,再按需打洞。"""
    for key in list(os.environ):
        if key.startswith("TIANSHU_"):
            monkeypatch.delenv(key, raising=False)
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)
    # 避免读取仓库根 .env
    monkeypatch.chdir(tmp_path)


class TestServe:
    def test_defaults_come_from_settings(self, monkeypatch, tmp_path, captured_run):
        _clean_env(monkeypatch, tmp_path)

        result = runner.invoke(app, [])

        assert result.exit_code == 0
        assert captured_run == [
            {
                "target": "tianshu.app:create_app",
                "factory": True,
                "host": "127.0.0.1",
                "port": 8000,
                "reload": False,
            }
        ]

    def test_env_overrides_defaults(self, monkeypatch, tmp_path, captured_run):
        _clean_env(monkeypatch, tmp_path, TIANSHU_PORT="9100")

        result = runner.invoke(app, [])

        assert result.exit_code == 0
        assert captured_run[0]["port"] == 9100

    def test_cli_options_override_env(self, monkeypatch, tmp_path, captured_run):
        _clean_env(monkeypatch, tmp_path, TIANSHU_PORT="9100")

        result = runner.invoke(app, ["--host", "localhost", "--port", "9200"])

        assert result.exit_code == 0
        assert captured_run[0]["host"] == "localhost"
        assert captured_run[0]["port"] == 9200

    def test_reload_flag_is_forwarded(self, monkeypatch, tmp_path, captured_run):
        _clean_env(monkeypatch, tmp_path)

        result = runner.invoke(app, ["--reload"])

        assert result.exit_code == 0
        assert captured_run[0]["reload"] is True

    def test_non_loopback_host_is_rejected_before_binding(
        self, monkeypatch, tmp_path, captured_run
    ):
        """--host 不能成为绕过 trusted-local loopback 校验的第二条路径。"""
        _clean_env(monkeypatch, tmp_path)

        result = runner.invoke(app, ["--host", "0.0.0.0"])

        assert result.exit_code == 1
        assert captured_run == []  # 校验失败必须发生在起服务之前
        assert "loopback" in result.stdout

    def test_validation_error_does_not_echo_config_values(
        self, monkeypatch, tmp_path, captured_run
    ):
        """错误输出只回显 msg:input 可能携带 API key 等配置原值。"""
        _clean_env(monkeypatch, tmp_path, TIANSHU_LLM_API_KEY="sk-must-not-leak")

        result = runner.invoke(app, ["--host", "203.0.113.7"])

        assert result.exit_code == 1
        assert "sk-must-not-leak" not in result.stdout
        assert "203.0.113.7" not in result.stdout
