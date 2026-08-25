"""PluginApi contribution ownership and lifecycle contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.config_manager import ConfigManager
from tianshu.kernel.hooks import HookRegistry, HookResult, HookType
from tianshu.notifier.channel_registry import ChannelRegistry
from tianshu.notifier.channels.base import NotificationChannel
from tianshu.plugins.api import PluginApi
from tianshu.plugins.contribution import ContributionDisposeStatus, ContributionHandle
from tianshu.providers.capabilities import ProviderInfo
from tianshu.providers.manager import ProviderManager
from tianshu.skills.loader import SkillsLoader
from tianshu.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRegistryConflict,
)


async def _handler(**kwargs):
    return None


async def _replacement_handler(**kwargs):
    return None


async def _hook(**kwargs):
    return HookResult()


class _Channel(NotificationChannel):
    def __init__(self, name: str = "test-channel") -> None:
        self._name = name
        self.sent: list[tuple[dict, str]] = []

    @property
    def name(self) -> str:
        return self._name

    async def send(self, message: dict, rendered: str) -> bool:
        self.sent.append((message, rendered))
        return True


@pytest.fixture
def tool_registry():
    return ToolRegistry()


@pytest.fixture
def plugin_api(storage, tool_registry):
    return PluginApi(storage=storage, tool_registry=tool_registry)


def _full_api(
    *,
    storage,
    config_manager: ConfigManager,
    root: Path,
    demo_provider: bool = False,
) -> tuple[
    PluginApi,
    ToolRegistry,
    HookRegistry,
    ChannelRegistry,
    ProviderManager,
    SkillsLoader,
]:
    tools = ToolRegistry()
    hooks = HookRegistry()
    channels = ChannelRegistry()
    providers = ProviderManager(storage, config_manager, demo_mode=demo_provider)
    skills = SkillsLoader(root / "builtin-skills")
    api = PluginApi(
        storage=storage,
        tool_registry=tools,
        hook_registry=hooks,
        channel_registry=channels,
        provider_manager=providers,
        skills_loader=skills,
    )
    return api, tools, hooks, channels, providers, skills


class TestRegisterTool:
    def test_register_tool_with_schema_builds_tool_definition(self, plugin_api, tool_registry):
        schema = {
            "description": "d",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
        }

        handle = plugin_api.register_tool("t", _handler, schema)

        assert isinstance(handle, ContributionHandle)
        assert handle.owner == "plugin:anonymous"
        definition = tool_registry.get_definition("t")
        assert isinstance(definition, ToolDefinition)
        assert definition.name == "t"
        assert definition.description == "d"
        assert definition.parameters == schema["parameters"]

    def test_register_tool_with_none_schema_uses_defaults(self, plugin_api, tool_registry):
        plugin_api.register_tool("t2", _handler)

        definition = tool_registry.get_definition("t2")
        assert isinstance(definition, ToolDefinition)
        assert definition.name == "t2"
        assert definition.description == "t2"
        assert definition.parameters == {"type": "object", "properties": {}}

    def test_plugin_tool_conflict_reports_name_and_existing_owner(
        self, plugin_api, tool_registry
    ) -> None:
        definition = ToolDefinition(name="same", description="same", parameters={})
        tool_registry.register("same", _handler, definition, owner="kernel:existing")

        with pytest.raises(ToolRegistryConflict) as raised:
            plugin_api.register_tool("same", _replacement_handler, owner="plugin:new")

        assert raised.value.name == "same"
        assert raised.value.existing_owner == "kernel:existing"
        assert "same" in str(raised.value)
        assert "kernel:existing" in str(raised.value)


def test_register_six_kinds_disposes_in_reverse_and_restores_state(
    storage,
    config_manager,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, tools, hooks, channels, providers, skills = _full_api(
        storage=storage,
        config_manager=config_manager,
        root=tmp_path,
    )
    owner = "plugin:all"
    order: list[str] = []

    original_tool_unregister = tools.unregister
    original_hook_unregister = hooks.unregister
    original_channel_unregister = channels.unregister
    original_provider_unregister = providers.unregister
    original_skill_unregister = skills.unregister_skill

    def unregister_tool(*args, **kwargs):
        order.append("tool")
        assert "command" in api.list_commands()
        return original_tool_unregister(*args, **kwargs)

    def unregister_hook(*args, **kwargs):
        order.append("hook")
        return original_hook_unregister(*args, **kwargs)

    def unregister_channel(*args, **kwargs):
        order.append("channel")
        return original_channel_unregister(*args, **kwargs)

    def unregister_provider(*args, **kwargs):
        order.append("provider")
        return original_provider_unregister(*args, **kwargs)

    def unregister_skill(*args, **kwargs):
        order.append("skill")
        return original_skill_unregister(*args, **kwargs)

    monkeypatch.setattr(tools, "unregister", unregister_tool)
    monkeypatch.setattr(hooks, "unregister", unregister_hook)
    monkeypatch.setattr(channels, "unregister", unregister_channel)
    monkeypatch.setattr(providers, "unregister", unregister_provider)
    monkeypatch.setattr(skills, "unregister_skill", unregister_skill)

    # Command is deliberately first: reverse disposal must leave it until after tool.
    handles = [
        api.register_command("command", object(), owner=owner),
        api.register_tool("tool", _handler, owner=owner),
        api.register_hook(HookType.AGENT_END, _hook, owner=owner),
        api.register_channel(_Channel(), owner=owner),
        api.register_provider(ProviderInfo(name="provider", model="test"), owner=owner),
        api.register_skill("skill", "skill body", owner=owner),
    ]

    assert [handle.kind for handle in handles] == [
        "command",
        "tool",
        "hook",
        "channel",
        "provider",
        "skill",
    ]
    assert api.dispose_owner(owner) == (6, 0)
    assert order == ["skill", "provider", "channel", "hook", "tool"]
    assert tools.get_definition("tool") is None
    assert hooks._hooks[HookType.AGENT_END] == []
    assert channels.get("test-channel") is None
    assert providers.get_provider("provider") is None
    assert skills.get_skill("skill") is None
    assert api.list_commands() == {}
    assert api.dispose_owner(owner) == (0, 0)


def test_one_hundred_register_dispose_cycles_leave_no_contributions(
    storage,
    config_manager,
    tmp_path,
) -> None:
    api, tools, hooks, channels, providers, skills = _full_api(
        storage=storage,
        config_manager=config_manager,
        root=tmp_path,
    )
    initial = (
        dict(tools._tools),
        dict(tools._owners),
        dict(channels._channels),
        dict(skills._injected_skills),
        dict(api._commands),
        list(hooks._hooks[HookType.AGENT_END]),
    )

    for index in range(100):
        owner = f"plugin:loop-{index}"

        async def loop_hook(**kwargs):
            return HookResult()

        api.register_tool("loop-tool", _handler, owner=owner)
        api.register_hook(HookType.AGENT_END, loop_hook, owner=owner)
        api.register_channel(_Channel("loop-channel"), owner=owner)
        api.register_provider(ProviderInfo(name="loop-provider", model="test"), owner=owner)
        api.register_skill("loop-skill", "loop body", owner=owner)
        api.register_command("loop-command", object(), owner=owner)

        assert api.dispose_owner(owner) == (6, 0)

    assert (
        tools._tools,
        tools._owners,
        channels._channels,
        skills._injected_skills,
        api._commands,
        hooks._hooks[HookType.AGENT_END],
    ) == initial
    assert api._contributions == {}
    assert api._active_contributions == {}


def test_same_hook_handler_is_owned_as_two_independent_contributions(storage) -> None:
    hooks = HookRegistry()
    api = PluginApi(storage=storage, hook_registry=hooks)

    api.register_hook(HookType.AGENT_END, _hook, owner="plugin:A")
    api.register_hook(HookType.AGENT_END, _hook, owner="plugin:B")

    entries = hooks._hooks[HookType.AGENT_END]
    assert len(entries) == 2
    assert entries[0].handler is not entries[1].handler
    assert api.dispose_owner("plugin:A") == (1, 0)
    assert len(hooks._hooks[HookType.AGENT_END]) == 1
    assert api.dispose_owner("plugin:B") == (1, 0)
    assert hooks._hooks[HookType.AGENT_END] == []


def test_dispose_failure_keeps_owner_tracking_for_retry(
    storage,
    config_manager,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api, tools, _, _, providers, _ = _full_api(
        storage=storage,
        config_manager=config_manager,
        root=tmp_path,
    )
    owner = "plugin:retry"
    api.register_tool("retry-tool", _handler, owner=owner)
    api.register_provider(ProviderInfo(name="retry-provider", model="test"), owner=owner)
    original_unregister = providers.unregister

    def fail_unregister(_name: str) -> bool:
        raise RuntimeError("transient dispose failure")

    monkeypatch.setattr(providers, "unregister", fail_unregister)
    with pytest.raises(RuntimeError, match="transient dispose failure"):
        api.dispose_owner(owner)

    assert tools.get_definition("retry-tool") is not None
    assert len(api._contributions[owner]) == 2
    monkeypatch.setattr(providers, "unregister", original_unregister)
    assert api.dispose_owner(owner) == (2, 0)
    assert tools.get_definition("retry-tool") is None
    assert api._contributions == {}


def test_stale_tool_handle_does_not_remove_replacement_and_is_audited(
    storage,
    tool_registry,
) -> None:
    api = PluginApi(storage=storage, tool_registry=tool_registry)
    api.register_tool("x", _handler, owner="plugin:A")
    replacement_definition = ToolDefinition(name="x", description="B", parameters={})
    tool_registry.register(
        "x",
        _replacement_handler,
        replacement_definition,
        owner="plugin:B",
    )

    assert api.dispose_owner("plugin:A") == (0, 1)
    assert tool_registry._tools["x"][1] is _replacement_handler
    assert tool_registry._owners["x"] == "plugin:B"
    assert [event.action for event in storage.list_system_audit()] == ["contribution_dispose_stale"]


def test_missing_optional_registries_return_idempotent_noop_handles(storage) -> None:
    api = PluginApi(storage=storage)
    handles = [
        api.register_tool("tool", _handler),
        api.register_hook(HookType.AGENT_END, _hook),
        api.register_channel(_Channel()),
        api.register_provider(ProviderInfo(name="provider", model="test")),
        api.register_skill("skill", "body"),
    ]

    assert all(handle.dispose() is ContributionDisposeStatus.NOOP for handle in handles)
    assert all(handle.dispose() is ContributionDisposeStatus.NOOP for handle in handles)
    assert api.dispose_owner("plugin:anonymous") == (0, 0)


def test_provider_demo_dispose_does_not_claim_persistent_row_was_removed(
    storage,
    config_manager,
    tmp_path,
) -> None:
    api, _, _, _, providers, _ = _full_api(
        storage=storage,
        config_manager=config_manager,
        root=tmp_path,
        demo_provider=True,
    )
    api.register_provider(
        ProviderInfo(name="demo-provider", model="demo"),
        owner="plugin:demo",
    )

    assert api.dispose_owner("plugin:demo") == (0, 0)
    assert providers.get_provider("demo-provider") is not None
    assert api._contributions == {}
    assert api._active_contributions == {}


def test_stale_provider_owner_does_not_remove_same_name_replacement(
    storage,
    config_manager,
    tmp_path,
) -> None:
    api, _, _, _, providers, _ = _full_api(
        storage=storage,
        config_manager=config_manager,
        root=tmp_path,
    )
    api.register_provider(
        ProviderInfo(name="shared-provider", model="model-a"),
        owner="plugin:A",
    )
    api.register_provider(
        ProviderInfo(name="shared-provider", model="model-b"),
        owner="plugin:B",
    )

    assert api.dispose_owner("plugin:A") == (0, 1)
    current = providers.get_provider("shared-provider")
    assert current is not None
    assert current.model == "model-b"
    assert api.dispose_owner("plugin:B") == (1, 0)
    assert providers.get_provider("shared-provider") is None


def test_skill_dispose_invalidates_metadata_content_and_digest_caches(
    storage,
    tmp_path,
) -> None:
    builtin = tmp_path / "builtin-skills"
    builtin.mkdir()
    skills = SkillsLoader(builtin)
    api = PluginApi(storage=storage, skills_loader=skills)
    baseline_digest = skills.content_digest()
    assert skills.list_all_metadata() == []

    api.register_skill("cached-skill", "cached body", owner="plugin:cached")
    injected_digest = skills.content_digest()
    assert injected_digest != baseline_digest
    assert skills.get_skill("cached-skill")["content"] == "cached body"
    assert [item["name"] for item in skills.list_all_metadata()] == ["cached-skill"]

    assert api.dispose_owner("plugin:cached") == (1, 0)
    assert skills.get_skill("cached-skill") is None
    assert skills.list_all_metadata() == []
    assert skills.content_digest() == baseline_digest


def test_skill_dispose_reveals_same_name_builtin_and_refreshes_all_caches(
    storage,
    tmp_path,
) -> None:
    builtin = tmp_path / "builtin-skills"
    skill_dir = builtin / "shared-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("builtin body", encoding="utf-8")
    skills = SkillsLoader(builtin)
    api = PluginApi(storage=storage, skills_loader=skills)
    baseline_digest = skills.content_digest()
    assert skills.get_skill("shared-skill")["content"] == "builtin body"
    assert [item["name"] for item in skills.list_all_metadata()] == ["shared-skill"]

    api.register_skill("shared-skill", "injected body", owner="plugin:skill")
    assert skills.get_skill("shared-skill")["content"] == "injected body"
    assert skills.content_digest() != baseline_digest

    assert api.dispose_owner("plugin:skill") == (1, 0)
    assert skills.get_skill("shared-skill")["content"] == "builtin body"
    assert [item["name"] for item in skills.list_all_metadata()] == ["shared-skill"]
    assert skills.content_digest() == baseline_digest


def test_list_commands_is_an_independent_projection(storage) -> None:
    api = PluginApi(storage=storage)
    api.register_command("one", object(), owner="plugin:command")

    listed = api.list_commands()
    listed.clear()

    assert list(api.list_commands()) == ["one"]
