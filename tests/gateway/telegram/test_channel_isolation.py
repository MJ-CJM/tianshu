"""channel 路由隔离：telegram 出站只投 telegram 敕令；飞书出站拒投 telegram 敕令。"""
from __future__ import annotations

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.outbound import FeishuOutbound
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.gateway.telegram.outbound import TelegramOutbound
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope

from ._helpers import make_settings


def _feishu_settings(home: str = "") -> FeishuSettings:
    return FeishuSettings(
        app_id="x", app_secret="y", domain="feishu", connection_mode="webhook",
        allowed_users=(), home_channel=home, encrypt_key="", verification_token="",
        bot_open_id="", bot_name="", webhook_path="/feishu/webhook",
        ws_reconnect_interval=120, text_batch_delay=0.6, dedup_cache_size=2048,
    )


def test_telegram_outbound_delivers_only_telegram(storage):
    bus = EventBus(storage=storage)
    out = TelegramOutbound(settings=make_settings(home_channel="home_tg"), storage=storage, event_bus=bus)

    tg_edict = Edict(title="t", goal="g", source="channel",
                     metadata={"channel": "telegram", "chat_id": "555"})
    storage.save_edict(tg_edict)
    ev = EventEnvelope(event_type="execution.completed", edict_id=tg_edict.id)
    assert out._lookup_chat_id(ev) == "555"

    fs_edict = Edict(title="t", goal="g", source="channel",
                     metadata={"channel": "feishu", "chat_id": "oc_x"})
    storage.save_edict(fs_edict)
    ev2 = EventEnvelope(event_type="execution.completed", edict_id=fs_edict.id)
    assert out._lookup_chat_id(ev2) is None  # 飞书敕令不由 telegram 投递


def test_telegram_outbound_home_fallback_for_sourceless(storage):
    bus = EventBus(storage=storage)
    out = TelegramOutbound(settings=make_settings(home_channel="home_tg"), storage=storage, event_bus=bus)
    # 无 edict_id（cron 兜底）→ home
    ev = EventEnvelope(event_type="execution.completed", edict_id="")
    assert out._lookup_chat_id(ev) == "home_tg"


def test_feishu_outbound_rejects_telegram_edict(storage):
    bus = EventBus(storage=storage)
    out = FeishuOutbound(settings=_feishu_settings(home="oc_home"), storage=storage, event_bus=bus)
    tg_edict = Edict(title="t", goal="g", source="channel",
                     metadata={"channel": "telegram", "chat_id": "555"})
    storage.save_edict(tg_edict)
    ev = EventEnvelope(event_type="execution.completed", edict_id=tg_edict.id)
    assert out._lookup_chat_id(ev) is None  # telegram 敕令不由飞书投递

    # 飞书敕令仍正常
    fs_edict = Edict(title="t", goal="g", source="channel",
                     metadata={"channel": "feishu", "chat_id": "oc_y"})
    storage.save_edict(fs_edict)
    ev2 = EventEnvelope(event_type="execution.completed", edict_id=fs_edict.id)
    assert out._lookup_chat_id(ev2) == "oc_y"
