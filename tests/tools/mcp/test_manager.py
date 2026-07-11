"""MCPManager 端到端集测：真实 stdio FastMCP 子进程。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path

import pytest

from tianshu.executor.execution_gateway import ExecutionGateway, ExecutionRequest
from tianshu.tools.mcp import MCPManager
from tianshu.tools.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    ToolFilter,
)
from tianshu.tools.registry import ToolRegistry

_FIXTURE_PATH = Path(__file__).parent / "_fixture_server.py"


class _RecordingGateway(ExecutionGateway):
    def __init__(self) -> None:
        super().__init__(termination_grace_seconds=0.05)
        self.requests: list[ExecutionRequest] = []

    async def start(self, request: ExecutionRequest):
        self.requests.append(request)
        return await super().start(request)


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
def manager(registry: ToolRegistry, tmp_path: Path) -> MCPManager:
    # config_path 故意指向不存在的文件 — 我们在测试里直接喂 config 进去
    return MCPManager(
        registry,
        execution_gateway=_RecordingGateway(),
        workspace_root=tmp_path,
        security_mode="trusted-local",
        config_path="/tmp/__tianshu_test_no_such_file__.yaml",
    )


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

        payload = "x" * (128 * 1024)
        large_result = await registry.execute("mcp_fx_echo", {"text": payload})
        assert large_result.is_error is False
        assert payload in large_result.content

        [request] = manager._execution_gateway.requests
        assert request.purpose == "mcp_stdio"
        assert request.mcp_server_name == "fx"
        assert request.stdin_mode == "pipe"
        assert request.actor.id == "system:mcp"
        assert request.correlation_id
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_shutdown_retains_terminal_gateway_receipt(
    manager: MCPManager,
) -> None:
    manager._config = MCPConfig(mcp_servers={"fx": _fixture_server_config(name="fx")})
    await asyncio.wait_for(manager.start(), timeout=2)
    session = manager.sessions["fx"]

    await manager.shutdown()

    receipt = session.terminal_receipt
    assert receipt is not None
    assert receipt.purpose == "mcp_stdio"
    assert receipt.mcp_server_name == "fx"
    assert receipt.command_admission == "transitional_mcp_config_g1_6_pending"
    assert receipt.status in {"succeeded", "cancelled"}
    assert manager.terminal_receipts["fx"] == receipt


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_manager_can_restart_and_register_tools_after_shutdown(
    manager: MCPManager,
    registry: ToolRegistry,
) -> None:
    manager._config = MCPConfig(mcp_servers={"fx": _fixture_server_config(name="fx")})
    await manager.start()
    await manager.shutdown()
    for name in list(registry._tools):
        if name.startswith("mcp_fx_"):
            del registry._tools[name]

    try:
        await manager.start()
        assert "fx" in manager.sessions
        assert registry.get_definition("mcp_fx_echo") is not None
        result = await registry.execute("mcp_fx_echo", {"text": "reloaded"})
        assert result.is_error is False
        assert "echo:reloaded" in result.content
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_api_restart_helper_restores_tools_on_same_manager(
    manager: MCPManager,
    registry: ToolRegistry,
) -> None:
    from tianshu.gateway.mcp_api import _restart_mcp_sessions

    manager._config = MCPConfig(mcp_servers={"fx": _fixture_server_config(name="fx")})
    await manager.start()

    await _restart_mcp_sessions(manager, registry)

    try:
        assert "fx" in manager.sessions
        assert registry.get_definition("mcp_fx_echo") is not None
    finally:
        await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stdio_env_uses_refs_and_redacts_values_everywhere(
    manager: MCPManager,
    registry: ToolRegistry,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "mcp-sentinel-value-3f8921"
    monkeypatch.setenv("MCP_TEST_SENTINEL", sentinel)
    monkeypatch.setenv("UNRELATED_PARENT_SECRET", "must-not-pass")
    cfg = _fixture_server_config(name="fx").model_copy(
        update={"env": {"MCP_SECRET": "${MCP_TEST_SENTINEL}"}}
    )
    manager._config = MCPConfig(mcp_servers={"fx": cfg})
    await manager.start()
    try:
        secret_result = await registry.execute(
            "mcp_fx_env_value",
            {"name": "MCP_SECRET"},
        )
        unrelated_result = await registry.execute(
            "mcp_fx_env_value",
            {"name": "UNRELATED_PARENT_SECRET"},
        )

        assert sentinel not in secret_result.content
        assert "[REDACTED" in secret_result.content
        assert "must-not-pass" not in unrelated_result.content
    finally:
        await manager.shutdown()

    receipt = manager.terminal_receipts["fx"]
    serialized = receipt.model_dump_json()
    assert receipt.secret_refs == ("MCP_TEST_SENTINEL",)
    assert "MCP_SECRET" in receipt.env_keys
    assert "UNRELATED_PARENT_SECRET" not in receipt.env_keys
    assert sentinel not in serialized
    assert sentinel not in caplog.text


async def _wait_for_file(path: Path) -> None:
    for _ in range(100):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"child pid file was not created: {path}")


async def _assert_pid_gone(pid: int) -> None:
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.01)
    pytest.fail(f"orphan MCP child process remains alive: {pid}")


def _child_server_config(name: str) -> MCPServerConfig:
    return _fixture_server_config(name=name).model_copy(
        update={"env": {"MCP_CHILD_PID_FILE": "${MCP_CHILD_PID_FILE}"}}
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manager_shutdown_reaps_stdio_process_tree(
    manager: MCPManager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "shutdown-child.pid"
    monkeypatch.setenv("MCP_CHILD_PID_FILE", str(pid_file))
    manager._config = MCPConfig(mcp_servers={"fx": _child_server_config("fx")})
    await manager.start()
    await _wait_for_file(pid_file)
    child_pid = int(pid_file.read_text())

    await manager.shutdown()

    await _assert_pid_gone(child_pid)
    assert manager.terminal_receipts["fx"].status == "cancelled"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_task_cancellation_reaps_stdio_process_tree(
    manager: MCPManager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "cancel-child.pid"
    monkeypatch.setenv("MCP_CHILD_PID_FILE", str(pid_file))
    manager._config = MCPConfig(mcp_servers={"fx": _child_server_config("fx")})
    await manager.start()
    session = manager.sessions["fx"]
    await _wait_for_file(pid_file)
    child_pid = int(pid_file.read_text())

    assert session._task is not None
    session._task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await session._task

    await _assert_pid_gone(child_pid)
    assert session.terminal_receipt is not None
    assert session.terminal_receipt.status == "cancelled"
    await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconnect_reaps_previous_stdio_process_tree(
    manager: MCPManager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "reconnect-child.pid"
    monkeypatch.setenv("MCP_CHILD_PID_FILE", str(pid_file))
    manager._config = MCPConfig(mcp_servers={"fx": _child_server_config("fx")})
    await manager.start()
    session = manager.sessions["fx"]
    await _wait_for_file(pid_file)
    first_child_pid = int(pid_file.read_text())

    session.request_reconnect()
    for _ in range(200):
        if len(manager._execution_gateway.requests) >= 2:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("MCP reconnect did not start a new governed process")

    await _assert_pid_gone(first_child_pid)
    assert session.terminal_receipt is not None
    await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_protocol_error_reaps_process_and_retains_receipt(
    manager: MCPManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tianshu.tools.mcp import client as client_module

    monkeypatch.setattr(client_module, "MAX_RECONNECT_ATTEMPTS", 1)
    manager._config = MCPConfig(
        mcp_servers={
            "broken": MCPServerConfig(
                name="broken",
                transport="stdio",
                command=sys.executable,
                args=["-c", "import time;print('not-json',flush=True);time.sleep(60)"],
            )
        }
    )

    await asyncio.wait_for(manager.start(), timeout=2)

    assert "broken" not in manager.sessions
    receipt = manager.terminal_receipts["broken"]
    assert receipt.purpose == "mcp_stdio"
    assert receipt.status == "cancelled"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_gateway_timeout_closes_transport_and_reconnects(
    manager: MCPManager,
) -> None:
    cfg = _fixture_server_config(name="fx").model_copy(update={"timeout": 1})
    manager._config = MCPConfig(mcp_servers={"fx": cfg})
    await manager.start()
    session = manager.sessions["fx"]

    for _ in range(300):
        if (
            session.terminal_receipt is not None
            and session.terminal_receipt.status == "timed_out"
            and len(manager._execution_gateway.requests) >= 2
        ):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("gateway timeout did not close and reconnect the MCP transport")

    await manager.shutdown()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_shutdown_cancels_starting_initialize_and_reaps_process_tree(
    manager: MCPManager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from tianshu.tools.mcp import manager as manager_module

    pid_file = tmp_path / "starting-child.pid"
    monkeypatch.setenv("MCP_CHILD_PID_FILE", str(pid_file))
    monkeypatch.setenv("MCP_INIT_DELAY", "60")
    cfg = _fixture_server_config(name="slow").model_copy(
        update={
            "env": {
                "MCP_CHILD_PID_FILE": "${MCP_CHILD_PID_FILE}",
                "MCP_INIT_DELAY": "${MCP_INIT_DELAY}",
            }
        }
    )
    manager._config = MCPConfig(mcp_servers={"slow": cfg})

    created_sessions = []
    real_session = manager_module.MCPServerSession

    def tracking_session(*args, **kwargs):
        session = real_session(*args, **kwargs)
        created_sessions.append(session)
        return session

    monkeypatch.setattr(manager_module, "MCPServerSession", tracking_session)
    start_task = asyncio.create_task(manager.start())
    await _wait_for_file(pid_file)
    child_pid = int(pid_file.read_text())

    try:
        shutdown_task = asyncio.create_task(manager.shutdown())
        for _ in range(100):
            if manager._stopping:
                break
            await asyncio.sleep(0)
        else:
            pytest.fail("manager did not enter stopping state")
        competing_start = asyncio.create_task(manager.start())
        await asyncio.sleep(0)
        assert competing_start.done()
        await asyncio.wait_for(shutdown_task, timeout=2)
        await asyncio.wait_for(asyncio.shield(start_task), timeout=2)
        await _assert_pid_gone(child_pid)
        assert manager._starting_sessions == {}
        assert manager._start_tasks == {}
        await asyncio.wait_for(manager.shutdown(), timeout=0.5)
    finally:
        if not start_task.done():
            start_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await start_task
        for session in created_sessions:
            if session._task is not None and not session._task.done():
                session._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await session._task


@pytest.mark.integration
@pytest.mark.asyncio
async def test_overlapping_shutdowns_keep_start_guard_until_all_finish(
    manager: MCPManager,
) -> None:
    manager._config = MCPConfig(mcp_servers={"fx": _fixture_server_config(name="fx")})
    await manager.start()
    session = manager.sessions["fx"]
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    call_count = 0
    real_shutdown = session.shutdown

    async def controlled_shutdown() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_entered.set()
            await release_first.wait()
            await real_shutdown()
            return
        second_entered.set()
        await release_second.wait()

    session.shutdown = controlled_shutdown  # type: ignore[method-assign]
    first = asyncio.create_task(manager.shutdown())
    await asyncio.wait_for(first_entered.wait(), timeout=1)
    second = asyncio.create_task(manager.shutdown())

    try:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(second_entered.wait(), timeout=0.05)
        if second_entered.is_set():
            release_second.set()
            await asyncio.wait_for(asyncio.shield(second), timeout=1)
        assert manager._stopping is True
    finally:
        release_first.set()
        release_second.set()
        results = await asyncio.gather(first, second, return_exceptions=True)

    assert results == [None, None]
    assert manager._stopping is False
    assert manager.sessions == {}


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
        failed_receipt = manager.terminal_receipts["broken"]
        assert failed_receipt.status == "failed"
        assert failed_receipt.mcp_server_name == "broken"
        assert failed_receipt.exit_code is None
    finally:
        await manager.shutdown()
