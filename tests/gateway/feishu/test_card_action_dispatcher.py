"""CardActionDispatcher 单元测试：按钮 value → 文本命令合成。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.gateway.feishu.card_action_dispatcher import CardActionDispatcher
from tianshu.gateway.feishu.dispatcher import FeishuCardAction


def _action(value: dict, chat: str = "oc_x", sender: str = "ou_a") -> FeishuCardAction:
    return FeishuCardAction(
        event_id="e",
        chat_id=chat,
        sender_open_id=sender,
        value=value,
    )


def test_synthesize_select():
    assert CardActionDispatcher._synthesize("select", {"edict_id": "ed_x"}) == "/select ed_x"


def test_synthesize_select_missing_id_returns_none():
    assert CardActionDispatcher._synthesize("select", {}) is None


def test_synthesize_list_with_filter():
    assert CardActionDispatcher._synthesize("list", {"filter": "completed"}) == "/list completed"


def test_synthesize_list_default_filter():
    assert CardActionDispatcher._synthesize("list", {}) == "/list open"


def test_synthesize_budget():
    assert CardActionDispatcher._synthesize("budget", {}) == "/budget"


def test_synthesize_help():
    assert CardActionDispatcher._synthesize("help", {}) == "/help"


def test_synthesize_new_with_goal():
    assert CardActionDispatcher._synthesize("new", {"goal": "写代码"}) == "/new 写代码"


def test_synthesize_new_without_goal_returns_none():
    assert CardActionDispatcher._synthesize("new", {}) is None


def test_synthesize_cancel_with_id():
    assert CardActionDispatcher._synthesize("cancel", {"edict_id": "ed_a"}) == "/cancel ed_a"


def test_synthesize_cancel_without_id():
    assert CardActionDispatcher._synthesize("cancel", {}) == "/cancel"


def test_synthesize_unknown_command_returns_none():
    assert CardActionDispatcher._synthesize("evil", {}) is None


@pytest.mark.asyncio
async def test_handle_dispatches_to_mode_router():
    router = MagicMock()
    router.dispatch = AsyncMock()
    d = CardActionDispatcher(mode_router=router)
    await d.handle(_action({"command": "list", "filter": "open"}))
    router.dispatch.assert_awaited_once()
    fake_msg = router.dispatch.await_args.args[0]
    assert fake_msg.text == "/list open"
    assert fake_msg.raw["_from_card_button"] is True


@pytest.mark.asyncio
async def test_handle_missing_command_no_dispatch():
    router = MagicMock()
    router.dispatch = AsyncMock()
    d = CardActionDispatcher(mode_router=router)
    await d.handle(_action({}))
    router.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_unknown_command_no_dispatch():
    router = MagicMock()
    router.dispatch = AsyncMock()
    d = CardActionDispatcher(mode_router=router)
    await d.handle(_action({"command": "evil"}))
    router.dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_synthesize_returns_none_no_dispatch():
    """synthesize 返回 None 也不分发（如 new 缺 goal）。"""
    router = MagicMock()
    router.dispatch = AsyncMock()
    d = CardActionDispatcher(mode_router=router)
    await d.handle(_action({"command": "new"}))
    router.dispatch.assert_not_awaited()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"command": "exit"}, "/exit"),
        ({"command": "clear"}, "/clear"),
        ({"command": "status", "edict_id": "01KZ5XFD"}, "/status 01KZ5XFD"),
    ],
)
def test_synthesize_supports_menu_buttons(value, expected):
    """敕令模式菜单的按钮必须能合成命令，否则点了没反应（废按钮）。"""
    from tianshu.gateway.feishu.card_action_dispatcher import CardActionDispatcher

    assert CardActionDispatcher._synthesize(value["command"], value) == expected
