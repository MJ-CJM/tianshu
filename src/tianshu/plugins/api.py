"""PluginApi — unified registration facade for tools, hooks, channels, providers, skills, commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.plugins.manifest import PluginManifest
from tianshu.tools.registry import ToolDefinition

if TYPE_CHECKING:
    from tianshu.kernel.hooks import HookHandler, HookRegistry, HookType
    from tianshu.notifier.channel_registry import ChannelRegistry
    from tianshu.notifier.channels.base import NotificationChannel
    from tianshu.providers.capabilities import ProviderInfo
    from tianshu.providers.manager import ProviderManager
    from tianshu.skills.loader import SkillsLoader
    from tianshu.storage import Storage
    from tianshu.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class PluginApi:
    """Unified facade for plugin registration.

    Delegates to existing registries:
    - ToolRegistry for tools
    - HookRegistry for hooks
    - ChannelRegistry for notification channels
    - ProviderManager for LLM providers
    - SkillsLoader for skills
    """

    def __init__(
        self,
        storage: Storage,
        tool_registry: ToolRegistry | None = None,
        hook_registry: HookRegistry | None = None,
        channel_registry: ChannelRegistry | None = None,
        provider_manager: ProviderManager | None = None,
        skills_loader: SkillsLoader | None = None,
    ) -> None:
        self._storage = storage
        self._tools = tool_registry
        self._hooks = hook_registry
        self._channels = channel_registry
        self._providers = provider_manager
        self._skills = skills_loader
        self._registered_plugins: dict[str, PluginManifest] = {}

    def register_plugin(self, manifest: PluginManifest) -> None:
        """Register a plugin and persist its metadata."""
        self._registered_plugins[manifest.name] = manifest
        self._storage.save_plugin(
            {
                "name": manifest.name,
                "version": manifest.version,
                "manifest": manifest.model_dump(),
                "sha256": manifest.sha256,
            }
        )
        logger.info("Plugin registered: %s v%s", manifest.name, manifest.version)

    def register_tool(self, name: str, handler, schema: dict | None = None) -> None:
        """Register a tool via ToolRegistry.

        schema 支持键：description / parameters（JSON schema）/ tier / max_result_chars / side_effect。
        """
        if self._tools:
            s = schema or {}
            definition = ToolDefinition(
                name=name,
                description=s.get("description", name),
                parameters=s.get("parameters", {"type": "object", "properties": {}}),
                tier=s.get("tier", 0),
                max_result_chars=s.get("max_result_chars", 8000),
                side_effect=s.get("side_effect", False),
            )
            self._tools.register(name, handler, definition)
            logger.info("Plugin tool registered: %s", name)

    def register_hook(
        self,
        hook_type: HookType,
        handler: HookHandler,
        priority: int = 100,
    ) -> None:
        """Register a hook via HookRegistry."""
        if self._hooks:
            self._hooks.register(hook_type, handler, priority)
            logger.info("Plugin hook registered: %s", hook_type)

    def register_channel(self, channel: NotificationChannel) -> None:
        """Register a notification channel via ChannelRegistry."""
        if self._channels:
            self._channels.register(channel)
            logger.info("Plugin channel registered: %s", channel.name)

    def register_provider(self, info: ProviderInfo) -> None:
        """Register an LLM provider via ProviderManager."""
        if self._providers:
            self._providers.register(info)
            logger.info("Plugin provider registered: %s", info.name)

    def register_skill(self, name: str, content: str) -> None:
        """Register a skill via SkillsLoader."""
        if self._skills and hasattr(self._skills, "register_skill"):
            self._skills.register_skill(name, content)
            logger.info("Plugin skill registered: %s", name)

    def register_command(self, name: str, handler: object) -> None:
        """Register a CLI command. Stored for plugin-provided CLI extensions."""
        if not hasattr(self, "_commands"):
            self._commands: dict[str, object] = {}
        self._commands[name] = handler
        logger.info("Plugin command registered: %s", name)

    def list_commands(self) -> dict[str, object]:
        return getattr(self, "_commands", {})

    def list_plugins(self) -> list[PluginManifest]:
        return list(self._registered_plugins.values())

    def get_plugin(self, name: str) -> PluginManifest | None:
        return self._registered_plugins.get(name)
