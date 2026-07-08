"""MCPManager — MCP server 集合的生命周期与 ToolRegistry 注册总线。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tianshu.tools.mcp.client import MCPServerSession
from tianshu.tools.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    MCPServerOverride,
    load_config_from_yaml,
    merge_overrides,
)
from tianshu.tools.mcp.naming import encode_tool_name
from tianshu.tools.types import ToolResult, error_result, ok_result

if TYPE_CHECKING:
    from tianshu.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class MCPManager:
    """负责 MCP server 的加载、启停和工具注册。"""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        config_path: str | Path = "~/.tianshu/mcp_servers.yaml",
        storage: object | None = None,
        allowlist: str | None = None,
    ) -> None:
        self._tools = tool_registry
        self._config_path = Path(config_path).expanduser()
        self._storage = storage
        # MCP 治理·准入清单(D15):非空 → 只启动清单内 server。空/None → 不强制。
        self._allowlist: frozenset[str] | None = (
            frozenset(n.strip() for n in allowlist.split(",") if n.strip())
            if allowlist and allowlist.strip()
            else None
        )
        self._config: MCPConfig = MCPConfig()
        self._sessions: dict[str, MCPServerSession] = {}

    def _admitted(self, name: str) -> bool:
        """准入判定:无清单则全允(启动时另有明示告警);有清单则须在册。"""
        return self._allowlist is None or name in self._allowlist

    # -- 配置 ---------------------------------------------------------------

    def load_config(
        self,
        overrides: list[MCPServerOverride] | None = None,
    ) -> MCPConfig:
        base = load_config_from_yaml(self._config_path)
        if overrides is None:
            overrides = self._load_overrides_from_storage()
        merged = merge_overrides(base, overrides)
        self._config = merged
        logger.info(
            "[mcp] config loaded: %d server(s) (%d enabled)",
            len(merged.mcp_servers),
            sum(1 for s in merged.mcp_servers.values() if s.enabled),
        )
        return merged

    def _load_overrides_from_storage(self) -> list[MCPServerOverride]:
        if self._storage is None or not hasattr(self._storage, "list_mcp_overrides"):
            return []
        try:
            rows = self._storage.list_mcp_overrides()
        except Exception:
            logger.exception("[mcp] failed to load overrides from storage")
            return []
        return [
            MCPServerOverride(
                name=r["name"],
                enabled=r["enabled"],
                env=r["env"],
                tools_include=r["tools_include"],
                tools_exclude=r["tools_exclude"],
                transport=r.get("transport"),
                command=r.get("command"),
                args=r.get("args"),
                url=r.get("url"),
                headers=r.get("headers"),
                default_tier=r.get("default_tier"),
                timeout=r.get("timeout"),
                connect_timeout=r.get("connect_timeout"),
                tool_overrides=r.get("tool_overrides"),
            )
            for r in rows
        ]

    @property
    def config(self) -> MCPConfig:
        return self._config

    @property
    def sessions(self) -> dict[str, MCPServerSession]:
        return dict(self._sessions)

    # -- 生命周期 -----------------------------------------------------------

    async def start(self) -> None:
        """并行启动所有 enabled server。

        - 单个 server 启动失败不影响其他（degrade 模式）。
        - 并行启动避免慢 server（如 npx 首次拉包）拖延整体响应。
        """
        import asyncio

        if self._allowlist is None:
            logger.warning(
                "[mcp] 未设准入清单(TIANSHU_MCP_SERVER_ALLOWLIST):所有 enabled server "
                "将被加载。生产环境建议显式配置白名单(D15 治理护栏)。"
            )
        enabled = [
            (name, cfg)
            for name, cfg in self._config.mcp_servers.items()
            if cfg.enabled and self._admitted(name)
        ]
        for name, cfg in self._config.mcp_servers.items():
            if not cfg.enabled:
                logger.info("[mcp] skip disabled server: %s", name)
            elif not self._admitted(name):
                logger.warning("[mcp] server %s 未在准入清单内,拒绝加载(D15)", name)

        async def _start_one(name: str, cfg) -> tuple[str, MCPServerSession | None]:
            session = MCPServerSession(config=cfg)
            try:
                connected = await session.start()
            except Exception:
                logger.exception("[mcp] server %s failed to start, continuing without it", name)
                await session.shutdown()
                return name, None
            if not connected:
                logger.warning(
                    "[mcp] server %s not connected (last_error=%s); skipping",
                    name,
                    session.last_error,
                )
                await session.shutdown()
                return name, None
            return name, session

        if not enabled:
            return

        results = await asyncio.gather(
            *[_start_one(name, cfg) for name, cfg in enabled],
            return_exceptions=False,
        )
        for name, session in results:
            if session is None:
                continue
            self._sessions[name] = session
            count = self._register_session_tools(session)
            logger.info("[mcp] registered %d tool(s) from server %s", count, name)

    async def shutdown(self) -> None:
        for session in self._sessions.values():
            try:
                await session.shutdown()
            except Exception:
                logger.exception("[mcp] error shutting down session %s", session.config.name)
        self._sessions.clear()

    # -- 工具注册 -----------------------------------------------------------

    def _register_session_tools(self, session: MCPServerSession) -> int:
        from tianshu.tools.registry import ToolDefinition  # 局部 import 避免循环

        count = 0
        cfg = session.config
        for tool in session.tools:
            if not _passes_filter(tool.name, cfg):
                continue
            full_name = encode_tool_name(cfg.name, tool.name)
            tier = cfg.tool_overrides.get(tool.name, cfg.default_tier)
            description = tool.description or "(no description)"
            description = f"[via MCP/{cfg.name}] {description}"
            defn = ToolDefinition(
                name=full_name,
                description=description,
                parameters=tool.input_schema or {"type": "object", "properties": {}},
                tier=tier,
                # MCP 工具大多有副作用；保守置 True 让 winding_down 阶段拦截。
                # 例外：default_tier == 0 视为只读。
                side_effect=tier > 0,
            )
            handler = _make_handler(session, tool.name)
            self._tools.register(full_name, handler, defn)
            count += 1
        return count


def _passes_filter(tool_name: str, cfg: MCPServerConfig) -> bool:
    """应用 ``cfg.tools.include`` / ``cfg.tools.exclude``。"""
    if cfg.tools.exclude and tool_name in cfg.tools.exclude:
        return False
    if cfg.tools.include:
        return tool_name in cfg.tools.include
    return True


def _make_handler(
    session: MCPServerSession, tool_name: str
) -> Callable[..., Awaitable[ToolResult]]:
    """为单个 MCP 工具产出 ToolRegistry 兼容的 async handler。"""

    async def handler(**kwargs: Any) -> ToolResult:
        try:
            result = await session.call_tool(tool_name, kwargs)
        except Exception as exc:
            logger.exception("[mcp] tool call failed: %s.%s", session.config.name, tool_name)
            return error_result(f"MCP {session.config.name}.{tool_name} failed: {exc}")

        text_parts: list[str] = []
        for piece in result.content or []:
            text = getattr(piece, "text", None)
            if text:
                text_parts.append(text)
        content = "\n".join(text_parts)
        details: dict[str, Any] | None = None
        structured = getattr(result, "structuredContent", None)
        if structured:
            details = {"structured": structured}
        if result.isError:
            return ToolResult(
                content=content or "MCP tool returned error", details=details, is_error=True
            )
        return ok_result(content=content, details=details)

    return handler
