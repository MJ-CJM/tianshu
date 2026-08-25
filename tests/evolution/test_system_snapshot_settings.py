from __future__ import annotations

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from tianshu.bootstrap.wiring_snapshot import wire_system_snapshot
from tianshu.config import TianshuSettings


def test_system_snapshot_switch_defaults_and_strict_invariant() -> None:
    settings = TianshuSettings(_env_file=None)
    assert settings.system_snapshot_enabled is True
    assert settings.system_snapshot_strict is False

    with pytest.raises(ValidationError, match="requires system_snapshot_enabled"):
        TianshuSettings(
            _env_file=None,
            system_snapshot_enabled=False,
            system_snapshot_strict=True,
        )


def test_snapshot_strict_uses_documented_environment_name(monkeypatch) -> None:
    monkeypatch.setenv("TIANSHU_SNAPSHOT_STRICT", "1")

    assert TianshuSettings(_env_file=None).system_snapshot_strict is True


def test_disabled_wiring_exposes_explicit_none() -> None:
    app = FastAPI()
    settings = TianshuSettings(_env_file=None, system_snapshot_enabled=False)

    wire_system_snapshot(app, settings)

    assert app.state.system_snapshot_resolver is None
