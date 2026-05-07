"""MCPServerSession 重连退避行为单测。

用 monkeypatch 替换 ``open_session`` 以注入连接错误，断言重试次数与最终状态。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest

from tianshu.tools.mcp import client as client_module
from tianshu.tools.mcp.client import MCPServerSession
from tianshu.tools.mcp.config import MCPServerConfig


def _make_cfg() -> MCPServerConfig:
    return MCPServerConfig(
        name="t",
        transport="stdio",
        command="echo",
        args=["dummy"],
        connect_timeout=10,
        timeout=10,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconnect_gives_up_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "MAX_RECONNECT_ATTEMPTS", 3)
    monkeypatch.setattr(client_module, "MAX_BACKOFF_SECONDS", 0)

    attempts = {"count": 0}

    @asynccontextmanager
    async def fake_open_session(_cfg) -> AsyncIterator[object]:
        attempts["count"] += 1
        raise ConnectionError("simulated failure")
        yield  # type: ignore[unreachable]

    monkeypatch.setattr(client_module, "open_session", fake_open_session)

    session = MCPServerSession(config=_make_cfg())
    connected = await session.start()
    try:
        assert connected is False
        assert session.status == "error"
        assert attempts["count"] == 3
        assert "simulated failure" in (session.last_error or "")
    finally:
        await session.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconnect_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "MAX_RECONNECT_ATTEMPTS", 5)
    monkeypatch.setattr(client_module, "MAX_BACKOFF_SECONDS", 0)

    attempts = {"count": 0}

    class FakeListResp:
        tools: list = []

    class FakeSession:
        async def list_tools(self) -> FakeListResp:
            return FakeListResp()

        async def call_tool(self, *args, **kwargs):  # pragma: no cover
            raise NotImplementedError

    @asynccontextmanager
    async def fake_open_session(_cfg) -> AsyncIterator[FakeSession]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("first attempt timeout")
        yield FakeSession()

    monkeypatch.setattr(client_module, "open_session", fake_open_session)

    session = MCPServerSession(config=_make_cfg())
    connected = await session.start()
    try:
        assert connected is True
        assert session.status == "connected"
        assert attempts["count"] == 2
    finally:
        await session.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reconnect_redacts_credentials_in_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client_module, "MAX_RECONNECT_ATTEMPTS", 1)
    monkeypatch.setattr(client_module, "MAX_BACKOFF_SECONDS", 0)

    secret = "ghp_" + "a" * 40

    @asynccontextmanager
    async def fake_open_session(_cfg) -> AsyncIterator[object]:
        raise PermissionError(f"401 Unauthorized: token={secret}")
        yield  # type: ignore[unreachable]

    monkeypatch.setattr(client_module, "open_session", fake_open_session)

    session = MCPServerSession(config=_make_cfg())
    connected = await session.start()
    try:
        assert connected is False
        assert session.last_error is not None
        assert secret not in session.last_error
        assert "[REDACTED]" in session.last_error
    finally:
        await session.shutdown()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_shutdown_aborts_reconnect_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """shutdown 期间应抢断退避 sleep，迅速退出。"""
    monkeypatch.setattr(client_module, "MAX_RECONNECT_ATTEMPTS", 5)
    monkeypatch.setattr(client_module, "MAX_BACKOFF_SECONDS", 60)  # 故意大

    attempts = {"count": 0}

    @asynccontextmanager
    async def fake_open_session(_cfg) -> AsyncIterator[object]:
        attempts["count"] += 1
        raise ConnectionError("nope")
        yield  # type: ignore[unreachable]

    monkeypatch.setattr(client_module, "open_session", fake_open_session)

    session = MCPServerSession(config=_make_cfg())
    # 不 await start (会阻塞 5 次失败 + 退避)；改成手动启动 task
    import asyncio

    session._task = asyncio.create_task(session._run())
    # 让它至少跑一次失败，进入退避
    await asyncio.sleep(0.1)
    # shutdown 应抢断
    await session.shutdown()
    assert attempts["count"] >= 1
    assert session.status in ("stopped", "error")
