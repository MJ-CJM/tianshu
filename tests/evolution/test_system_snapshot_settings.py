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
    assert settings.executor_generation_enabled is False
    assert settings.executor_drift_scan_enabled is False

    with pytest.raises(ValidationError, match="requires system_snapshot_enabled"):
        TianshuSettings(
            _env_file=None,
            system_snapshot_enabled=False,
            system_snapshot_strict=True,
        )


def test_executor_generation_requires_snapshot_while_drift_scan_is_independent() -> None:
    generation_only = TianshuSettings(
        _env_file=None,
        executor_generation_enabled=True,
    )
    scan_only = TianshuSettings(
        _env_file=None,
        system_snapshot_enabled=False,
        executor_drift_scan_enabled=True,
    )

    assert generation_only.executor_generation_enabled is True
    assert generation_only.executor_drift_scan_enabled is False
    assert scan_only.executor_generation_enabled is False
    assert scan_only.executor_drift_scan_enabled is True

    with pytest.raises(
        ValidationError,
        match="executor_generation_enabled requires system_snapshot_enabled",
    ):
        TianshuSettings(
            _env_file=None,
            system_snapshot_enabled=False,
            executor_generation_enabled=True,
        )


def test_snapshot_strict_uses_documented_environment_name(monkeypatch) -> None:
    monkeypatch.setenv("TIANSHU_SNAPSHOT_STRICT", "1")

    assert TianshuSettings(_env_file=None).system_snapshot_strict is True


def test_disabled_wiring_exposes_explicit_none() -> None:
    app = FastAPI()
    settings = TianshuSettings(_env_file=None, system_snapshot_enabled=False)

    wire_system_snapshot(app, settings)

    assert app.state.system_snapshot_resolver is None
