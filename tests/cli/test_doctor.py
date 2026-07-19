"""tianshu doctor CLI——G1.5 版本化结构报告契约（table/json 双格式、退出码）。"""

from __future__ import annotations

import json

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
        "TIANSHU_MEMORY_DIR": str(tmp_path / "memory"),
        "TIANSHU_RUNTIME_PERSONAS_DIR": str(tmp_path / "personas"),
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
    def test_all_ok_exits_zero_in_table_format(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch, tmp_path)
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert "doctor report v1" in result.output
        assert "runtime.profile" in result.output

    def test_missing_api_key_in_live_profile_fails(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch, tmp_path, TIANSHU_LLM_API_KEY=None)
        result = runner.invoke(app, [])
        assert result.exit_code == 1
        assert "provider.config" in result.output

    def test_demo_profile_passes_without_api_key(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch, tmp_path, TIANSHU_LLM_API_KEY=None, TIANSHU_STARTUP_PROFILE="demo")
        result = runner.invoke(app, [])
        assert result.exit_code == 0

    def test_json_format_emits_canonical_versioned_report(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch, tmp_path)
        result = runner.invoke(app, ["--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["schema_version"] == "1"
        ids = [c["id"] for c in payload["checks"]]
        assert "database.migrations" in ids and "resources.package" in ids
        # table 与 json 共享同一检查集与退出语义
        table = runner.invoke(app, [])
        for check_id in ids:
            assert check_id in table.output

    def test_doctor_default_does_not_create_database(self, monkeypatch, tmp_path):
        db_path = tmp_path / "db" / "t.db"
        _clean_env(monkeypatch, tmp_path, TIANSHU_DB_PATH=str(db_path))
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert not db_path.exists(), "Doctor 不得创建数据库或其父目录"
        assert not db_path.parent.exists()

    def test_secret_never_appears_in_output(self, monkeypatch, tmp_path):
        secret = "sk-cli-secret-987654321"
        _clean_env(monkeypatch, tmp_path, TIANSHU_LLM_API_KEY=secret)
        for args in ([], ["--format", "json"]):
            result = runner.invoke(app, args)
            assert secret not in result.output
            assert secret[:8] not in result.output

    def test_unknown_format_exits_two(self, monkeypatch, tmp_path):
        _clean_env(monkeypatch, tmp_path)
        result = runner.invoke(app, ["--format", "yaml"])
        assert result.exit_code == 2

    def test_degraded_marker_survives_table_rendering(self, monkeypatch, tmp_path):
        """rich markup 不得吞掉 [degraded]——恰恰是需要关注的降级项会丢标记。"""
        # workspace_dir 指向不存在的目录 → workspace.git = degraded
        _clean_env(monkeypatch, tmp_path, TIANSHU_WORKSPACE_DIR=str(tmp_path / "no-such-workspace"))
        result = runner.invoke(app, [])
        assert result.exit_code == 0
        assert "workspace.git" in result.output
        assert "[degraded]" in result.output, "degraded 状态标记被 rich markup 吞掉"

    def test_evidence_is_not_interpreted_as_rich_markup(self, monkeypatch, tmp_path):
        """证据文本（可能来自端口上的任意进程）不得被当作 rich 样式标签解释。"""
        import tianshu.cli.commands.doctor as doctor_cli
        from tianshu.diagnostics import DoctorCheck, DoctorReport

        _clean_env(monkeypatch, tmp_path)
        hostile = DoctorReport(
            schema_version="1",
            status="degraded",
            checks=(
                DoctorCheck(
                    id="server.live",
                    status="degraded",
                    required=False,
                    evidence={"reported": "[red]spoofed[/red]"},
                    remediation="check it",
                ),
            ),
        )
        monkeypatch.setattr(doctor_cli, "run_doctor_checks", lambda *a, **k: hostile)
        result = runner.invoke(app, [])
        assert "[red]spoofed[/red]" in result.output, "evidence 被 rich markup 解释/吞掉"
