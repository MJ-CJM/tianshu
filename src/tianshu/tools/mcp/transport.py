"""MCP transport 工厂。

MVP 阶段（P1 / P2）只实现 stdio；streamable-HTTP 在 P3 接入。
对外暴露统一的 :func:`open_session` async context manager，
内部根据 ``MCPServerConfig.transport`` 分发。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from tianshu.tools.mcp.config import MCPServerConfig

if TYPE_CHECKING:
    from mcp import ClientSession

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_session(config: MCPServerConfig) -> AsyncIterator[ClientSession]:
    """对一个 server 配置打开一个 MCP ``ClientSession``。

    使用方式::

        async with open_session(cfg) as session:
            tools = await session.list_tools()
    """
    cfg = config.with_env_interpolated()

    if cfg.transport == "stdio":
        async with _open_stdio(cfg) as session:
            yield session
    elif cfg.transport == "streamable_http":
        async with _open_streamable_http(cfg) as session:
            yield session
    else:  # pragma: no cover — Pydantic 已限制 Literal
        raise ValueError(f"unknown transport: {cfg.transport!r}")


@asynccontextmanager
async def _open_stdio(cfg: MCPServerConfig) -> AsyncIterator[ClientSession]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    assert cfg.command is not None  # validator 已保证
    params = StdioServerParameters(
        command=cfg.command,
        args=list(cfg.args),
        env=dict(cfg.env) if cfg.env else None,
    )
    async with (
        stdio_client(params) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


@asynccontextmanager
async def _open_streamable_http(
    cfg: MCPServerConfig,
) -> AsyncIterator[ClientSession]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    assert cfg.url is not None  # validator 已保证
    headers = dict(cfg.headers) if cfg.headers else None
    async with (
        streamablehttp_client(
            cfg.url,
            headers=headers,
            timeout=float(cfg.connect_timeout),
            sse_read_timeout=float(cfg.timeout),
        ) as (read_stream, write_stream, _get_session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session
