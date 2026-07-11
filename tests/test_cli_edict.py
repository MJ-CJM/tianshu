"""CLI smoke tests — typer CliRunner + respx-mocked HTTP backend.

只覆盖 `edict submit` / `edict list` 两条命令的端到端 happy path（HTTP 层用 respx
拦截，不连真后端）。respx_mock 由 respx 自带的 pytest 插件提供（dev 依赖已装 respx）。
"""

from __future__ import annotations

from typer.testing import CliRunner

from tianshu.cli.main import app

runner = CliRunner()


def test_edict_submit_smoke(respx_mock, monkeypatch, tmp_path):
    monkeypatch.delenv("TIANSHU_API_URL", raising=False)
    monkeypatch.delenv("TIANSHU_API_TOKEN", raising=False)
    monkeypatch.setenv("TIANSHU_CREDENTIAL_FILE", str(tmp_path / "credentials.json"))
    respx_mock.post("http://localhost:8000/api/edicts").respond(
        200,
        json={"data": {"id": "01HXAMPLE", "goal": "写周报", "status": "submitted"}},
    )

    result = runner.invoke(app, ["edict", "submit", "--goal", "写周报"])

    assert result.exit_code == 0
    assert "01HXAMPLE" in result.stdout
    assert "写周报" in result.stdout
    assert "submitted" in result.stdout


def test_edict_list_smoke(respx_mock, monkeypatch, tmp_path):
    monkeypatch.delenv("TIANSHU_API_URL", raising=False)
    monkeypatch.delenv("TIANSHU_API_TOKEN", raising=False)
    monkeypatch.setenv("TIANSHU_CREDENTIAL_FILE", str(tmp_path / "credentials.json"))
    respx_mock.get("http://localhost:8000/api/edicts").respond(
        200,
        json={
            "data": [
                {"id": "01HYEXAMPLE", "goal": "写周报", "created_at": "2026-07-01T00:00:00Z"},
            ],
            "metadata": {"total": 1},
        },
    )

    result = runner.invoke(app, ["edict", "list"])

    assert result.exit_code == 0
    assert "01HYEXAMPLE" in result.stdout
    assert "写周报" in result.stdout
