"""ToolRegistry / register_builtins / MCP / tool_switches / engine_prefs 装配。

对应原 lifespan() 的 `# --- Tools ---` + `# --- MCP（藏兵阁外挂）---` 两个分区，
以及紧随其后两段没有独立分区注释、但同属"工具装配收尾"的桥接代码
（禁用列表回放、默认引擎覆盖）——四段代码原本相邻，合并为一个 wiring 函数。

跨区变量处置：`tools`（ToolRegistry 实例）在原代码里要到 `# --- Agent ---`
分区才写入 `app.state.tool_registry`，但 persona / skill 相关 wiring 在那
之前就需要用它注册工具。它是有状态对象、不可重新构造（会丢失已注册的内置
工具），按 task-12 设计约定 #2 由本函数返回，lifespan 显式向后传递。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI

from tianshu.config import TianshuSettings
from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.tools.builtins import register_builtins
from tianshu.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from tianshu.tools.mcp import MCPManager

logger = logging.getLogger(__name__)


def _runtime_secret_resolver(settings: TianshuSettings):
    setting_refs = {
        "settings:eval_llm_api_key": settings.eval_llm_api_key,
        "settings:llm_api_key": settings.llm_api_key,
    }

    def resolve(ref: str) -> str | None:
        if ref in setting_refs:
            return setting_refs[ref] or None
        if ref.startswith("settings:"):
            return None
        return os.environ.get(ref)

    return resolve


async def _mcp_bg_start(mcp_manager: MCPManager) -> None:
    """MCP session 后台启动。原 lifespan 内嵌闭包提为顶层，捕获对象改为显式参数。"""
    try:
        await mcp_manager.start()
    except Exception:
        logger.exception("[mcp] background start failed")


async def wire_tools(app: FastAPI, settings: TianshuSettings) -> ToolRegistry:
    """创建 ToolRegistry，注册内置工具与 MCP 外挂，回放禁用列表与引擎覆盖。

    返回 tools：在它被写入 app.state（Agent 分区）之前，后续几个 wiring
    函数仍需要这个具体实例（不可重新构造）。
    """
    storage = app.state.storage
    event_bus = app.state.event_bus

    # --- Tools ---
    execution_gateway = ExecutionGateway(secret_resolver=_runtime_secret_resolver(settings))
    app.state.execution_gateway = execution_gateway
    tools = ToolRegistry()
    register_builtins(
        tools,
        settings.workspace_dir,
        storage=storage,
        event_bus=event_bus,
        execution_gateway=execution_gateway,
        edict_application_service=app.state.edict_application_service,
    )

    # --- MCP（藏兵阁外挂）：fire-and-forget 启动，不阻塞 lifespan ---
    # config 同步读（毫秒级），session 启动放后台（broken/慢 server 的退避不会卡 web）。
    # 惰性导入：mcp 属可选能力，保持 import 时不加载（对应原 lifespan 的函数内导入）。
    from tianshu.tools.mcp import MCPManager

    mcp_manager = MCPManager(
        tools,
        execution_gateway,
        workspace_root=Path(settings.workspace_dir),
        security_mode=settings.security_mode,
        storage=storage,
        allowlist=settings.mcp_server_allowlist,
    )
    app.state.mcp_manager = mcp_manager
    try:
        mcp_manager.load_config()
    except Exception:
        logger.exception("[mcp] load_config failed; continuing without MCP tools")

    app.state._mcp_start_task = asyncio.create_task(_mcp_bg_start(mcp_manager), name="mcp-bg-start")

    # 应用 DB 里持久化的禁用列表（live toggle 在运行时通过 PATCH /api/tools/{name} 更新）
    try:
        disabled = storage.list_disabled_tools()
        tools.apply_disabled(disabled)
        if disabled:
            logger.info("[tools] disabled at startup: %s", sorted(disabled))
    except Exception:
        logger.exception("[tools] failed to load tool_switches; treating all as enabled")

    # 系统默认引擎覆盖（可以在鸿胪寺页动态改，启动时从 DB 加载一次）
    try:
        from tianshu.tools.policy_profile import set_system_engine_overrides

        prefs = storage.get_engine_preferences()
        set_system_engine_overrides(
            fetch_chain=prefs["fetch_chain"],
            search_provider=prefs["search_provider"] or "",
            fallback_mode=prefs["fallback_mode"] or "",
        )
        if any([prefs["fetch_chain"], prefs["search_provider"], prefs["fallback_mode"]]):
            logger.info("[hongluisi] system engine overrides loaded: %s", prefs)
    except Exception:
        logger.exception("[hongluisi] failed to load engine_preferences; using profile defaults")

    return tools
