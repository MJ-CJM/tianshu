"""单个 MCP server 的会话封装（P3）。

设计：
- 每个 server 对应一个 :class:`MCPServerSession`，内部跑一个长驻 task ``_run``。
- ``_run`` 是一个 reconnect 循环：进入 ``open_session()`` → 发现工具 → publish
  ``ClientSession`` 引用 → 阻塞在 ``_shutdown_event`` / ``_reconnect_event`` 任一。
- 连接失败走指数退避（每次 ``2^attempt`` 秒，封顶 30s），最多
  ``MAX_RECONNECT_ATTEMPTS`` 次后放弃，``status = "error"``。
- 所有写到 ``last_error`` / 日志的异常文本都过 :func:`redact` 脱敏。
- ``start()`` 阻塞至首次 ``ready_event`` 触发；只有 ``status == "connected"`` 才
  视为成功。彻底失败时 ``_run`` 也会 set ``ready_event`` 让 ``start()`` 解阻塞。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tianshu.tools.mcp.config import MCPServerConfig
from tianshu.tools.mcp.redact import redact
from tianshu.tools.mcp.transport import open_session

if TYPE_CHECKING:
    from mcp import ClientSession

    from tianshu.executor.execution_gateway import ExecutionGateway, ExecutionReceipt

logger = logging.getLogger(__name__)


SessionStatus = Literal["pending", "connected", "reconnecting", "error", "stopped"]

MAX_RECONNECT_ATTEMPTS = 5
"""单次连接失败后的最大重试次数，参照 hermes ``_MAX_RECONNECT_RETRIES``。"""

MAX_BACKOFF_SECONDS = 30
"""退避封顶（秒）。"""


@dataclass
class DiscoveredTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class MCPServerSession:
    config: MCPServerConfig
    execution_gateway: ExecutionGateway
    workspace_root: Path
    security_mode: Literal["trusted-local", "secure-remote"]
    status: SessionStatus = "pending"
    tools: list[DiscoveredTool] = field(default_factory=list)
    last_error: str | None = None
    terminal_receipt: ExecutionReceipt | None = None

    _session: ClientSession | None = None
    _ready_event: asyncio.Event = field(default_factory=asyncio.Event)
    _shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    _reconnect_event: asyncio.Event = field(default_factory=asyncio.Event)
    _transport_closed_event: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = None

    async def start(self) -> bool:
        """启动会话；阻塞至首次连接成功或彻底失败。

        返回 True 表示成功（``status == "connected"``，工具已发现）；
        False 表示彻底失败（``status == "error"``）。
        """
        if self._task is not None:
            raise RuntimeError(f"MCPServerSession({self.config.name}) already started")
        self._task = asyncio.create_task(self._run(), name=f"mcp-session-{self.config.name}")
        await self._ready_event.wait()
        return self.status == "connected"

    async def _run(self) -> None:
        attempt = 0
        try:
            while not self._shutdown_event.is_set():
                try:
                    self._transport_closed_event.clear()
                    async with open_session(
                        self.config,
                        execution_gateway=self.execution_gateway,
                        workspace_root=self.workspace_root,
                        security_mode=self.security_mode,
                        receipt_callback=self._record_receipt,
                        closed_event=self._transport_closed_event,
                    ) as session:
                        self._session = session
                        await self._discover_tools(session)
                        self.status = "connected"
                        attempt = 0
                        self._ready_event.set()
                        logger.info(
                            "[mcp] session ready: %s (%d tool(s))",
                            self.config.name,
                            len(self.tools),
                        )
                        await self._wait_lifecycle()
                        if self._shutdown_event.is_set():
                            return
                        # 否则是 _reconnect_event 触发：清状态，外层循环重连
                        self._reconnect_event.clear()
                        self.status = "reconnecting"
                        logger.info("[mcp] reconnect requested for %s", self.config.name)
                        continue
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    attempt += 1
                    msg = redact(str(exc))
                    self.last_error = msg
                    logger.warning(
                        "[mcp] %s connect failed (attempt %d/%d): %s",
                        self.config.name,
                        attempt,
                        MAX_RECONNECT_ATTEMPTS,
                        msg,
                    )
                    if attempt >= MAX_RECONNECT_ATTEMPTS:
                        self.status = "error"
                        self._ready_event.set()
                        return
                    self.status = "reconnecting"
                    backoff = min(2**attempt, MAX_BACKOFF_SECONDS)
                    if await self._sleep_or_shutdown(backoff):
                        return
                    continue
                finally:
                    self._session = None
        finally:
            if self.status not in ("error",):
                self.status = "stopped"
            # 哪怕从未 ready，也要解阻塞 start()
            self._ready_event.set()

    async def _wait_lifecycle(self) -> None:
        """阻塞直到 ``_shutdown_event`` 或 ``_reconnect_event`` 触发。

        参照 hermes ``MCPServerTask._wait_for_lifecycle_event``。
        """
        shutdown_waiter = asyncio.create_task(self._shutdown_event.wait())
        reconnect_waiter = asyncio.create_task(self._reconnect_event.wait())
        transport_waiter = asyncio.create_task(self._transport_closed_event.wait())
        try:
            await asyncio.wait(
                {shutdown_waiter, reconnect_waiter, transport_waiter},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for w in (shutdown_waiter, reconnect_waiter, transport_waiter):
                if not w.done():
                    w.cancel()

    async def _sleep_or_shutdown(self, seconds: float) -> bool:
        """退避 sleep，期间若收到 shutdown 立即返回 True。"""
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    async def _discover_tools(self, session: ClientSession) -> None:
        resp = await session.list_tools()
        self.tools = []
        for t in resp.tools:
            schema = t.inputSchema or {"type": "object", "properties": {}}
            self.tools.append(
                DiscoveredTool(
                    name=t.name,
                    description=t.description or "",
                    input_schema=schema,
                )
            )

    def _record_receipt(self, receipt: ExecutionReceipt) -> None:
        self.terminal_receipt = receipt

    def request_reconnect(self) -> None:
        """触发一次重连（非阻塞）。"""
        self._reconnect_event.set()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if self._session is None:
            raise RuntimeError(f"MCP session {self.config.name!r} not connected")
        try:
            return await self._session.call_tool(tool_name, arguments)
        except Exception as exc:
            # 让上层拿到的错误信息已经脱敏
            raise RuntimeError(redact(str(exc))) from None

    async def shutdown(self) -> None:
        self._shutdown_event.set()
        if self._task is None:
            return
        if self.status in {"pending", "reconnecting"}:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            return
        try:
            await asyncio.wait_for(self._task, timeout=10)
        except (TimeoutError, asyncio.CancelledError):
            logger.warning(
                "[mcp] session shutdown timeout, cancelling: %s",
                self.config.name,
            )
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        except Exception:
            pass
