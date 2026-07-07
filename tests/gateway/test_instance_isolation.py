"""多实例隔离：出站路由 / anchor / list_edicts 三层互不串扰。

镜像 tests/gateway/telegram/test_channel_isolation.py 的 channel 隔离思路，
但聚焦同一渠道下不同 instance_id 的隔离（telegram-default vs telegram-x）。
"""

from __future__ import annotations

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.feishu.outbound import FeishuOutbound
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.gateway.telegram.outbound import TelegramOutbound
from tianshu.models.edict import Edict
from tianshu.models.events import EventEnvelope

from .telegram._helpers import make_settings


def _tg_outbound(storage, instance_id: str, home: str = "") -> TelegramOutbound:
    bus = EventBus(storage=storage)
    return TelegramOutbound(
        settings=make_settings(instance_id=instance_id, home_channel=home),
        storage=storage,
        event_bus=bus,
        instance_id=instance_id,
    )


def _feishu_settings(instance_id: str, home: str = "") -> FeishuSettings:
    return FeishuSettings(
        app_id="x",
        app_secret="y",
        domain="feishu",
        connection_mode="webhook",
        allowed_users=(),
        home_channel=home,
        encrypt_key="",
        verification_token="",
        bot_open_id="",
        bot_name="",
        webhook_path="/feishu/webhook",
        ws_reconnect_interval=120,
        text_batch_delay=0.6,
        dedup_cache_size=2048,
        instance_id=instance_id,
    )


def _fs_outbound(storage, instance_id: str, home: str = "") -> FeishuOutbound:
    bus = EventBus(storage=storage)
    return FeishuOutbound(
        settings=_feishu_settings(instance_id, home),
        storage=storage,
        event_bus=bus,
        instance_id=instance_id,
    )


# --------------------------------------------------------------------------
# 1) 出站路由隔离（telegram）
# --------------------------------------------------------------------------


def test_telegram_outbound_only_owning_instance_delivers(storage):
    default_out = _tg_outbound(storage, "telegram-default")
    x_out = _tg_outbound(storage, "telegram-x")

    tagged = Edict(
        title="t",
        goal="g",
        source="channel",
        metadata={"channel": "telegram", "instance_id": "telegram-x", "chat_id": "555"},
    )
    storage.save_edict(tagged)
    ev = EventEnvelope(event_type="execution.completed", edict_id=tagged.id)

    # 只有 telegram-x 实例的 outbound 投递，default 拒投
    assert default_out._lookup_chat_id(ev) is None
    assert x_out._lookup_chat_id(ev) == "555"


def test_telegram_outbound_legacy_untagged_goes_to_default(storage):
    default_out = _tg_outbound(storage, "telegram-default")
    x_out = _tg_outbound(storage, "telegram-x")

    # 存量敕令：无 instance_id → 回退 telegram-default
    legacy = Edict(
        title="t",
        goal="g",
        source="channel",
        metadata={"channel": "telegram", "chat_id": "oc"},
    )
    storage.save_edict(legacy)
    ev = EventEnvelope(event_type="execution.completed", edict_id=legacy.id)

    assert default_out._lookup_chat_id(ev) == "oc"
    assert x_out._lookup_chat_id(ev) is None


# --------------------------------------------------------------------------
# 2) 出站路由隔离（feishu 镜像，低成本补一份）
# --------------------------------------------------------------------------


def test_feishu_outbound_only_owning_instance_delivers(storage):
    default_out = _fs_outbound(storage, "feishu-default")
    x_out = _fs_outbound(storage, "feishu-x")

    tagged = Edict(
        title="t",
        goal="g",
        source="channel",
        metadata={"channel": "feishu", "instance_id": "feishu-x", "chat_id": "oc_555"},
    )
    storage.save_edict(tagged)
    ev = EventEnvelope(event_type="execution.completed", edict_id=tagged.id)
    assert default_out._lookup_chat_id(ev) is None
    assert x_out._lookup_chat_id(ev) == "oc_555"

    legacy = Edict(
        title="t",
        goal="g",
        source="channel",
        metadata={"channel": "feishu", "chat_id": "oc_old"},
    )
    storage.save_edict(legacy)
    ev2 = EventEnvelope(event_type="execution.completed", edict_id=legacy.id)
    assert default_out._lookup_chat_id(ev2) == "oc_old"
    assert x_out._lookup_chat_id(ev2) is None


# --------------------------------------------------------------------------
# 3) anchor 隔离：同一 chat_id 在不同实例互不碰撞
# --------------------------------------------------------------------------


def test_telegram_anchor_isolated_by_instance(storage):
    storage.set_telegram_anchor("123", "e1", instance_id="telegram-default")
    storage.set_telegram_anchor("123", "e2", instance_id="telegram-x")

    assert storage.get_telegram_anchor("123", instance_id="telegram-default") == "e1"
    assert storage.get_telegram_anchor("123", instance_id="telegram-x") == "e2"


def test_feishu_anchor_isolated_by_instance(storage):
    storage.set_feishu_anchor("oc_1", "e1", instance_id="feishu-default")
    storage.set_feishu_anchor("oc_1", "e2", instance_id="feishu-x")

    assert storage.get_feishu_anchor("oc_1", instance_id="feishu-default") == "e1"
    assert storage.get_feishu_anchor("oc_1", instance_id="feishu-x") == "e2"


# --------------------------------------------------------------------------
# 4) list_edicts 隔离：x 实例只见自己；default 见自己 + 旧无标记
# --------------------------------------------------------------------------


def test_list_edicts_instance_filter(storage):
    e_x = Edict(
        title="x",
        goal="g",
        source="channel",
        metadata={"channel": "telegram", "instance_id": "telegram-x", "chat_id": "1"},
    )
    e_default = Edict(
        title="d",
        goal="g",
        source="channel",
        metadata={"channel": "telegram", "instance_id": "telegram-default", "chat_id": "2"},
    )
    e_legacy = Edict(
        title="l",
        goal="g",
        source="channel",
        metadata={"channel": "telegram", "chat_id": "3"},  # 无 instance_id
    )
    for e in (e_x, e_default, e_legacy):
        storage.save_edict(e)

    x_list, _ = storage.list_edicts(instance_id="telegram-x")
    default_list, _ = storage.list_edicts(instance_id="telegram-default")
    all_list, all_total = storage.list_edicts()

    x_ids = {e.id for e in x_list}
    default_ids = {e.id for e in default_list}
    all_ids = {e.id for e in all_list}

    # telegram-x 只见自己（不含 default、不含 legacy）
    assert x_ids == {e_x.id}
    # telegram-default 见自己 + 旧无标记（继承 channel=telegram 的 legacy）
    assert default_ids == {e_default.id, e_legacy.id}
    # 无 instance 过滤 → 全部三条
    assert {e_x.id, e_default.id, e_legacy.id} <= all_ids
    assert all_total >= 3
