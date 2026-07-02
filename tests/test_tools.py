"""Tests for ToolRegistry."""

import pytest

from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, error_result, ok_result


class TestToolResult:
    def test_ok_result(self):
        r = ok_result("success")
        assert r.content == "success"
        assert r.is_error is False

    def test_error_result(self):
        r = error_result("failed")
        assert r.content == "failed"
        assert r.is_error is True

    def test_frozen(self):
        r = ok_result("x")
        with pytest.raises(AttributeError):
            r.content = "y"


class TestToolRegistry:
    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    async def test_register_and_execute(self, registry):
        async def my_tool(x: int) -> ToolResult:
            return ok_result(f"result: {x}")

        defn = ToolDefinition(
            name="my_tool",
            description="A test tool",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        )
        registry.register("my_tool", my_tool, defn)

        result = await registry.execute("my_tool", {"x": 42})
        assert result.content == "result: 42"
        assert result.is_error is False

    async def test_execute_unknown_tool(self, registry):
        result = await registry.execute("nonexistent", {})
        assert result.is_error is True
        assert "not found" in result.content

    async def test_get_openai_tools(self, registry):
        defn = ToolDefinition(
            name="t1",
            description="desc",
            parameters={"type": "object", "properties": {}},
        )

        async def dummy() -> ToolResult:
            return ok_result("ok")

        registry.register("t1", dummy, defn)

        tools = registry.get_openai_tools()
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "t1"
