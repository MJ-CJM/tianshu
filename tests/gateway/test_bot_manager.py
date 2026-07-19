"""ChannelBotManager._build_instances 三级来源 + outbound 退订。

覆盖：
- env 兜底：空 DB → feishu-default / telegram-default 各一个，无凭证 → 不启用。
- DB 实例：channel_instances 有行 → 用其 runtime（含解密 bot_token）构造。
- 旧单配置迁移：channel_configs 有凭证 → 首次 _build 时迁移成 *-default 实例。
- outbound.stop() → EventBus.off → 已停实例不再收事件。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.gateway.bot_manager import ChannelBotManager
from tianshu.gateway.feishu.outbound import FeishuOutbound
from tianshu.gateway.feishu.settings import FeishuSettings
from tianshu.gateway.instance import ChannelInstance
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
        event_bus=EventBus(),
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
    bus = EventBus()
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


@pytest.mark.asyncio
async def test_webhook_public_path_exists_only_while_instance_runs(storage, monkeypatch):
    app = FastAPI()
    app.state.public_webhook_paths = set()
    manager = ChannelBotManager(
        storage=storage,
        event_bus=EventBus(),
        approval_manager=MagicMock(),
        executor=MagicMock(),
        notifier=MagicMock(),
        persona_loader=MagicMock(),
        provider_manager=MagicMock(),
        cost_manager=MagicMock(),
        env_settings=SimpleNamespace(security_mode="trusted-local"),
        app=app,
    )
    settings = SimpleNamespace(
        enabled=True,
        connection_mode="webhook",
        webhook_path="/channels/feishu/custom",
        encrypt_key="encrypt-key",
        verification_token="",
        validate_or_raise=lambda: None,
    )
    instance = ChannelInstance(
        instance_id="custom",
        channel_type="feishu",
        label="custom",
        enabled=True,
        settings=settings,
    )
    bot = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        attach_webhook_router=MagicMock(),
    )
    monkeypatch.setattr(manager, "_construct", lambda _instance: bot)

    assert await manager.start_instance(instance) is True
    assert app.state.public_webhook_paths == {"/channels/feishu/custom"}

    await manager.stop_instance("custom")
    assert app.state.public_webhook_paths == set()


def _webhook_manager(storage, *, security_mode: str = "trusted-local"):
    app = FastAPI()
    app.state.public_webhook_paths = set()
    manager = ChannelBotManager(
        storage=storage,
        event_bus=EventBus(),
        approval_manager=MagicMock(),
        executor=MagicMock(),
        notifier=MagicMock(),
        persona_loader=MagicMock(),
        provider_manager=MagicMock(),
        cost_manager=MagicMock(),
        env_settings=SimpleNamespace(security_mode=security_mode),
        app=app,
    )
    bot = SimpleNamespace(
        start=AsyncMock(),
        stop=AsyncMock(),
        attach_webhook_router=MagicMock(),
    )
    manager._construct = lambda _instance: bot  # type: ignore[method-assign]
    return manager, app, bot


def _webhook_instance(
    *,
    instance_id: str,
    channel_type: str,
    path: str,
    encrypt_key: str = "encrypt-key",
    verification_token: str = "",
) -> ChannelInstance:
    settings = SimpleNamespace(
        enabled=True,
        connection_mode="webhook",
        webhook_path=path,
        encrypt_key=encrypt_key,
        verification_token=verification_token,
        validate_or_raise=lambda: None,
    )
    return ChannelInstance(
        instance_id=instance_id,
        channel_type=channel_type,
        label=instance_id,
        enabled=True,
        settings=settings,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("channel_type", "path"),
    [
        ("feishu", "/api/edicts"),
        ("feishu", "/channels/telegram/inbound"),
        ("telegram", "/channels/feishu/inbound"),
        ("feishu", "/channels/feishu/../api/edicts"),
        ("feishu", "/channels/feishu/{instance}"),
    ],
)
async def test_webhook_rejects_paths_outside_provider_namespace(
    storage,
    channel_type,
    path,
) -> None:
    manager, app, bot = _webhook_manager(storage)
    instance = _webhook_instance(
        instance_id=f"{channel_type}-bad",
        channel_type=channel_type,
        path=path,
    )

    assert await manager.start_instance(instance) is False
    assert app.state.public_webhook_paths == set()
    bot.start.assert_not_awaited()
    bot.attach_webhook_router.assert_not_called()


@pytest.mark.asyncio
async def test_webhook_rejects_existing_route_and_duplicate_instance_path(storage) -> None:
    manager, app, bot = _webhook_manager(storage)

    @app.post("/channels/feishu/existing")
    async def existing_route():
        return {"ok": True}

    @app.post("/channels/feishu/dynamic/{tail}")
    async def dynamic_route(tail: str):
        return {"tail": tail}

    conflict = _webhook_instance(
        instance_id="feishu-conflict",
        channel_type="feishu",
        path="/channels/feishu/existing",
    )
    dynamic_conflict = _webhook_instance(
        instance_id="feishu-dynamic-conflict",
        channel_type="feishu",
        path="/channels/feishu/dynamic/inbound",
    )
    first = _webhook_instance(
        instance_id="feishu-first",
        channel_type="feishu",
        path="/channels/feishu/shared",
    )
    duplicate = _webhook_instance(
        instance_id="feishu-duplicate",
        channel_type="feishu",
        path="/channels/feishu/shared",
    )

    assert await manager.start_instance(conflict) is False
    assert await manager.start_instance(dynamic_conflict) is False
    assert await manager.start_instance(first) is True
    assert await manager.start_instance(duplicate) is False
    assert app.state.public_webhook_paths == {"/channels/feishu/shared"}
    assert bot.start.await_count == 1


@pytest.mark.asyncio
async def test_webhook_ignores_get_only_spa_fallback_and_post_reaches_provider(
    storage,
    tmp_path,
) -> None:
    from fastapi.testclient import TestClient

    from tianshu.web import mount_web

    manager, app, bot = _webhook_manager(storage)
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    assert mount_web(app, str(static_dir)) is True

    instance = _webhook_instance(
        instance_id="feishu-spa-compatible",
        channel_type="feishu",
        path="/channels/feishu/inbound",
    )

    def attach(target_app: FastAPI) -> None:
        @target_app.post(instance.settings.webhook_path)
        async def provider_handler():
            return {"handler": "provider"}

    bot.attach_webhook_router.side_effect = attach

    assert await manager.start_instance(instance) is True
    with TestClient(app) as client:
        response = client.post(instance.settings.webhook_path, json={"event": "test"})

    assert response.status_code == 200
    assert response.json() == {"handler": "provider"}


@pytest.mark.asyncio
async def test_webhook_rejects_conflicting_mount(storage) -> None:
    manager, app, bot = _webhook_manager(storage)
    app.mount("/channels/feishu", FastAPI())
    instance = _webhook_instance(
        instance_id="feishu-mount-conflict",
        channel_type="feishu",
        path="/channels/feishu/inbound",
    )

    assert await manager.start_instance(instance) is False
    bot.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_secure_remote_feishu_webhook_requires_verifier(storage) -> None:
    manager, app, bot = _webhook_manager(storage, security_mode="secure-remote")
    instance = _webhook_instance(
        instance_id="feishu-empty-verifier",
        channel_type="feishu",
        path="/channels/feishu/inbound",
        encrypt_key="",
        verification_token="",
    )

    assert await manager.start_instance(instance) is False
    assert app.state.public_webhook_paths == set()
    bot.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_bot_start_failure_always_attempts_cleanup(storage, monkeypatch) -> None:
    manager, _, bot = _webhook_manager(storage)
    instance = _webhook_instance(
        instance_id="feishu-partial",
        channel_type="feishu",
        path="/channels/feishu/partial",
    )
    bot.start.side_effect = RuntimeError("allocated then failed")
    monkeypatch.setattr(manager, "_construct", lambda _instance: bot)

    assert await manager.start_instance(instance) is False
    bot.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_webhook_stop_removes_dynamic_route_and_allows_restart(storage) -> None:
    manager, app, bot = _webhook_manager(storage)
    instance = _webhook_instance(
        instance_id="feishu-restart",
        channel_type="feishu",
        path="/channels/feishu/restart",
    )

    def attach(target_app: FastAPI) -> None:
        async def inbound():
            return {"ok": True}

        target_app.post(instance.settings.webhook_path)(inbound)

    bot.attach_webhook_router.side_effect = attach

    assert await manager.start_instance(instance) is True
    assert any(getattr(route, "path", None) == "/channels/feishu/restart" for route in app.routes)
    await manager.stop_instance(instance.instance_id)
    assert not any(
        getattr(route, "path", None) == "/channels/feishu/restart" for route in app.routes
    )

    assert await manager.start_instance(instance) is True
    assert app.state.public_webhook_paths == {"/channels/feishu/restart"}


@pytest.mark.asyncio
async def test_webhook_reload_rebinds_router_to_new_settings(storage, monkeypatch) -> None:
    manager, app, _ = _webhook_manager(storage)
    old = _webhook_instance(
        instance_id="feishu-reload",
        channel_type="feishu",
        path="/channels/feishu/old",
    )
    new_settings = SimpleNamespace(
        **{
            **vars(old.settings),
            "webhook_path": "/channels/feishu/new",
        }
    )
    new_settings.validate_or_raise = lambda: None

    def make_bot(path: str):
        bot = SimpleNamespace(
            start=AsyncMock(),
            stop=AsyncMock(),
            reload=AsyncMock(),
            attach_webhook_router=MagicMock(),
        )

        def attach(target_app: FastAPI) -> None:
            async def inbound():
                return {"ok": True}

            target_app.post(path)(inbound)

        bot.attach_webhook_router.side_effect = attach
        return bot

    old_bot = make_bot(old.settings.webhook_path)
    new_bot = make_bot(new_settings.webhook_path)
    bots = iter((old_bot, new_bot))
    monkeypatch.setattr(manager, "_construct", lambda _instance: next(bots))

    assert await manager.start_instance(old) is True
    assert await manager.reload_instance(old.instance_id, new_settings) is True

    old_bot.stop.assert_awaited_once()
    old_bot.reload.assert_not_awaited()
    new_bot.start.assert_awaited_once()
    assert app.state.public_webhook_paths == {"/channels/feishu/new"}
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/channels/feishu/old" not in paths
    assert "/channels/feishu/new" in paths


@pytest.mark.asyncio
async def test_invalid_webhook_reload_keeps_known_good_route(storage) -> None:
    manager, app, bot = _webhook_manager(storage)
    old = _webhook_instance(
        instance_id="feishu-stable",
        channel_type="feishu",
        path="/channels/feishu/stable",
    )

    def attach(target_app: FastAPI) -> None:
        async def inbound():
            return {"ok": True}

        target_app.post(old.settings.webhook_path)(inbound)

    bot.attach_webhook_router.side_effect = attach
    invalid_settings = SimpleNamespace(**{**vars(old.settings), "webhook_path": "/api/edicts"})
    invalid_settings.validate_or_raise = lambda: None

    assert await manager.start_instance(old) is True
    assert await manager.reload_instance(old.instance_id, invalid_settings) is False

    bot.stop.assert_not_awaited()
    assert manager.get(old.instance_id) is bot
    assert app.state.public_webhook_paths == {"/channels/feishu/stable"}
