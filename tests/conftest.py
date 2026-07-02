"""Shared test fixtures."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# lark_oapi is an optional dependency (飞书 bot 功能)。在没有安装的环境里，
# 通过 sys.modules 注入空 stub，让 gateway/feishu/connection.py 的顶层导入不报错。
# ---------------------------------------------------------------------------
def _stub_lark_oapi() -> None:
    """Insert MagicMock stubs for all lark_oapi sub-modules so top-level
    imports in gateway/feishu/connection.py don't raise ModuleNotFoundError."""
    import types

    _submodules = [
        "lark_oapi",
        "lark_oapi.api",
        "lark_oapi.api.im",
        "lark_oapi.api.im.v1",
        "lark_oapi.event",
        "lark_oapi.event.callback",
        "lark_oapi.event.callback.model",
        "lark_oapi.event.callback.model.p2_card_action_trigger",
        "lark_oapi.core",
        "lark_oapi.core.const",
        "lark_oapi.core.json",
        "lark_oapi.ws",
        "lark_oapi.ws.client",
        "lark_oapi.ws.const",
        "lark_oapi.ws.enum",
        "lark_oapi.ws.model",
    ]
    for name in _submodules:
        if name not in sys.modules:
            mod = types.ModuleType(name)
            # Make every attribute access return a MagicMock so that
            # `from lark_oapi.xxx import Yyy` statements succeed.
            # 有意用默认参数在 lambda 定义时求值一次，让同一 stub 模块的所有属性
            # 访问返回同一个 MagicMock 实例（而非每次都是新实例）。
            mod.__getattr__ = lambda attr, _m=MagicMock(): _m  # noqa: B008  # type: ignore[method-assign]
            sys.modules[name] = mod


try:
    import lark_oapi  # noqa: F401
except ModuleNotFoundError:
    _stub_lark_oapi()

import pytest  # noqa: E402

from tianshu.config_manager import AgentConfigState, ConfigManager, LLMConfigState  # noqa: E402
from tianshu.storage import Storage  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """所有测试自动隔离：将 TIANSHU_DB_PATH 指向 tmp_path，杜绝漏入 prod DB。

    背景：tests/test_gateway*.py 调 `create_app() + lifespan()` 会触发 Settings 读
    `TIANSHU_DB_PATH` 默认 `~/.tianshu/tianshu.db`。autouse fixture 强制覆盖。
    """
    db = tmp_path / "tianshu_isolated.db"
    monkeypatch.setenv("TIANSHU_DB_PATH", str(db))
    # 也禁用真实 logs 目录写入
    monkeypatch.setenv("TIANSHU_LOGS_DIR", str(tmp_path / "logs"))
    yield


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_db()
    yield s
    s.close()


@pytest.fixture
def config_manager():
    initial = LLMConfigState(
        name="test-model",
        model="test-model",
        api_key="test-key",
        api_base="http://localhost:9999",
    )
    agent_cfg = AgentConfigState(
        agent_max_iterations=5,
        agent_timeout_seconds=30,
        skills_char_budget=1000,
    )
    return ConfigManager(initial, agent_config=agent_cfg)


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient that returns a simple text response."""
    mock = AsyncMock()
    from tianshu.models import UsageSummary

    mock.chat.return_value = MagicMock(
        content="Task completed successfully.",
        tool_calls=None,
        usage=UsageSummary(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        reasoning_content=None,
        finish_reason="stop",
    )
    return mock
