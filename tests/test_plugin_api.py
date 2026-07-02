"""Tests for PluginApi.register_tool."""

from __future__ import annotations

import pytest

from tianshu.plugins.api import PluginApi
from tianshu.tools.registry import ToolDefinition, ToolRegistry


async def _handler(**kwargs):
    return None


@pytest.fixture
def tool_registry():
    return ToolRegistry()


@pytest.fixture
def plugin_api(storage, tool_registry):
    return PluginApi(storage=storage, tool_registry=tool_registry)


class TestRegisterTool:
    def test_register_tool_with_schema_builds_tool_definition(
        self, plugin_api, tool_registry
    ):
        schema = {
            "description": "d",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
        }

        plugin_api.register_tool("t", _handler, schema)

        definition = tool_registry.get_definition("t")
        assert isinstance(definition, ToolDefinition)
        assert definition.name == "t"
        assert definition.description == "d"
        assert definition.parameters == schema["parameters"]

    def test_register_tool_with_none_schema_uses_defaults(
        self, plugin_api, tool_registry
    ):
        plugin_api.register_tool("t2", _handler)

        definition = tool_registry.get_definition("t2")
        assert isinstance(definition, ToolDefinition)
        assert definition.name == "t2"
        assert definition.description == "t2"
        assert definition.parameters == {"type": "object", "properties": {}}
