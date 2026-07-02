"""winding_down 阶段下副作用工具被拦截测试。"""

from __future__ import annotations

import pytest

from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult


@pytest.fixture
def registry():
    r = ToolRegistry()

    async def write_handler(**kwargs):
        return ToolResult(content="written", is_error=False)

    async def read_handler(**kwargs):
        return ToolResult(content="read", is_error=False)

    r.register(
        "write_file",
        write_handler,
        ToolDefinition(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {}, "required": []},
            side_effect=True,
        ),
    )
    r.register(
        "read_file",
        read_handler,
        ToolDefinition(
            name="read_file",
            description="read",
            parameters={"type": "object", "properties": {}, "required": []},
            side_effect=False,
        ),
    )
    return r


@pytest.mark.asyncio
async def test_active_phase_allows_side_effect(registry):
    result = await registry.execute("write_file", {}, lifecycle_phase="active")
    assert result.is_error is False
    assert result.content == "written"


@pytest.mark.asyncio
async def test_winding_down_blocks_side_effect(registry):
    result = await registry.execute("write_file", {}, lifecycle_phase="winding_down")
    assert result.is_error is True
    assert "winding_down" in (result.content or "")


@pytest.mark.asyncio
async def test_winding_down_allows_read_only(registry):
    result = await registry.execute("read_file", {}, lifecycle_phase="winding_down")
    assert result.is_error is False
    assert result.content == "read"


@pytest.mark.asyncio
async def test_default_lifecycle_phase_is_active(registry):
    """不传 phase 时默认 active，副作用工具放行——保证向后兼容。"""
    result = await registry.execute("write_file", {})
    assert result.is_error is False
