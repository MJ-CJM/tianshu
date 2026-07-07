"""ChannelBotManager._build_instances 三级来源 + outbound 退订。

覆盖：
- env 兜底：空 DB → feishu-default / telegram-default 各一个，无凭证 → 不启用。
- DB 实例：channel_instances 有行 → 用其 runtime（含解密 bot_token）构造。
- 旧单配置迁移：channel_configs 有凭证 → 首次 _build 时迁移成 *-default 实例。
- outbound.stop() → EventBus.off → 已停实例不再收事件。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.gateway.bot_manager import ChannelBotManager
from tianshu.gateway.feishu.outbound import FeishuOutbound
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope


@pytest.fixture
def _master_key(monkeypatch):
    """配置 Fernet 主密钥，让 save_channel_instance / runtime 加解密可用。"""
    from cryptography.fernet import Fernet

    from tianshu.secrets.vault import reset_vault

    monkeypatch.setenv("TIANSHU_SECRET_MASTER_KEY", Fernet.generate_key().decode())
    reset_vault()
    yield
    reset_vault()


def _manager(storage) -> ChannelBotManager:
    return ChannelBotManager(
        storage=storage,
        event_bus=EventBus(storage=storage),
        approval_manager=MagicMock(),
        executor=MagicMock(),
        notifier=MagicMock(),
        persona_loader=MagicMock(),
        provider_manager=MagicMock(),
        cost_manager=MagicMock(),
        env_settings=TianshuSettings(),
        app=None,
    )


# --------------------------------------------------------------------------
# 1) env 兜底
# --------------------------------------------------------------------------


def test_env_fallback_two_disabled_instances(storage):
    mgr = _manager(storage)
    instances = mgr._build_instances()
    by_id = {i.instance_id: i for i in instances}
    assert set(by_id) == {"feishu-default", "telegram-default"}
    # 无凭证 → settings.enabled False，整体 enabled False
    assert by_id["feishu-default"].enabled is False
    assert by_id["telegram-default"].enabled is False


@pytest.mark.asyncio
async def test_start_all_with_env_fallback_starts_none(storage):
    mgr = _manager(storage)
    await mgr.start_all()  # 不应抛异常
    assert mgr.status() == []  # 没有实例被启动


# --------------------------------------------------------------------------
# 2) DB 实例
# --------------------------------------------------------------------------


def test_db_instance_built_with_decrypted_token(storage, _master_key):
    storage.save_channel_instance(
        instance_id="telegram-default",
        channel_type="telegram",
        label="x",
        enabled=True,
        config={"connection_mode": "polling", "home_channel": ""},
        secret_plaintext="123:ABC",
    )
    mgr = _manager(storage)
    instances = mgr._build_instances()
    tg = next(i for i in instances if i.channel_type == "telegram")
    assert tg.instance_id == "telegram-default"
    assert tg.enabled is True
    assert tg.settings.bot_token == "123:ABC"
    assert tg.settings.instance_id == "telegram-default"


def test_db_instance_construct_returns_telegram_bot(storage, _master_key):
    storage.save_channel_instance(
        instance_id="telegram-default",
        channel_type="telegram",
        label="x",
        enabled=True,
        config={"connection_mode": "polling", "home_channel": ""},
        secret_plaintext="123:ABC",
    )
    mgr = _manager(storage)
    tg = next(i for i in mgr._build_instances() if i.channel_type == "telegram")
    bot = mgr._construct(tg)
    from tianshu.gateway.telegram import TelegramBot

    assert isinstance(bot, TelegramBot)


# --------------------------------------------------------------------------
# 3) 旧单配置迁移
# --------------------------------------------------------------------------


def test_legacy_config_migrated_to_default_instance(storage, _master_key):
    # 仅有旧单配置，无 channel_instances 行
    storage.save_channel_config(
        "telegram",
        {"connection_mode": "polling", "home_channel": ""},
        secret_plaintext="999:XYZ",
    )
    assert storage.list_channel_instances("telegram") == []

    mgr = _manager(storage)
    instances = mgr._build_instances()

    # 迁移已写回 channel_instances
    rows = storage.list_channel_instances("telegram")
    assert any(r["instance_id"] == "telegram-default" for r in rows)

    tg = next(i for i in instances if i.channel_type == "telegram")
    assert tg.instance_id == "telegram-default"
    assert tg.settings.bot_token == "999:XYZ"


# --------------------------------------------------------------------------
# 4) outbound 退订（stop() → EventBus.off）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outbound_stop_unsubscribes_from_event_bus(storage):
    bus = EventBus(storage=storage)
    settings = FeishuSettings(
        app_id="x",
        app_secret="y",
        domain="feishu",
        connection_mode="webhook",
        allowed_users=(),
        home_channel="",
        encrypt_key="",
        verification_token="",
        bot_open_id="",
        bot_name="",
        webhook_path="/feishu/webhook",
        ws_reconnect_interval=120,
        text_batch_delay=0.6,
        dedup_cache_size=2048,
        instance_id="feishu-default",
    )
    out = FeishuOutbound(
        settings=settings,
        storage=storage,
        event_bus=bus,
        instance_id="feishu-default",
    )

    # 哨兵：每次 handler 跑到 _lookup_chat_id 就记一次，并返回 None 避免真发送
    calls: list[int] = []

    def _spy(_event):
        calls.append(1)
        return None

    out._lookup_chat_id = _spy  # type: ignore[assignment]

    edict = Edict(
        title="t",
        goal="g",
        source="channel",
        metadata={"channel": "feishu", "instance_id": "feishu-default", "chat_id": "oc"},
    )
    storage.save_edict(edict)
    ev = EventEnvelope(event_type="execution.completed", edict_id=edict.id)

    out.start()
    await bus.emit(ev)
    assert len(calls) == 1  # 订阅后 handler 跑了一次

    out.stop()
    await bus.emit(ev)
    assert len(calls) == 1  # 退订后不再跑
