"""ChannelRegistry / Notifier / feishu / telegram / session rules 装配。

两个函数对应原 lifespan() 中的两段代码：

- `wire_channels`：`# --- ChannelRegistry ---`（含 EVAL_MODE 分支的
  feishu/dingtalk/smtp 渠道注册）+ `# --- Notifier ---` +
  `# --- SessionRuleStore ---` 三个相邻分区，合并。
- `wire_channel_bots`：`# --- Channel bots（多实例）---` 分区，位置在
  Executor/CostManager 之后，此时所有依赖均已在 app.state，直接
  service-locator 取用。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from tianshu.config import TianshuSettings
from tianshu.gateway.bot_manager import ChannelBotManager
from tianshu.notifier.channel_registry import ChannelRegistry
from tianshu.notifier.channels.dingtalk import DingTalkChannel
from tianshu.notifier.channels.email import EmailChannel
from tianshu.notifier.channels.feishu import FeishuChannel
from tianshu.notifier.notifier import Notifier
from tianshu.tools.policy_store import (
    CompositeSessionRuleStore,
    InMemorySessionRuleStore,
    SqliteSessionRuleStore,
)

logger = logging.getLogger(__name__)


def wire_channels(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 ChannelRegistry（按环境变量注册外发渠道）+ Notifier + SessionRuleStore。"""
    storage = app.state.storage

    # --- ChannelRegistry ---
    channel_registry = ChannelRegistry()
    app.state.channel_registry = channel_registry

    # EVAL_MODE：沙箱评估时不挂真实外发渠道，避免评估副作用
    if not settings.eval_mode:
        # Register channels from environment
        if settings.feishu_app_id:
            # app bot 模式：FeishuOutbound 在 FeishuBot.start() 内部直接订阅 EventBus，
            # 不通过 ChannelRegistry。互斥：旧 incoming webhook URL 完全跳过。
            pass
        elif settings.feishu_webhook:
            channel_registry.register(FeishuChannel(settings.feishu_webhook))
        if settings.dingtalk_webhook:
            channel_registry.register(
                DingTalkChannel(
                    settings.dingtalk_webhook,
                    secret=settings.dingtalk_secret,
                )
            )
        if settings.smtp_host:
            channel_registry.register(
                EmailChannel(
                    smtp_host=settings.smtp_host,
                    smtp_port=settings.smtp_port,
                    username=settings.smtp_username,
                    password=settings.smtp_password,
                    from_addr=settings.smtp_from,
                    to_addrs=settings.smtp_to.split(",") if settings.smtp_to else [],
                )
            )
    else:
        logger.info(
            "[eval_mode] skipping real outbound channel registration (feishu/dingtalk/smtp)"
        )

    # --- Notifier ---
    notifier = Notifier(storage=storage, channel_registry=channel_registry)
    app.state.notifier = notifier

    # --- SessionRuleStore ---
    session_rule_store = CompositeSessionRuleStore(
        in_memory=InMemorySessionRuleStore(),
        sqlite=SqliteSessionRuleStore(storage=storage),
    )
    app.state.session_rule_store = session_rule_store


async def wire_channel_bots(app: FastAPI, settings: TianshuSettings) -> None:
    """创建 ChannelBotManager 并启动所有渠道 bot 实例（多实例：每渠道 N 个）。"""
    storage = app.state.storage
    event_bus = app.state.event_bus
    approval_manager = app.state.approval_manager
    executor = app.state.executor
    notifier = app.state.notifier
    persona_loader = app.state.persona_loader
    provider_manager = app.state.provider_manager
    cost_manager = app.state.cost_manager

    # --- Channel bots（多实例：每渠道 N 个 bot 实例）---
    bot_manager = ChannelBotManager(
        storage=storage,
        event_bus=event_bus,
        approval_manager=approval_manager,
        executor=executor,
        notifier=notifier,
        persona_loader=persona_loader,
        provider_manager=provider_manager,
        cost_manager=cost_manager,
        env_settings=settings,
        app=app,
    )
    app.state.bot_manager = bot_manager
    # EVAL_MODE：沙箱评估时不启动 bot（Feishu/Telegram），避免真实消息推送副作用
    if not settings.eval_mode:
        try:
            await bot_manager.start_all()
        except Exception:
            logger.exception("[gateway] bot_manager.start_all failed; web stays up")
    else:
        logger.info("[eval_mode] skipping bot startup (feishu/telegram bots not started)")
    # 向后兼容别名：部分代码/测试读取 app.state.feishu_bot / telegram_bot（默认实例）。
    app.state.feishu_bot = bot_manager.get("feishu-default")
    app.state.telegram_bot = bot_manager.get("telegram-default")
