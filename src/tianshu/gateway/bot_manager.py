"""ChannelBotManager：每个渠道运行 N 个 bot 实例（多实例支持）。

职责：
- 从 DB（channel_instances）/ 旧单配置（channel_configs）/ env 三级来源构建实例列表，
  旧单配置首次启动时会被迁移成默认实例（写回 channel_instances，供 web UI 可见）。
- 构造对应 FeishuBot / TelegramBot 并 start / stop / reload。
- 单个实例启动失败降级（不阻塞其它实例与 web 服务）。

供 Phase 5 调用的公共方法：
  start_instance(inst) / stop_instance(instance_id) / reload_instance(instance_id, new_settings)
  / status() / get(instance_id) / start_all() / stop_all()。
"""
from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from tianshu.gateway.feishu.settings import from_global_settings as feishu_from_env
from tianshu.gateway.instance import ChannelInstance, default_instance_id
from tianshu.gateway.telegram.settings import from_global_settings as telegram_from_env

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tianshu.bus.event_bus import EventBus
    from tianshu.cost.manager import CostManager
    from tianshu.executor.approvals import ApprovalManager
    from tianshu.executor.executor import Executor
    from tianshu.notifier.notifier import Notifier
    from tianshu.persona.loader import PersonaLoader
    from tianshu.providers.manager import ProviderManager
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_CHANNELS = ("feishu", "telegram")


class ChannelBotManager:
    """管理每个渠道的多个 bot 实例。"""

    def __init__(
        self,
        *,
        storage: Storage,
        event_bus: EventBus,
        approval_manager: ApprovalManager,
        executor: Executor,
        notifier: Notifier,
        persona_loader: PersonaLoader | None,
        provider_manager: ProviderManager | None,
        cost_manager: CostManager | None,
        env_settings,
        app: FastAPI | None,
    ) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._approval_manager = approval_manager
        self._executor = executor
        self._notifier = notifier
        self._persona_loader = persona_loader
        self._provider_manager = provider_manager
        self._cost_manager = cost_manager
        self._env_settings = env_settings
        self._app = app
        self._bots: dict[str, object] = {}

    # --- 实例发现 ---

    def _build_instances(self) -> list[ChannelInstance]:
        """构建所有渠道的实例列表（DB > 旧单配置迁移 > env 兜底）。"""
        instances: list[ChannelInstance] = []
        for channel_type in _CHANNELS:
            instances.extend(self._build_channel_instances(channel_type))
        return instances

    def _build_channel_instances(self, channel_type: str) -> list[ChannelInstance]:
        rows = self._storage.list_channel_instances(channel_type)
        if rows:
            return self._instances_from_rows(channel_type, rows)
        migrated = self._migrate_legacy_if_any(channel_type)
        if migrated is not None:
            return [migrated]
        return [self._env_fallback_instance(channel_type)]

    def _instances_from_rows(self, channel_type: str, rows: list[dict]) -> list[ChannelInstance]:
        builder = self._runtime_builder(channel_type)
        out: list[ChannelInstance] = []
        for row in rows:
            instance_id = row["instance_id"]
            runtime = self._storage.load_channel_instance_runtime(instance_id)
            if runtime is None:
                logger.warning(
                    "[gateway] instance %s (%s) unreadable (secret/vault missing); skipping",
                    instance_id, channel_type,
                )
                continue
            settings = dataclasses.replace(builder(runtime), instance_id=instance_id)
            out.append(
                ChannelInstance(
                    instance_id=instance_id,
                    channel_type=channel_type,
                    label=row.get("label", ""),
                    enabled=row["enabled"],
                    settings=settings,
                )
            )
        return out

    def _migrate_legacy_if_any(self, channel_type: str) -> ChannelInstance | None:
        """旧单配置存在且有凭证 → 迁移成默认实例并返回；否则 None。"""
        legacy_cfg = self._storage.get_channel_config(channel_type)
        if not legacy_cfg:
            return None
        has_creds = (
            bool(legacy_cfg.get("app_id"))
            if channel_type == "feishu"
            else bool(legacy_cfg.get("_has_secret"))
        )
        if not has_creds:
            return None

        legacy = self._storage.load_channel_runtime_config(channel_type)
        if legacy is None:
            logger.warning(
                "[gateway] legacy %s config unreadable (vault missing); not migrating",
                channel_type,
            )
            return None

        secret_key = "app_secret" if channel_type == "feishu" else "bot_token"
        secret = legacy.get(secret_key, "")
        non_secret = {
            k: v
            for k, v in legacy.items()
            if k != secret_key and not k.startswith("_")
        }
        instance_id = default_instance_id(channel_type)
        self._storage.save_channel_instance(
            instance_id=instance_id,
            channel_type=channel_type,
            label="默认",
            enabled=True,
            config=non_secret,
            secret_plaintext=secret or None,
        )
        logger.info(
            "[gateway] migrated legacy %s config to default instance %s",
            channel_type, instance_id,
        )
        builder = self._runtime_builder(channel_type)
        settings = dataclasses.replace(builder(legacy), instance_id=instance_id)
        return ChannelInstance(
            instance_id=instance_id,
            channel_type=channel_type,
            label="默认",
            enabled=True,
            settings=settings,
        )

    def _env_fallback_instance(self, channel_type: str) -> ChannelInstance:
        """无 DB 实例、无旧配置 → env 兜底（env 为空时 enabled=False）。"""
        if channel_type == "feishu":
            settings = feishu_from_env(self._env_settings)
        else:
            settings = telegram_from_env(self._env_settings)
        return ChannelInstance(
            instance_id=default_instance_id(channel_type),
            channel_type=channel_type,
            label="默认(env)",
            enabled=settings.enabled,
            settings=settings,
        )

    @staticmethod
    def _runtime_builder(channel_type: str):
        from tianshu.gateway.tongzheng_api import (
            _build_feishu_settings_from_runtime,
            _build_telegram_settings_from_runtime,
        )
        return (
            _build_feishu_settings_from_runtime
            if channel_type == "feishu"
            else _build_telegram_settings_from_runtime
        )

    # --- 构造 ---

    def _construct(self, inst: ChannelInstance):
        common = dict(
            storage=self._storage,
            event_bus=self._event_bus,
            approval_manager=self._approval_manager,
            executor=self._executor,
            notifier=self._notifier,
            persona_loader=self._persona_loader,
            provider_manager=self._provider_manager,
            cost_manager=self._cost_manager,
            settings=inst.settings,
            instance_id=inst.instance_id,
        )
        if inst.channel_type == "feishu":
            from tianshu.gateway.feishu import FeishuBot
            return FeishuBot(**common)
        from tianshu.gateway.telegram import TelegramBot
        return TelegramBot(**common)

    # --- 生命周期 ---

    async def start_instance(self, inst: ChannelInstance) -> bool:
        """构造并启动单实例。失败降级（log + 返回 False），不抛。"""
        if not inst.enabled or not inst.settings.enabled:
            logger.info(
                "[gateway] instance %s (%s) skipped (enabled=%s, settings.enabled=%s)",
                inst.instance_id, inst.channel_type, inst.enabled, inst.settings.enabled,
            )
            return False
        try:
            inst.settings.validate_or_raise()
            bot = self._construct(inst)
            await bot.start()
            self._bots[inst.instance_id] = bot
            if inst.settings.connection_mode == "webhook" and self._app is not None:
                bot.attach_webhook_router(self._app)
            logger.info(
                "[gateway] instance %s (%s) started (mode=%s)",
                inst.instance_id, inst.channel_type, inst.settings.connection_mode,
            )
            return True
        except Exception:
            logger.exception(
                "[gateway] instance %s (%s) start failed; degrading (other instances unaffected)",
                inst.instance_id, inst.channel_type,
            )
            return False

    async def start_all(self) -> None:
        for inst in self._build_instances():
            await self.start_instance(inst)

    async def stop_instance(self, instance_id: str) -> None:
        bot = self._bots.pop(instance_id, None)
        if bot is None:
            return
        try:
            await bot.stop()
        except Exception:
            logger.exception("[gateway] instance %s stop failed", instance_id)

    async def reload_instance(self, instance_id: str, new_settings) -> bool:
        """热加载已运行实例；若未运行（新启用）则按 DB 配置构造并启动。"""
        bot = self._bots.get(instance_id)
        if bot is not None:
            await bot.reload(new_settings)
            return True
        # 未运行 → 视为新启用：从 DB 取实例配置构造并启动
        row = self._storage.get_channel_instance(instance_id)
        if row is None:
            logger.warning("[gateway] reload_instance: %s not found in DB", instance_id)
            return False
        runtime = self._storage.load_channel_instance_runtime(instance_id)
        if runtime is None:
            logger.warning(
                "[gateway] reload_instance: %s unreadable (secret/vault missing)", instance_id,
            )
            return False
        channel_type = row["channel_type"]
        builder = self._runtime_builder(channel_type)
        settings = dataclasses.replace(builder(runtime), instance_id=instance_id)
        inst = ChannelInstance(
            instance_id=instance_id,
            channel_type=channel_type,
            label=row.get("label", ""),
            enabled=row["enabled"],
            settings=settings,
        )
        return await self.start_instance(inst)

    async def stop_all(self) -> None:
        for instance_id in list(self._bots.keys()):
            await self.stop_instance(instance_id)

    # --- 查询 ---

    def status(self) -> list[dict]:
        out: list[dict] = []
        for instance_id, bot in self._bots.items():
            out.append(
                {
                    "instance_id": instance_id,
                    "running": True,
                    "channel_type": type(bot).__name__.replace("Bot", "").lower(),
                    "mode": bot._settings.connection_mode,  # type: ignore[attr-defined]
                }
            )
        return out

    def get(self, instance_id: str):
        return self._bots.get(instance_id)


__all__ = ["ChannelBotManager"]
