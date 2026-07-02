"""/api/mcp 路由（藏兵阁外援）：server 增删改查、工具列表、配置热重载。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from tianshu.models import ApiResponse
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

mcp_router = APIRouter(prefix="/mcp", tags=["mcp"])


# --- MCP servers endpoints (藏兵阁外援) ---


def _mcp_server_to_dict(name: str, cfg, session=None) -> dict:
    """把 MCPServerConfig + 可选的运行时 session 序列化成 API 响应。"""
    out: dict = {
        "name": name,
        "transport": cfg.transport,
        "enabled": cfg.enabled,
        "default_tier": cfg.default_tier,
        "timeout": cfg.timeout,
        "connect_timeout": cfg.connect_timeout,
        "tools_filter": {
            "include": list(cfg.tools.include),
            "exclude": list(cfg.tools.exclude),
        },
        "tool_overrides": dict(cfg.tool_overrides),
    }
    if cfg.transport == "stdio":
        out["command"] = cfg.command
        out["args"] = list(cfg.args)
        out["env_keys"] = sorted(cfg.env.keys())  # 不回传 value 防泄露
    else:
        out["url"] = cfg.url
        out["header_keys"] = sorted(cfg.headers.keys())  # 同上

    if session is not None:
        out["status"] = session.status
        out["last_error"] = session.last_error
        out["tools"] = [{"name": t.name, "description": t.description} for t in session.tools]
    else:
        out["status"] = "disabled" if not cfg.enabled else "unknown"
        out["last_error"] = None
        out["tools"] = []
    return out


@mcp_router.get("/servers")
async def list_mcp_servers(request: Request):
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:
        return ApiResponse(success=True, data=[])
    try:
        sessions = manager.sessions
        data = []
        for name, cfg in manager.config.mcp_servers.items():
            try:
                data.append(_mcp_server_to_dict(name, cfg, sessions.get(name)))
            except Exception as exc:
                logger.exception("[mcp] failed to serialize server %s", name)
                data.append(
                    {
                        "name": name,
                        "status": "error",
                        "last_error": f"serialization error: {exc}",
                    }
                )
        return ApiResponse(success=True, data=data)
    except Exception as exc:
        logger.exception("[mcp] list servers failed")
        return ApiResponse(success=False, error=str(exc), data=[])


@mcp_router.get("/servers/{name}")
async def get_mcp_server(name: str, request: Request):
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None or name not in manager.config.mcp_servers:
        raise HTTPException(404, f"mcp server {name!r} not found")
    cfg = manager.config.mcp_servers[name]
    session = manager.sessions.get(name)
    return ApiResponse(success=True, data=_mcp_server_to_dict(name, cfg, session))


@mcp_router.get("/servers/{name}/tools")
async def list_mcp_server_tools(name: str, request: Request):
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None or name not in manager.config.mcp_servers:
        raise HTTPException(404, f"mcp server {name!r} not found")
    session = manager.sessions.get(name)
    if session is None:
        return ApiResponse(success=True, data=[])
    from tianshu.tools.mcp.naming import encode_tool_name

    cfg = manager.config.mcp_servers[name]
    data = []
    for t in session.tools:
        full_name = encode_tool_name(name, t.name)
        tier = cfg.tool_overrides.get(t.name, cfg.default_tier)
        data.append(
            {
                "name": t.name,
                "full_name": full_name,
                "description": t.description,
                "tier": tier,
            }
        )
    return ApiResponse(success=True, data=data)


class _MCPOverridePatch(BaseModel):
    enabled: bool | None = None
    env: dict[str, str] | None = None
    tools_include: list[str] | None = None
    tools_exclude: list[str] | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    default_tier: int | None = None
    timeout: int | None = None
    connect_timeout: int | None = None
    tool_overrides: dict[str, int] | None = None


@mcp_router.patch("/servers/{name}")
async def patch_mcp_server(
    name: str,
    body: _MCPOverridePatch,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """写入 DB override，同步刷新 manager.config，后台重启 sessions。"""
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None or name not in manager.config.mcp_servers:
        raise HTTPException(404, f"mcp server {name!r} not found")
    storage: Storage = request.app.state.storage
    storage.upsert_mcp_override(
        name,
        enabled=body.enabled,
        env=body.env,
        tools_include=body.tools_include,
        tools_exclude=body.tools_exclude,
        transport=body.transport,
        command=body.command,
        args=body.args,
        url=body.url,
        headers=body.headers,
        default_tier=body.default_tier,
        timeout=body.timeout,
        connect_timeout=body.connect_timeout,
        tool_overrides=body.tool_overrides,
    )
    # 同步重载 config（毫秒），让 GET /mcp/servers 立即看到新状态
    try:
        manager.load_config()
    except Exception:
        logger.exception("[mcp] load_config after patch failed")
    # 后台重启 sessions（npx 拉包等慢操作不阻塞 HTTP）
    registry = request.app.state.tool_registry
    background_tasks.add_task(_restart_mcp_sessions, manager, registry)
    return ApiResponse(
        success=True,
        data={"name": name, "status": "restart_scheduled"},
    )


class _MCPServerCreate(BaseModel):
    name: str
    transport: str  # "stdio" | "streamable_http"
    enabled: bool = True
    default_tier: int = 2
    timeout: int = 120
    connect_timeout: int = 30
    # stdio
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    # streamable_http
    url: str | None = None
    headers: dict[str, str] = {}
    # filter / per-tool tier
    tools_include: list[str] = []
    tools_exclude: list[str] = []
    tool_overrides: dict[str, int] = {}


@mcp_router.post("/servers", status_code=201)
async def create_mcp_server(
    body: _MCPServerCreate, request: Request, background_tasks: BackgroundTasks
):
    """从 web 后台直接新增一个 MCP server（DB-only，不写 YAML）。

    立即触发 reload，把新 server 接入 ToolRegistry。
    """
    # 1) 校验 name
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    if "_" in name:
        raise HTTPException(400, "name must not contain underscore (mcp_<server>_<tool> uses it)")
    if body.transport not in ("stdio", "streamable_http"):
        raise HTTPException(
            400, f"transport must be 'stdio' or 'streamable_http', got {body.transport!r}"
        )
    if body.transport == "stdio" and not body.command:
        raise HTTPException(400, "stdio transport requires 'command'")
    if body.transport == "streamable_http" and not body.url:
        raise HTTPException(400, "streamable_http transport requires 'url'")
    if body.default_tier not in (0, 1, 2, 3, 4):
        raise HTTPException(400, "default_tier must be 0..4")

    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(503, "mcp manager not initialized")

    # 2) 不允许覆盖 YAML 中已有同名 server（避免 UI 误操作；改用 PATCH）
    if name in manager.config.mcp_servers:
        raise HTTPException(
            409,
            f"server {name!r} already exists; use PATCH to modify or DELETE override first",
        )

    storage: Storage = request.app.state.storage
    storage.upsert_mcp_override(
        name,
        enabled=body.enabled,
        env=body.env or None,
        tools_include=body.tools_include or None,
        tools_exclude=body.tools_exclude or None,
        transport=body.transport,
        command=body.command,
        args=body.args or None,
        url=body.url,
        headers=body.headers or None,
        default_tier=body.default_tier,
        timeout=body.timeout,
        connect_timeout=body.connect_timeout,
        tool_overrides=body.tool_overrides or None,
    )

    # 3) 同步 reload config（毫秒级）让 GET /mcp/servers 立即可见新 server，
    #    session restart 走后台 task 避开 npx 拉包等慢操作（前端有 axios 30s 超时）。
    try:
        manager.load_config()
    except Exception:
        logger.exception("[mcp] load_config failed")
    registry = request.app.state.tool_registry
    background_tasks.add_task(_restart_mcp_sessions, manager, registry)

    return ApiResponse(
        success=True,
        data={
            "name": name,
            "status": "starting",
            "note": "poll GET /mcp/servers for live status",
        },
    )


@mcp_router.delete("/servers/{name}/override")
async def delete_mcp_server_override(name: str, request: Request):
    """清除 DB override（YAML 种子保留）。"""
    storage: Storage = request.app.state.storage
    storage.delete_mcp_override(name)
    return ApiResponse(
        success=True,
        data={"name": name, "note": "override deleted; call POST /mcp/reload to apply"},
    )


async def _restart_mcp_sessions(manager, registry) -> None:
    """后台执行：摘工具 → shutdown 旧 session → start 新 session。

    与 ``manager.load_config()`` 解耦：config 在 HTTP 端点里同步加载（毫秒级），
    session restart 单独走后台（npx 拉包 / 远端握手可能 30s+，不能让 HTTP 等）。
    """
    from tianshu.tools.mcp.naming import is_mcp_tool

    if hasattr(registry, "_tools"):
        for name in list(registry._tools.keys()):
            if is_mcp_tool(name):
                del registry._tools[name]
    try:
        await manager.shutdown()
        await manager.start()
    except Exception:
        logger.exception("[mcp] restart sessions failed")


@mcp_router.post("/reload")
async def reload_mcp(request: Request, background_tasks: BackgroundTasks):
    """异步重新加载 YAML+DB 配置并重启所有 session。

    config 同步重载（立即可在 GET /mcp/servers 看到新 server），session restart
    在后台进行；前端通过轮询拿真实连接状态。
    """
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(503, "mcp manager not initialized")
    try:
        manager.load_config()
    except Exception:
        logger.exception("[mcp] load_config failed")
    registry = request.app.state.tool_registry
    background_tasks.add_task(_restart_mcp_sessions, manager, registry)
    return ApiResponse(
        success=True,
        data={
            "servers": len(manager.config.mcp_servers),
            "status": "restart_scheduled",
            "note": "poll GET /mcp/servers for live session status",
        },
    )
