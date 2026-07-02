"""streamable-HTTP transport 单测（用 mock 验证调用路径与参数）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.tools.mcp.config import MCPServerConfig


@pytest.mark.unit
@pytest.mark.asyncio
async def test_open_streamable_http_invokes_sdk_with_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    @asynccontextmanager
    async def fake_streamablehttp_client(
        url, headers=None, timeout=None, sse_read_timeout=None, **_kwargs
    ) -> AsyncIterator[tuple]:
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        captured["sse_read_timeout"] = sse_read_timeout
        # 三元组：read_stream, write_stream, get_session_id
        yield (object(), object(), lambda: None)

    fake_session = MagicMock()
    fake_session.initialize = AsyncMock(return_value=None)

    @asynccontextmanager
    async def fake_client_session(read, write) -> AsyncIterator[MagicMock]:
        yield fake_session

    monkeypatch.setattr(
        "mcp.client.streamable_http.streamablehttp_client",
        fake_streamablehttp_client,
    )
    monkeypatch.setattr("mcp.ClientSession", fake_client_session)

    cfg = MCPServerConfig(
        name="gh",
        transport="streamable_http",
        url="https://api.example.com/mcp",
        headers={"Authorization": "Bearer test-token"},
        timeout=180,
        connect_timeout=15,
    )

    from tianshu.tools.mcp.transport import open_session

    async with open_session(cfg) as session:
        assert session is fake_session

    assert captured["url"] == "https://api.example.com/mcp"
    assert captured["headers"] == {"Authorization": "Bearer test-token"}
    assert captured["timeout"] == 15.0  # connect_timeout
    assert captured["sse_read_timeout"] == 180.0  # timeout
    fake_session.initialize.assert_awaited_once()
