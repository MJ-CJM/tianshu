"""tianshu doctor 装机自检——离线检查项测试(不触真实 LLM)。"""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from tianshu.cli.commands.doctor import doctor

runner = CliRunner()

app = typer.Typer()
app.command()(doctor)


def _clean_env(monkeypatch, tmp_path, **overrides):
    """构造隔离环境:默认给全通过的最小配置,overrides 可打洞。"""
    defaults = {
        "TIANSHU_LLM_API_KEY": "sk-test-123456",
        "TIANSHU_DB_PATH": str(tmp_path / "db" / "t.db"),
        "TIANSHU_WORKSPACE_DIR": str(tmp_path),
        "TIANSHU_PORT": "59999",  # 高位端口,几乎必然空闲
    }
    defaults.update(overrides)
    # 先清掉宿主机可能带的 TIANSHU_* 干扰
    import os

    for key in list(os.environ):
        if key.startswith("TIANSHU_"):
            monkeypatch.delenv(key, raising=False)
    for k, v in defaults.items():
        if v is not None:
            monkeypatch.setenv(k, v)
    # 避免读取仓库根 .env
    monkeypatch.chdir(tmp_path)


class TestDoctor:
    def test_all_ok_exits_zero(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch, tmp_path)
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert "0 fail" in result.output

    def test_missing_api_key_fails(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch, tmp_path, TIANSHU_LLM_API_KEY=None)
        result = runner.invoke(app, [])
        assert result.exit_code == 1
        assert "1 fail" in result.output

    def test_unwritable_db_dir_fails(self, monkeypatch, tmp_path):
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        ro_dir.chmod(0o444)
        _clean_env(monkeypatch, tmp_path, TIANSHU_DB_PATH=str(ro_dir / "sub" / "t.db"))
        try:
            result = runner.invoke(app, [])
            assert result.exit_code == 1
        finally:
            ro_dir.chmod(0o755)

    def test_enabled_feishu_without_dep_reports(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch, tmp_path, TIANSHU_FEISHU_APP_ID="cli_test")
        # 仓库 .venv 装了 all extras(lark_oapi 存在)→ 应报 ok 而非 fail
        result = runner.invoke(app, [])
        assert "feishu" in result.output
        assert result.exit_code == 0
