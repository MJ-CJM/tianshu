"""PluginApi — unified registration facade for trusted source extensions."""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, Protocol

from tianshu.models.tool_definition import ToolDefinition
from tianshu.plugins.contribution import (
    DEFAULT_PLUGIN_OWNER,
    ContributionDisposeStatus,
    ContributionHandle,
    ContributionKind,
    record_stale_contribution_dispose,
)
from tianshu.plugins.manifest import PluginManifest

if TYPE_CHECKING:
    from tianshu.kernel.hooks import HookHandler, HookRegistry, HookType
    from tianshu.notifier.channel_registry import ChannelRegistry
    from tianshu.notifier.channels.base import NotificationChannel
    from tianshu.providers.capabilities import ProviderInfo
    from tianshu.providers.manager import ProviderManager
    from tianshu.skills.loader import SkillsLoader
    from tianshu.storage import Storage


class _ToolRegistry(Protocol):
    def register(
        self,
        name: str,
        func: Any,
        definition: ToolDefinition,
        *,
        owner: str = "kernel",
        on_conflict: Literal["error", "replace"] = "replace",
    ) -> None: ...

    def is_registered(
        self,
        name: str,
        *,
        owner: str | None = None,
        target: object | None = None,
    ) -> bool: ...

    def unregister(
        self,
        name: str,
        *,
        owner: str | None = None,
        target: object | None = None,
    ) -> bool: ...


logger = logging.getLogger(__name__)


class PluginApi:
    """Unified facade for owned contributions from trusted source extensions.

    This remains an in-process registration seam. It does not import or activate
    third-party plugin entry points.
    """

    def __init__(
        self,
        storage: Storage,
        tool_registry: _ToolRegistry | None = None,
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
        self._commands: dict[str, object] = {}
        self._contributions: dict[str, list[ContributionHandle]] = {}
        self._active_contributions: dict[tuple[ContributionKind, str], ContributionHandle] = {}

    def _noop_handle(
        self,
        *,
        owner: str,
        kind: ContributionKind,
        name: str,
        target: object,
    ) -> ContributionHandle:
        logger.warning("Plugin %s registry unavailable; registration skipped: %s", kind, name)

        def dispose() -> ContributionDisposeStatus:
            return ContributionDisposeStatus.NOOP

        return ContributionHandle(
            owner=owner,
            kind=kind,
            name=name,
            target=target,
            dispose=dispose,
        )

    def _forget_handle(self, handle: ContributionHandle) -> None:
        key = (handle.kind, handle.name)
        if self._active_contributions.get(key) is handle:
            self._active_contributions.pop(key, None)
        handles = self._contributions.get(handle.owner)
        if handles is None:
            return
        handles[:] = [item for item in handles if item is not handle]
        if not handles:
            self._contributions.pop(handle.owner, None)

    def _track_handle(
        self,
        *,
        owner: str,
        kind: ContributionKind,
        name: str,
        target: object,
        is_current: Callable[[], bool],
        dispose_current: Callable[[], bool],
    ) -> ContributionHandle:
        key = (kind, name)
        finished = False
        handle: ContributionHandle

        def dispose() -> ContributionDisposeStatus:
            nonlocal finished
            if finished:
                return ContributionDisposeStatus.NOOP
            if self._active_contributions.get(key) is not handle or not is_current():
                record_stale_contribution_dispose(
                    self._storage,
                    owner=owner,
                    kind=kind,
                    name=name,
                )
                status = ContributionDisposeStatus.SKIPPED_STALE
            elif dispose_current():
                status = ContributionDisposeStatus.DISPOSED
            else:
                status = ContributionDisposeStatus.NOOP
            finished = True
            self._forget_handle(handle)
            return status

        handle = ContributionHandle(
            owner=owner,
            kind=kind,
            name=name,
            target=target,
            dispose=dispose,
        )
        self._active_contributions[key] = handle
        self._contributions.setdefault(owner, []).append(handle)
        return handle

    def register_plugin(self, manifest: PluginManifest) -> None:
        """Catalog a discovered manifest without loading its entry point."""
        self._registered_plugins[manifest.name] = manifest
        self._storage.save_plugin(
            {
                "name": manifest.name,
                "version": manifest.version,
                "manifest": manifest.model_dump(),
                "sha256": manifest.sha256,
                "status": "manifest_only",
            }
        )
        logger.info(
            "Plugin manifest catalogued without code loading: %s v%s",
            manifest.name,
            manifest.version,
        )

    def register_tool(
        self,
        name: str,
        handler,
        schema: dict | None = None,
        *,
        owner: str = DEFAULT_PLUGIN_OWNER,
    ) -> ContributionHandle:
        """Register an owned tool via ToolRegistry."""

        if self._tools is None:
            return self._noop_handle(owner=owner, kind="tool", name=name, target=handler)
        s = schema or {}
        definition = ToolDefinition(
            name=name,
            description=s.get("description", name),
            parameters=s.get("parameters", {"type": "object", "properties": {}}),
            tier=s.get("tier", 0),
            max_result_chars=s.get("max_result_chars", 8000),
            side_effect=s.get("side_effect", False),
        )
        self._tools.register(
            name,
            handler,
            definition,
            owner=owner,
            on_conflict="error",
        )
        logger.info("Plugin tool registered: %s", name)
        return self._track_handle(
            owner=owner,
            kind="tool",
            name=name,
            target=handler,
            is_current=lambda: (
                self._tools is not None
                and self._tools.is_registered(name, owner=owner, target=handler)
            ),
            dispose_current=lambda: (
                self._tools is not None
                and self._tools.unregister(name, owner=owner, target=handler)
            ),
        )

    def register_hook(
        self,
        hook_type: HookType,
        handler: HookHandler,
        priority: int = 100,
        *,
        owner: str = DEFAULT_PLUGIN_OWNER,
    ) -> ContributionHandle:
        """Register an owned lifecycle hook."""

        if self._hooks is None:
            name = f"{hook_type.value}:{id(handler)}"
            return self._noop_handle(owner=owner, kind="hook", name=name, target=handler)

        @wraps(handler)
        async def owned_handler(**kwargs):
            return await handler(**kwargs)

        name = f"{hook_type.value}:{id(owned_handler)}"
        self._hooks.register(hook_type, owned_handler, priority)
        logger.info("Plugin hook registered: %s", hook_type)

        def is_current() -> bool:
            return self._hooks is not None and any(
                entry.handler is owned_handler for entry in self._hooks._hooks.get(hook_type, [])
            )

        def dispose_current() -> bool:
            assert self._hooks is not None
            self._hooks.unregister(hook_type, owned_handler)
            return True

        return self._track_handle(
            owner=owner,
            kind="hook",
            name=name,
            target=owned_handler,
            is_current=is_current,
            dispose_current=dispose_current,
        )

    def register_channel(
        self,
        channel: NotificationChannel,
        *,
        owner: str = DEFAULT_PLUGIN_OWNER,
    ) -> ContributionHandle:
        """Register an owned notification channel."""

        if self._channels is None:
            return self._noop_handle(
                owner=owner,
                kind="channel",
                name=channel.name,
                target=channel,
            )
        self._channels.register(channel)
        logger.info("Plugin channel registered: %s", channel.name)
        return self._track_handle(
            owner=owner,
            kind="channel",
            name=channel.name,
            target=channel,
            is_current=lambda: (
                self._channels is not None and self._channels.get(channel.name) is channel
            ),
            dispose_current=lambda: (
                self._channels is not None and self._channels.unregister(channel.name)
            ),
        )

    def register_provider(
        self,
        info: ProviderInfo,
        *,
        owner: str = DEFAULT_PLUGIN_OWNER,
    ) -> ContributionHandle:
        """Register an owned provider row."""

        if self._providers is None:
            return self._noop_handle(
                owner=owner,
                kind="provider",
                name=info.name,
                target=info,
            )
        self._providers.register(info)
        logger.info("Plugin provider registered: %s", info.name)
        return self._track_handle(
            owner=owner,
            kind="provider",
            name=info.name,
            target=info,
            is_current=lambda: True,
            dispose_current=lambda: (
                self._providers is not None and self._providers.unregister(info.name)
            ),
        )

    def register_skill(
        self,
        name: str,
        content: str,
        *,
        owner: str = DEFAULT_PLUGIN_OWNER,
    ) -> ContributionHandle:
        """Register an owned in-memory skill."""

        if self._skills is None:
            return self._noop_handle(owner=owner, kind="skill", name=name, target=content)
        self._skills.register_skill(name, content)
        logger.info("Plugin skill registered: %s", name)
        return self._track_handle(
            owner=owner,
            kind="skill",
            name=name,
            target=content,
            is_current=lambda: (
                self._skills is not None and self._skills._injected_skills.get(name) is content
            ),
            dispose_current=lambda: (
                self._skills is not None and self._skills.unregister_skill(name)
            ),
        )

    def register_command(
        self,
        name: str,
        handler: object,
        *,
        owner: str = DEFAULT_PLUGIN_OWNER,
    ) -> ContributionHandle:
        """Register an owned CLI command projection."""

        self._commands[name] = handler
        logger.info("Plugin command registered: %s", name)

        def dispose_current() -> bool:
            if self._commands.get(name) is not handler:
                return False
            self._commands.pop(name, None)
            return True

        return self._track_handle(
            owner=owner,
            kind="command",
            name=name,
            target=handler,
            is_current=lambda: self._commands.get(name) is handler,
            dispose_current=dispose_current,
        )

    def dispose_owner(self, owner: str) -> tuple[int, int]:
        """Dispose one owner's contributions in reverse registration order."""

        handles = list(self._contributions.get(owner, ()))
        disposed = 0
        skipped_stale = 0
        for handle in reversed(handles):
            status = handle.dispose()
            if status is ContributionDisposeStatus.DISPOSED:
                disposed += 1
            elif status is ContributionDisposeStatus.SKIPPED_STALE:
                skipped_stale += 1
        return disposed, skipped_stale

    def list_commands(self) -> dict[str, object]:
        return dict(self._commands)

    def list_plugins(self) -> list[PluginManifest]:
        return list(self._registered_plugins.values())

    def get_plugin(self, name: str) -> PluginManifest | None:
        return self._registered_plugins.get(name)
