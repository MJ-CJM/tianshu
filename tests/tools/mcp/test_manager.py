"""MCPManager 端到端集测：真实 stdio FastMCP 子进程。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tianshu.tools.mcp import MCPManager
from tianshu.tools.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    ToolFilter,
)
from tianshu.tools.registry import ToolRegistry

_FIXTURE_PATH = Path(__file__).parent / "_fixture_server.py"


def _fixture_server_config(
    name: str = "fixture",
    *,
    enabled: bool = True,
    default_tier: int = 0,
    tools: ToolFilter | None = None,
    tool_overrides: dict[str, int] | None = None,
) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=[str(_FIXTURE_PATH)],
        enabled=enabled,
        default_tier=default_tier,
        tools=tools or ToolFilter(),
        tool_overrides=tool_overrides or {},
    )


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def manager(registry: ToolRegistry) -> MCPManager:
    # config_path 故意指向不存在的文件 — 我们在测试里直接喂 config 进去
    return MCPManager(registry, config_path="/tmp/__tianshu_test_no_such_file__.yaml")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_start_registers_tools(manager: MCPManager, registry: ToolRegistry) -> None:
    manager._config = MCPConfig(mcp_servers={"fx": _fixture_server_config(name="fx")})
    try:
        await manager.start()

        names = {d.name for d in registry.list_definitions()}
        assert "mcp_fx_echo" in names
        assert "mcp_fx_add" in names

        echo_defn = registry.get_definition("mcp_fx_echo")
        assert echo_defn is not None
        assert echo_defn.tier == 0  # default_tier
        assert "[via MCP/fx]" in echo_defn.description
        assert echo_defn.parameters.get("type") == "object"
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_executor_can_call_mcp_tool(manager: MCPManager, registry: ToolRegistry) -> None:
    manager._config = MCPConfig(mcp_servers={"fx": _fixture_server_config(name="fx")})
    try:
        await manager.start()

        result = await registry.execute("mcp_fx_echo", {"text": "hello"})
        assert result.is_error is False
        assert "echo:hello" in result.content

        add_result = await registry.execute("mcp_fx_add", {"a": 3, "b": 4})
        assert add_result.is_error is False
        assert "7" in add_result.content
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_disabled_server_skipped(manager: MCPManager, registry: ToolRegistry) -> None:
    manager._config = MCPConfig(
        mcp_servers={
            "fx": _fixture_server_config(name="fx", enabled=False),
        }
    )
    try:
        await manager.start()
        names = {d.name for d in registry.list_definitions()}
        assert not any(n.startswith("mcp_fx_") for n in names)
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_include_filter_applied(manager: MCPManager, registry: ToolRegistry) -> None:
    manager._config = MCPConfig(
        mcp_servers={
            "fx": _fixture_server_config(
                name="fx",
                tools=ToolFilter(include=["echo"]),
            )
        }
    )
    try:
        await manager.start()
        names = {d.name for d in registry.list_definitions()}
        assert "mcp_fx_echo" in names
        assert "mcp_fx_add" not in names
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exclude_filter_applied(manager: MCPManager, registry: ToolRegistry) -> None:
    manager._config = MCPConfig(
        mcp_servers={
            "fx": _fixture_server_config(
                name="fx",
                tools=ToolFilter(exclude=["add"]),
            )
        }
    )
    try:
        await manager.start()
        names = {d.name for d in registry.list_definitions()}
        assert "mcp_fx_echo" in names
        assert "mcp_fx_add" not in names
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tool_overrides_change_tier(manager: MCPManager, registry: ToolRegistry) -> None:
    manager._config = MCPConfig(
        mcp_servers={
            "fx": _fixture_server_config(
                name="fx",
                default_tier=2,
                tool_overrides={"echo": 0},
            )
        }
    )
    try:
        await manager.start()
        echo_defn = registry.get_definition("mcp_fx_echo")
        add_defn = registry.get_definition("mcp_fx_add")
        assert echo_defn is not None and echo_defn.tier == 0
        assert add_defn is not None and add_defn.tier == 2
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failing_server_does_not_break_others(
    manager: MCPManager,
    registry: ToolRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """启动失败的 server 应被记录、跳过；其他 server 仍然注册成功。"""
    # 压缩重试以便测试快速结束（生产仍按 module 默认 5 次 + 退避）
    from tianshu.tools.mcp import client as client_module

    monkeypatch.setattr(client_module, "MAX_RECONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(client_module, "MAX_BACKOFF_SECONDS", 0)

    manager._config = MCPConfig(
        mcp_servers={
            "broken": MCPServerConfig(
                name="broken",
                transport="stdio",
                command="/no/such/command-definitely-missing",
                args=[],
                enabled=True,
            ),
            "ok": _fixture_server_config(name="ok"),
        }
    )
    try:
        await manager.start()
        names = {d.name for d in registry.list_definitions()}
        # broken 没注册，ok 注册了
        assert not any(n.startswith("mcp_broken_") for n in names)
        assert "mcp_ok_echo" in names
        # broken 也没出现在 sessions
        assert "broken" not in manager.sessions
        assert "ok" in manager.sessions
    finally:
        await manager.shutdown()
