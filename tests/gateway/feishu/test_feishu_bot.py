"""FeishuBot 命令路由 + EdictBusyError 兜底单元测试。

直接构造 FeishuBot，mock outbound/connection 依赖，绕开 lark client 真启动。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu import FeishuBot
from tianshu.gateway.feishu.dispatcher import FeishuCardAction, FeishuMessage
from tianshu.gateway.feishu.settings import FeishuSettings


def _settings(disable_assistant_mode: bool = True) -> FeishuSettings:
    """v1 legacy fallback：disable_assistant_mode=True，保留 v1 命令路由测试。"""
    return FeishuSettings(
        app_id="x",
        app_secret="y",
        domain="feishu",
        connection_mode="webhook",
        allowed_users=("ou_test",),
        home_channel="",
        encrypt_key="",
        verification_token="",
        bot_open_id="",
        bot_name="",
        webhook_path="/feishu/webhook",
        ws_reconnect_interval=120,
        text_batch_delay=0.0,
        dedup_cache_size=2048,
        assistant_persona_id="tongzheng",
        intent_llm_enabled=False,
        disable_assistant_mode=disable_assistant_mode,
    )


@pytest.fixture
def bot(storage):
    bus = EventBus(storage=storage)
    approval = MagicMock()
    approval.submit_tool_decision = AsyncMock()
    executor = MagicMock()
    executor.execute_edict = AsyncMock()
    executor.running_tasks = set()
    notifier = MagicMock()
    persona_loader = MagicMock()
    persona_loader.get = MagicMock(return_value=None)
    fb = FeishuBot(
        storage=storage,
        event_bus=bus,
        approval_manager=approval,
        executor=executor,
        notifier=notifier,
        settings=_settings(),
        persona_loader=persona_loader,
        provider_manager=None,
        cost_manager=None,
    )
    # 把 outbound.send_text 替换成 mock，避免 lark client 调用
    fb._outbound.send_text = AsyncMock(return_value="m1")
    fb._outbound.send_card = AsyncMock(return_value="mc1")
    fb._outbound.update_card = AsyncMock(return_value=True)
    return fb


def _msg(text: str, chat_id: str = "oc_x", sender: str = "ou_test") -> FeishuMessage:
    return FeishuMessage(
        event_id="e",
        chat_id=chat_id,
        chat_type="p2p",
        sender_open_id=sender,
        text=text,
        raw={},
    )


@pytest.mark.asyncio
async def test_new_command_creates_edict(bot, storage):
    await bot._on_message(_msg("/new build me a thing"))
    bot._outbound.send_text.assert_awaited_once()
    args = bot._outbound.send_text.await_args.args
    assert "新敕令" in args[1] or "已创建" in args[1]


@pytest.mark.asyncio
async def test_new_without_goal_prompts_usage(bot):
    await bot._on_message(_msg("/new"))
    args = bot._outbound.send_text.await_args.args
    assert "/new" in args[1]


@pytest.mark.asyncio
async def test_status_no_anchor(bot):
    await bot._on_message(_msg("/status"))
    args = bot._outbound.send_text.await_args.args
    assert "无活跃敕令" in args[1]


@pytest.mark.asyncio
async def test_status_with_anchor(bot, storage):
    """先创建一条 → /status 应显示其状态。"""
    await bot._on_message(_msg("/new task one"))
    bot._outbound.send_text.reset_mock()
    await bot._on_message(_msg("/status"))
    args = bot._outbound.send_text.await_args.args
    assert "标题" in args[1]


@pytest.mark.asyncio
async def test_status_unknown_id(bot):
    await bot._on_message(_msg("/status nonexistent_id"))
    args = bot._outbound.send_text.await_args.args
    assert "不存在" in args[1]


@pytest.mark.asyncio
async def test_cancel_no_target(bot):
    await bot._on_message(_msg("/cancel"))
    args = bot._outbound.send_text.await_args.args
    assert "/cancel" in args[1]


@pytest.mark.asyncio
async def test_cancel_unknown_id(bot):
    await bot._on_message(_msg("/cancel nonexistent_id"))
    args = bot._outbound.send_text.await_args.args
    assert "不存在" in args[1]


@pytest.mark.asyncio
async def test_set_home_command(bot):
    await bot._on_message(_msg("/set-home", chat_id="oc_target"))
    args = bot._outbound.send_text.await_args.args
    assert "oc_target" in args[1]


@pytest.mark.asyncio
async def test_help_command(bot):
    await bot._on_message(_msg("/help"))
    args = bot._outbound.send_text.await_args.args
    assert "可用命令" in args[1] or "/new" in args[1]


@pytest.mark.asyncio
async def test_unknown_command(bot):
    await bot._on_message(_msg("/foo"))
    args = bot._outbound.send_text.await_args.args
    assert "未知命令" in args[1]


@pytest.mark.asyncio
async def test_default_text_continues_or_creates(bot, storage):
    await bot._on_message(_msg("帮我看进度"))
    args = bot._outbound.send_text.await_args.args
    assert "已收到" in args[1]


@pytest.mark.asyncio
async def test_default_text_busy_raises_friendly_message(bot):
    """active memorial 存在时 → continue_or_create 抛 EdictBusyError → 用户看到友好消息。"""
    # 第一条建 Edict，留 SUBMITTED memorial
    await bot._on_message(_msg("first task"))
    bot._outbound.send_text.reset_mock()
    # 第二条文本（非命令）→ 应触发 EdictBusyError 路径
    await bot._on_message(_msg("second"))
    args = bot._outbound.send_text.await_args.args
    assert "处理中" in args[1] or "等待" in args[1]


@pytest.mark.asyncio
async def test_card_dispatch_routes_to_approval_handler(bot):
    bot._approval_card.handle_button_click = AsyncMock()
    action = FeishuCardAction(
        event_id="e",
        chat_id="c",
        sender_open_id="ou_test",
        value={"memorial_id": "m1", "action": "approve"},
    )
    await bot._on_card(action)
    bot._approval_card.handle_button_click.assert_awaited_once_with(action)
