"""SystemSnapshot target input for ``tianshu serve``."""

from __future__ import annotations

import os

import typer
import uvicorn
from typer.testing import CliRunner

from tianshu.cli.commands.serve import serve

runner = CliRunner()
app = typer.Typer()
app.command()(serve)


def _clean_env(monkeypatch, tmp_path) -> None:
    for key in tuple(os.environ):
        if key.startswith("TIANSHU_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)


def test_system_snapshot_option_rebuilds_settings_from_environment(
    monkeypatch,
    tmp_path,
) -> None:
    _clean_env(monkeypatch, tmp_path)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda target, **kwargs: calls.append({"target": target, **kwargs}),
    )
    digest = "a" * 64
    monkeypatch.setenv("TIANSHU_SYSTEM_SNAPSHOT_TARGET", "b" * 64)

    result = runner.invoke(app, ["--system-snapshot", digest])

    assert result.exit_code == 0
    assert os.environ["TIANSHU_SYSTEM_SNAPSHOT_TARGET"] == digest
    assert len(calls) == 1


def test_system_snapshot_option_rejects_noncanonical_digest_before_server_start(
    monkeypatch,
    tmp_path,
) -> None:
    _clean_env(monkeypatch, tmp_path)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda target, **kwargs: calls.append({"target": target, **kwargs}),
    )
    monkeypatch.setenv("TIANSHU_SYSTEM_SNAPSHOT_TARGET", "b" * 64)

    result = runner.invoke(app, ["--system-snapshot", "A" * 64])

    assert result.exit_code == 1
    assert calls == []
    assert "system_snapshot_target must be 64 lowercase hex characters" in result.stdout


def test_system_snapshot_help_makes_strict_matching_explicit() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "指定启动时对照" in result.stdout
    assert "TIANSHU_SYSTEM_SNAPSHOT_STRICT" in result.stdout
    assert "时要求匹配" in result.stdout
