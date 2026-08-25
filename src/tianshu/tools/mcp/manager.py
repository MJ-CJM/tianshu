"""MCPManager — MCP server 集合的生命周期与 ToolRegistry 注册总线。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from tianshu.executor.execution_gateway import ExecutionGateway, ExecutionReceipt
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.plugins.contribution import (
    ContributionDisposeStatus,
    ContributionHandle,
    record_stale_contribution_dispose,
)
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

_MCP_RUNTIME_ACTOR_DIGEST = hashlib.sha256(b"internal:mcp-manager").hexdigest()
_MCP_RUNTIME_CORRELATION = "mcp-runtime:admission"


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    allowed: bool
    reason_code: str


class MCPManager:
    """负责 MCP server 的加载、启停和工具注册。"""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        execution_gateway: ExecutionGateway,
        *,
        workspace_root: str | Path = ".",
        security_mode: Literal["trusted-local", "secure-remote"] = "trusted-local",
        config_path: str | Path = "~/.tianshu/mcp_servers.yaml",
        storage: object | None = None,
        allowlist: str | None = None,
    ) -> None:
        self._tools = tool_registry
        self._execution_gateway = execution_gateway
        self._workspace_root = Path(workspace_root).resolve()
        self._security_mode = security_mode
        self._config_path = Path(config_path).expanduser()
        self._storage = storage
        self._configured_stdio_commands: dict[str, tuple[str, ...]] = {}
        # MCP 治理·准入清单(D15):非空 → 只启动清单内 server。空/None → 不强制。
        self._allowlist: frozenset[str] | None = (
            frozenset(n.strip() for n in allowlist.split(",") if n.strip())
            if allowlist and allowlist.strip()
            else None
        )
        self._config: MCPConfig = MCPConfig()
        self._sessions: dict[str, MCPServerSession] = {}
        self._terminal_receipts: dict[str, ExecutionReceipt] = {}
        self._starting_sessions: dict[str, MCPServerSession] = {}
        self._start_tasks: dict[str, asyncio.Task[tuple[str, MCPServerSession | None]]] = {}
        self._tool_contributions: dict[int, list[ContributionHandle]] = {}
        self._stopping = False
        self._shutdown_waiters = 0
        self._shutdown_lock = asyncio.Lock()

    def _admitted(self, name: str) -> bool:
        """准入判定:无清单则全允(启动时另有明示告警);有清单则须在册。"""
        return self._allowlist is None or name in self._allowlist

    def _admission_for(self, config: MCPServerConfig) -> AdmissionDecision:
        if not config.enabled:
            return AdmissionDecision(False, "disabled")
        if config.transport == "streamable_http" and self._security_mode == "secure-remote":
            return AdmissionDecision(False, "trusted_egress_unavailable")
        if config.transport == "stdio" and not config.tools.include:
            return AdmissionDecision(False, "approved_tools_required")
        if not self._admitted(config.name):
            return AdmissionDecision(False, "server_not_allowlisted")
        return AdmissionDecision(True, "admitted")

    def admission_for(self, config: MCPServerConfig) -> AdmissionDecision:
        """Return the immutable Lean admission result without changing runtime state."""

        return self._admission_for(config)

    def _audit_admission_denial(
        self,
        config: MCPServerConfig,
        decision: AdmissionDecision,
    ) -> None:
        if (
            decision.allowed
            or decision.reason_code == "disabled"
            or self._storage is None
            or not hasattr(self._storage, "append_system_audit")
        ):
            return
        self._storage.append_system_audit(
            AppendSystemAuditRequest(
                correlation_id=_MCP_RUNTIME_CORRELATION,
                actor_digest=_MCP_RUNTIME_ACTOR_DIGEST,
                action="mcp.admission.denied",
                outcome="denied",
                reason_code=decision.reason_code,
                subject_kind="mcp_server",
                subject_digest=hashlib.sha256(config.name.encode()).hexdigest(),
                metadata={},
            )
        )

    def _sync_stdio_commands(self, commands: dict[str, tuple[str, ...]]) -> None:
        if commands:
            self._execution_gateway.configure_mcp_stdio_commands(commands)
            self._configured_stdio_commands = dict(commands)
        elif self._configured_stdio_commands:
            self._execution_gateway.configure_mcp_stdio_commands({})
            self._configured_stdio_commands = {}

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
        rows = self._storage.list_mcp_overrides()
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

    @property
    def starting_names(self) -> tuple[str, ...]:
        """正在启动、尚未落地 ``_sessions`` 的 server 名。

        MCP 在后台任务里启动（``wiring_tools._mcp_bg_start``），冷启动（如 npx 拉包）
        可达数十秒。这段窗口内 server 既没连上也没失败——readiness 若把它算作失败，
        每次重启都会先报一段假降级。
        """
        return tuple(sorted(self._starting_sessions))

    @property
    def admitted_enabled_names(self) -> tuple[str, ...]:
        """``start()`` 会尝试启动的 server 名（enabled ∧ 准入）。

        readiness 的"应连"基线：启动失败的 session 不会进 ``_sessions``
        （``_start_one`` 返回 None），只看 ``sessions`` 就永远发现不了
        "配置了却压根没连上"。
        """
        return tuple(
            sorted(
                name
                for name, cfg in self._config.mcp_servers.items()
                if self._admission_for(cfg).allowed
            )
        )

    @property
    def terminal_receipts(self) -> dict[str, ExecutionReceipt]:
        receipts = dict(self._terminal_receipts)
        receipts.update(
            (name, session.terminal_receipt)
            for name, session in self._sessions.items()
            if session.terminal_receipt is not None
        )
        return receipts

    # -- 生命周期 -----------------------------------------------------------

    async def start(self) -> None:
        """并行启动所有 enabled server。

        - 单个 server 启动失败不影响其他（degrade 模式）。
        - 并行启动避免慢 server（如 npx 首次拉包）拖延整体响应。
        """
        if self._stopping:
            return
        if self._allowlist is None:
            logger.warning(
                "[mcp] 未设准入清单(TIANSHU_MCP_SERVER_ALLOWLIST):所有 enabled server "
                "将被加载。生产环境建议显式配置白名单(D15 治理护栏)。"
            )
        decisions = {
            name: self._admission_for(cfg) for name, cfg in self._config.mcp_servers.items()
        }
        admitted = [
            (name, cfg) for name, cfg in self._config.mcp_servers.items() if decisions[name].allowed
        ]
        stdio_commands = {
            name: (cfg.command, *cfg.args)
            for name, cfg in admitted
            if cfg.transport == "stdio" and cfg.command is not None
        }
        self._sync_stdio_commands(stdio_commands)
        for name, cfg in self._config.mcp_servers.items():
            decision = decisions[name]
            if decision.allowed:
                continue
            self._audit_admission_denial(cfg, decision)
            if decision.reason_code == "disabled":
                logger.info("[mcp] skip disabled server: %s", name)
            else:
                logger.warning(
                    "[mcp] server admission denied: reason=%s",
                    decision.reason_code,
                )

        async def _start_one(
            name: str,
            session: MCPServerSession,
        ) -> tuple[str, MCPServerSession | None]:
            try:
                connected = await session.start()
            except asyncio.CancelledError:
                await session.shutdown()
                raise
            except Exception:
                logger.exception("[mcp] server %s failed to start, continuing without it", name)
                await session.shutdown()
                if session.terminal_receipt is not None:
                    self._terminal_receipts[name] = session.terminal_receipt
                return name, None
            if not connected:
                logger.warning(
                    "[mcp] server %s not connected (last_error=%s); skipping",
                    name,
                    session.last_error,
                )
                await session.shutdown()
                if session.terminal_receipt is not None:
                    self._terminal_receipts[name] = session.terminal_receipt
                return name, None
            return name, session

        if not admitted:
            return

        for name, cfg in admitted:
            session = MCPServerSession(
                config=cfg,
                execution_gateway=self._execution_gateway,
                workspace_root=self._workspace_root,
                security_mode=self._security_mode,
                on_tools_changed=self._handle_session_tools_changed,
                on_tools_unavailable=self._dispose_session_tools,
            )
            self._starting_sessions[name] = session
            self._start_tasks[name] = asyncio.create_task(
                _start_one(name, session),
                name=f"mcp-start-{name}",
            )

        results = await asyncio.gather(
            *self._start_tasks.values(),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                continue
            name, started_session = result
            self._starting_sessions.pop(name, None)
            self._start_tasks.pop(name, None)
            if started_session is None:
                continue
            if self._stopping:
                await started_session.shutdown()
                if started_session.terminal_receipt is not None:
                    self._terminal_receipts[name] = started_session.terminal_receipt
                continue
            self._sessions[name] = started_session
            if (
                id(started_session) not in self._tool_contributions
                and getattr(started_session, "on_tools_changed", None) is None
            ):
                # Backward-compatible fallback for lightweight session fakes.
                # Real sessions publish discoveries through on_tools_changed;
                # republishing here can resurrect tools already withdrawn while
                # another server was still starting.
                self._register_session_tools(started_session)

    async def shutdown(self) -> None:
        self._shutdown_waiters += 1
        self._stopping = True
        try:
            async with self._shutdown_lock:
                self._sync_stdio_commands({})
                self._dispose_all_session_tools()
                starting_sessions = tuple(self._starting_sessions.values())
                if starting_sessions:
                    await asyncio.gather(
                        *(session.shutdown() for session in starting_sessions),
                        return_exceptions=True,
                    )
                starting_tasks = tuple(self._start_tasks.values())
                if starting_tasks:
                    await asyncio.gather(*starting_tasks, return_exceptions=True)
                for name, session in tuple(self._starting_sessions.items()):
                    if session.terminal_receipt is not None:
                        self._terminal_receipts[name] = session.terminal_receipt
                self._starting_sessions.clear()
                self._start_tasks.clear()

                for name, session in self._sessions.items():
                    try:
                        await session.shutdown()
                    except Exception:
                        logger.exception(
                            "[mcp] error shutting down session %s", session.config.name
                        )
                    if session.terminal_receipt is not None:
                        self._terminal_receipts[name] = session.terminal_receipt
                self._sessions.clear()
        finally:
            self._shutdown_waiters -= 1
            self._stopping = self._shutdown_waiters > 0

    # -- 工具注册 -----------------------------------------------------------

    def _handle_session_tools_changed(self, session: MCPServerSession) -> None:
        self._register_session_tools(session)

    def _dispose_handles(self, handles: list[ContributionHandle]) -> None:
        for handle in reversed(handles):
            status = handle.dispose()
            if status is ContributionDisposeStatus.SKIPPED_STALE:
                logger.warning(
                    "[mcp] stale tool contribution preserved: owner=%s name=%s",
                    handle.owner,
                    handle.name,
                )

    def _dispose_session_tools(self, session: MCPServerSession) -> None:
        handles = self._tool_contributions.pop(id(session), [])
        self._dispose_handles(handles)

    def _dispose_all_session_tools(self) -> None:
        contributions = tuple(self._tool_contributions.values())
        self._tool_contributions.clear()
        for handles in contributions:
            self._dispose_handles(handles)

    def _tool_handle(
        self,
        *,
        owner: str,
        name: str,
        handler: object,
    ) -> ContributionHandle:
        finished = False

        def dispose() -> ContributionDisposeStatus:
            nonlocal finished
            if finished:
                return ContributionDisposeStatus.NOOP
            finished = True
            if self._tools.unregister(name, owner=owner, target=handler):
                return ContributionDisposeStatus.DISPOSED
            if self._tools.get_definition(name) is not None:
                record_stale_contribution_dispose(
                    self._storage,
                    owner=owner,
                    kind="tool",
                    name=name,
                )
                return ContributionDisposeStatus.SKIPPED_STALE
            return ContributionDisposeStatus.NOOP

        return ContributionHandle(
            owner=owner,
            kind="tool",
            name=name,
            target=handler,
            dispose=dispose,
        )

    def _register_session_tools(self, session: MCPServerSession) -> int:
        from tianshu.tools.registry import ToolDefinition  # 局部 import 避免循环

        if self._stopping:
            return 0
        name = session.config.name
        if (
            self._starting_sessions.get(name) is not session
            and self._sessions.get(name) is not session
        ):
            return 0
        self._dispose_session_tools(session)
        count = 0
        cfg = session.config
        owner = f"mcp:{cfg.name}"
        handles: list[ContributionHandle] = []
        decision = self._admission_for(cfg)
        if not decision.allowed:
            self._audit_admission_denial(cfg, decision)
            return 0
        try:
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
                self._tools.register(full_name, handler, defn, owner=owner)
                handles.append(
                    self._tool_handle(
                        owner=owner,
                        name=full_name,
                        handler=handler,
                    )
                )
                count += 1
        except Exception:
            self._dispose_handles(handles)
            raise
        if handles:
            self._tool_contributions[id(session)] = handles
        logger.info("[mcp] registered %d tool(s) from server %s", count, cfg.name)
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
