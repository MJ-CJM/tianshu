"""engine_registry 降级与开关注册单测。"""

from __future__ import annotations

import pytest

from tianshu.tools.hongluisi import engine_registry
from tianshu.tools.hongluisi.engine_registry import build_engines, reset_engines


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_engines()
    yield
    reset_engines()


@pytest.mark.unit
def test_duckduckgo_always_registered():
    fetch, search = build_engines(storage=None)
    assert "duckduckgo" in search


@pytest.mark.unit
def test_browser_engines_off_by_default():
    """storage=None → 无 prefs → 浏览器引擎不注册。"""
    fetch, _ = build_engines(storage=None)
    assert "scrapling_dynamic" not in fetch
    assert "scrapling_stealthy" not in fetch


@pytest.mark.unit
def test_scrapling_skipped_when_not_installed(monkeypatch):
    """build_scrapling 返回 None 时 fetch 不含 scrapling。"""
    monkeypatch.setattr(engine_registry, "build_scrapling", lambda mode: None)
    fetch, _ = build_engines(storage=None)
    assert "scrapling" not in fetch
    assert "local" in fetch  # local 始终在
