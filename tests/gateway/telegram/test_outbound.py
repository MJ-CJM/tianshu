"""TelegramOutbound：发送、send_card、完成投递 + thinking 占位删除。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.telegram.outbound import TelegramOutbound, _to_chat_id
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope
from tianshu.models.memorial import Memorial

from ._helpers import make_settings


def _outbound(storage, **skw):
    bus = EventBus(storage=storage)
    out = TelegramOutbound(settings=make_settings(**skw), storage=storage, event_bus=bus)
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    bot.delete_message = AsyncMock(return_value=True)
    bot.edit_message_text = AsyncMock(return_value=True)
    out._bot = bot
    return out, bot


def test_to_chat_id_numeric_vs_username():
    assert _to_chat_id("555") == 555
    assert _to_chat_id("-100123") == -100123
    assert _to_chat_id("@channel") == "@channel"


@pytest.mark.asyncio
async def test_send_text_returns_message_id(storage):
    out, bot = _outbound(storage)
    mid = await out.send_text("555", "**hello**")
    assert mid == "123"
    bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_send_text_none_when_no_bot(storage):
    bus = EventBus(storage=storage)
    out = TelegramOutbound(settings=make_settings(), storage=storage, event_bus=bus)
    assert await out.send_text("555", "hi") is None  # 未 start，bot=None


@pytest.mark.asyncio
async def test_send_card_with_keyboard(storage):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    out, bot = _outbound(storage)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("x", callback_data="cmd:list")]])
    mid = await out.send_card("555", ("菜单", kb))
    assert mid == "123"
    assert bot.send_message.await_args.kwargs["reply_markup"] is kb


@pytest.mark.asyncio
async def test_execution_completed_delivers_and_clears_thinking(storage):
    out, bot = _outbound(storage)
    edict = Edict(
        title="t", goal="g", source="channel", metadata={"channel": "telegram", "chat_id": "555"}
    )
    storage.save_edict(edict)
    memorial = Memorial(
        edict_id=edict.id, instruction="hi", status=TaskStatus.COMPLETED, result="最终结果"
    )
    storage.save_memorial(memorial)
    # 登记 thinking 占位
    storage.save_telegram_thinking(memorial_id=memorial.id, chat_id="555", message_id="9")

    ev = EventEnvelope(event_type="execution.completed", edict_id=edict.id, memorial_id=memorial.id)
    await out._on_execution_completed(ev)

    # 删除了 thinking 占位
    bot.delete_message.assert_awaited()
    # 发了结果
    bot.send_message.assert_awaited()
    # thinking 记录已清
    assert storage.pop_telegram_thinking(memorial.id) is None


@pytest.mark.asyncio
async def test_execution_completed_skips_non_telegram(storage):
    out, bot = _outbound(storage)
    edict = Edict(
        title="t", goal="g", source="channel", metadata={"channel": "feishu", "chat_id": "oc_x"}
    )
    storage.save_edict(edict)
    ev = EventEnvelope(event_type="execution.completed", edict_id=edict.id)
    await out._on_execution_completed(ev)
    bot.send_message.assert_not_awaited()  # 飞书敕令不投递


@pytest.mark.asyncio
async def test_execution_completed_multi_chunk_adds_continuation_prefix(storage):
    """长结果被 split_long 切成多片时，第 2 片起应带"续 i/n"前缀，首片不带。"""
    out, bot = _outbound(storage)
    edict = Edict(
        title="t", goal="g", source="channel", metadata={"channel": "telegram", "chat_id": "555"}
    )
    storage.save_edict(edict)
    # 两段各 2000 字，中间空行分隔：合计超过 _SAFE_CHUNK(3500) → split_long 切成 2 片
    para1 = "第一段。" * 500
    para2 = "第二段。" * 500
    memorial = Memorial(
        edict_id=edict.id,
        instruction="hi",
        status=TaskStatus.COMPLETED,
        result=para1 + "\n\n" + para2,
    )
    storage.save_memorial(memorial)

    ev = EventEnvelope(event_type="execution.completed", edict_id=edict.id, memorial_id=memorial.id)
    await out._on_execution_completed(ev)

    assert bot.send_message.await_count == 2
    first_text = bot.send_message.await_args_list[0].kwargs["text"]
    second_text = bot.send_message.await_args_list[1].kwargs["text"]
    assert "续" not in first_text
    assert "续 2/2" in second_text
