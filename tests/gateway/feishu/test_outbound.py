"""FeishuOutbound 单元测试：markdown 检测、客户端调用、chat_id 反查。"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.outbound import FeishuOutbound, _MD_HINT_RE
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope


def _settings(home: str = "") -> FeishuSettings:
    return FeishuSettings(
        app_id="x", app_secret="y", domain="feishu", connection_mode="webhook",
        allowed_users=("ou_test",), home_channel=home,
        encrypt_key="", verification_token="", bot_open_id="", bot_name="",
        webhook_path="/feishu/webhook", ws_reconnect_interval=120,
        text_batch_delay=0.6, dedup_cache_size=2048,
    )


def test_md_hint_regex_detects_markdown():
    assert _MD_HINT_RE.search("\n# title\n") is not None
    assert _MD_HINT_RE.search("\n- item") is not None
    assert _MD_HINT_RE.search("**bold**") is not None
    assert _MD_HINT_RE.search("plain text only") is None


@pytest.mark.asyncio
async def test_send_text_uses_post_when_markdown_detected(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.success = lambda: True
    fake_resp.data = MagicMock(message_id="msg_1")
    fake_client.im.v1.message.acreate = AsyncMock(return_value=fake_resp)
    out._client = fake_client
    mid = await out.send_text("oc_x", "**hello**")
    assert mid == "msg_1"
    assert fake_client.im.v1.message.acreate.await_count == 1


@pytest.mark.asyncio
async def test_send_text_plain_when_no_markdown(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.success = lambda: True
    fake_resp.data = MagicMock(message_id="m1")
    fake_client.im.v1.message.acreate = AsyncMock(return_value=fake_resp)
    out._client = fake_client
    mid = await out.send_text("oc_x", "plain hello")
    assert mid == "m1"
    assert fake_client.im.v1.message.acreate.await_count == 1


@pytest.mark.asyncio
async def test_send_returns_none_when_client_uninitialized(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    # 未调 start()，self._client = None
    mid = await out.send_text("oc_x", "hello")
    assert mid is None


@pytest.mark.asyncio
async def test_send_text_empty_inputs_short_circuits(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    assert await out.send_text("", "x") is None
    assert await out.send_text("oc", "") is None


@pytest.mark.asyncio
async def test_lookup_chat_id_uses_metadata(storage):
    """events handler 反查 edict.metadata.chat_id。"""
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(), storage=storage, event_bus=bus)
    edict = Edict(title="t", goal="g", source="channel", metadata={"chat_id": "oc_meta"})
    storage.save_edict(edict)
    event = EventEnvelope(event_type="execution.completed", edict_id=edict.id)
    chat_id = out._lookup_chat_id(event)
    assert chat_id == "oc_meta"


@pytest.mark.asyncio
async def test_lookup_chat_id_fallback_home_channel(storage):
    """metadata 无 chat_id → 兜底 home_channel。"""
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(home="oc_home"), storage=storage, event_bus=bus)
    edict = Edict(title="t", goal="g", source="api", metadata={})
    storage.save_edict(edict)
    event = EventEnvelope(event_type="execution.completed", edict_id=edict.id)
    chat_id = out._lookup_chat_id(event)
    assert chat_id == "oc_home"


@pytest.mark.asyncio
async def test_lookup_chat_id_returns_none_when_no_edict_no_home(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_settings(home=""), storage=storage, event_bus=bus)
    event = EventEnvelope(event_type="execution.completed", edict_id="missing")
    assert out._lookup_chat_id(event) is None
