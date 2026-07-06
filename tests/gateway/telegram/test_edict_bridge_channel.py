"""EdictBridge channel 参数：telegram 写对 metadata；默认 feishu 向后兼容。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.core.edict_bridge import EdictBridge
from tianshu.gateway.telegram.session_anchor import SessionAnchor


def _bridge(storage, **kw):
    bus = EventBus(storage=storage)
    anchor = SessionAnchor(storage)
    executor = MagicMock()
    executor.execute_edict = AsyncMock()
    executor.running_tasks = set()
    return EdictBridge(storage=storage, event_bus=bus, executor=executor, anchor=anchor, **kw)


@pytest.mark.asyncio
async def test_default_channel_is_feishu(storage):
    """不传 channel → 行为与改动前完全一致（向后兼容）。"""
    b = _bridge(storage)  # 默认 channel="feishu"
    r = await b.create_new(chat_id="oc_x", sender_open_id="ou_a", goal="任务")
    edict = storage.get_edict(r.edict_id)
    assert edict.metadata["channel"] == "feishu"
    assert edict.metadata["feishu_user"] == "ou_a"


@pytest.mark.asyncio
async def test_telegram_channel_metadata(storage):
    b = _bridge(
        storage,
        channel="telegram",
        user_meta_key="telegram_user",
        chat_title_prefix="Telegram 助手对话",
    )
    r = await b.create_new(chat_id="555", sender_open_id="777", goal="部署")
    edict = storage.get_edict(r.edict_id)
    assert edict.metadata["channel"] == "telegram"
    assert edict.metadata["telegram_user"] == "777"
    assert "feishu_user" not in edict.metadata


@pytest.mark.asyncio
async def test_telegram_ensure_chat_edict_title(storage):
    b = _bridge(
        storage,
        channel="telegram",
        user_meta_key="telegram_user",
        chat_title_prefix="Telegram 助手对话",
    )
    eid = await b.ensure_chat_edict(chat_id="555", sender_open_id="777")
    edict = storage.get_edict(eid)
    assert edict.title.startswith("Telegram 助手对话")
    assert edict.metadata["assistant_chat"] is True
    assert edict.metadata["channel"] == "telegram"
