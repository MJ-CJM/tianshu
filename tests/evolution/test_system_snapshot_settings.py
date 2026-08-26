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


def test_system_snapshot_target_is_an_exact_lowercase_sha256(monkeypatch) -> None:
    digest = "a" * 64
    monkeypatch.setenv("TIANSHU_SYSTEM_SNAPSHOT_TARGET", digest)

    assert TianshuSettings(_env_file=None).system_snapshot_target == digest

    for invalid in ("A" * 64, f"sha256:{digest}", f" {digest}", "a" * 63):
        with pytest.raises(
            ValidationError,
            match="system_snapshot_target must be 64 lowercase hex characters",
        ):
            TianshuSettings(_env_file=None, system_snapshot_target=invalid)


def test_system_snapshot_target_requires_snapshot_wiring() -> None:
    with pytest.raises(
        ValidationError,
        match="system_snapshot_target requires system_snapshot_enabled",
    ):
        TianshuSettings(
            _env_file=None,
            system_snapshot_enabled=False,
            system_snapshot_target="a" * 64,
        )


def test_disabled_wiring_exposes_explicit_none() -> None:
    app = FastAPI()
    settings = TianshuSettings(_env_file=None, system_snapshot_enabled=False)

    wire_system_snapshot(app, settings)

    assert app.state.system_snapshot_resolver is None


def test_frozen_content_view_switches_default_off_and_allow_shadow_mode() -> None:
    defaults = TianshuSettings(_env_file=None)
    shadow = TianshuSettings(_env_file=None, frozen_content_views=True)

    assert defaults.frozen_content_views is False
    assert defaults.frozen_content_views_enforced is False
    assert shadow.frozen_content_views is True
    assert shadow.frozen_content_views_enforced is False


def test_frozen_content_view_enforcement_requires_view_and_snapshot_support() -> None:
    with pytest.raises(
        ValidationError,
        match="frozen_content_views_enforced requires frozen_content_views",
    ):
        TianshuSettings(_env_file=None, frozen_content_views_enforced=True)

    with pytest.raises(
        ValidationError,
        match="frozen_content_views_enforced requires system_snapshot_enabled",
    ):
        TianshuSettings(
            _env_file=None,
            system_snapshot_enabled=False,
            frozen_content_views=True,
            frozen_content_views_enforced=True,
        )

    enforced = TianshuSettings(
        _env_file=None,
        frozen_content_views=True,
        frozen_content_views_enforced=True,
    )
    assert enforced.frozen_content_views_enforced is True
