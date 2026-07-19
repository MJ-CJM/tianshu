"""engine_registry 降级与开关注册单测。"""

from __future__ import annotations

import sys

import pytest

from tianshu.tools.hongluisi import engine_registry
from tianshu.tools.hongluisi.engine_registry import build_engines, reset_engines


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_engines()
    yield
    reset_engines()


@pytest.mark.unit
def test_duckduckgo_registered_when_lxml_available():
    fetch, search = build_engines(storage=None)
    assert "duckduckgo" in search


@pytest.mark.unit
def test_duckduckgo_skipped_when_lxml_not_installed(monkeypatch):
    """lxml 缺失只该让这一个引擎不注册，绝不能让进程起不来。

    回归守卫：duckduckgo_search 曾在模块级无条件 ``import lxml.html``，而 lxml
    并非核心依赖（开发 venv 里由 scrapling/web extra 传递带入，所以单测从没
    发现）——结果只装 ``tianshu[cli]`` 的发行物在 import engine_registry 时就
    炸，API 根本起不来。这个洞是 S1.5 fresh-wheel 黑盒第一次跑就抓到的。
    """
    monkeypatch.setattr(engine_registry, "build_duckduckgo", lambda: None)
    fetch, search = build_engines(storage=None)
    assert "duckduckgo" not in search
    assert "local" in fetch  # 其余引擎照常装配


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


@pytest.mark.unit
def test_engine_registry_imports_without_lxml_installed(monkeypatch):
    """把 lxml 真正从 import 系统里摘掉，engine_registry 仍必须能导入。

    比 monkeypatch build_duckduckgo 更强一档：那只能证明"注册逻辑会跳过"，
    这条证明的是"模块级不会有人偷偷 import 可选依赖"——即黑盒抓到的那类缺陷
    （ModuleNotFoundError: lxml → uvicorn 加载 app 直接失败）。
    """
    import builtins
    import importlib

    real_import = builtins.__import__

    def _no_lxml(name, *args, **kwargs):
        if name == "lxml" or name.startswith("lxml."):
            raise ImportError("No module named 'lxml' (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_lxml)
    for mod in ("lxml", "lxml.html"):
        monkeypatch.delitem(sys.modules, mod, raising=False)

    module = importlib.reload(
        importlib.import_module("tianshu.tools.hongluisi.engines.duckduckgo_search")
    )
    assert module.build_duckduckgo() is None, "lxml 缺失时必须不注册，而不是抛异常"

    registry = importlib.reload(importlib.import_module("tianshu.tools.hongluisi.engine_registry"))
    _, search = registry.build_engines(storage=None)
    assert "duckduckgo" not in search
