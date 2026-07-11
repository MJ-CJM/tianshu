"""天枢 MCP server——把核心操作暴露给外部 MCP 宿主(Claude Code / Codex 等)。

治理边界(诚实设计,见 docs/strategy/DECISIONS.md D15):
- 只暴露「提交 + 只读」5 个 tools;批红(Decree)等治理写操作**不经 MCP 暴露**——
  MCP 宿主无法区分"用户本人指令"与"agent 自主行为",批红必须走 Web/飞书人工面。
- stateless HTTP(每请求独立、json_response),与 MCP spec 的无状态化演进方向一致。

挂载:app.py 中 `app.mount("/mcp", build_mcp_server(app).streamable_http_app())`,
session manager 在宿主 lifespan 内启动(见 app.py)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

if TYPE_CHECKING:
    from fastapi import FastAPI

_MAX_TEXT = 8000  # 单字段回传上限,防单次工具输出打爆宿主上下文


def _clip(text: str | None) -> str:
    if not text:
        return ""
    return text if len(text) <= _MAX_TEXT else text[:_MAX_TEXT] + f"…(截断,共 {len(text)} 字)"


def _memorial_brief(m: Any) -> dict:
    return {
        "memorial_id": m.id,
        "status": m.status.value,
        "review_status": m.review_status,
        "attempt": m.attempt,
        "summary": _clip(m.summary),
        "created_at": m.created_at.isoformat(),
    }


def build_mcp_server(app: FastAPI) -> FastMCP:
    """构造天枢 MCP server;tools 经闭包在请求时读取 app.state(届时已完成装配)。"""
    settings = app.state.settings
    if settings.security_mode == "secure-remote":
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(settings.allowed_hosts_list),
            allowed_origins=list(settings.allowed_origins_list),
        )
    else:
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "localhost",
                "localhost:*",
                "127.0.0.1",
                "127.0.0.1:*",
                "[::1]",
                "[::1]:*",
                *settings.allowed_hosts_list,
            ],
            allowed_origins=[
                "http://localhost:*",
                "http://127.0.0.1:*",
                "http://[::1]:*",
                *settings.allowed_origins_list,
            ],
        )
    mcp = FastMCP(
        "tianshu",
        instructions=(
            "天枢(Tianshu)异步 AI 执行平台。submit_edict 下旨后台执行,"
            "get_edict_status 跟踪进度,get_memorial 取结果全文;"
            "需要人工批红的事项只能在天枢 Web/飞书端处理。"
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=transport_security,
    )

    def _require_scope(scope: str):
        from tianshu.gateway.auth import get_current_auth_context

        context = get_current_auth_context()
        if context is None:
            raise PermissionError("MCP authentication context unavailable")
        if scope not in context.principal.scopes:
            raise PermissionError(f"MCP scope required: {scope}")
        return context

    @mcp.tool()
    def submit_edict(goal: str, context: str = "") -> dict:
        """下旨:提交一道诏令(异步执行)。返回 edict_id 与 memorial_id 用于跟踪。"""
        from tianshu.edict_ops import submit_new_edict
        from tianshu.models.edict import Edict, title_from_goal

        auth_context = _require_scope("mcp:submit")
        edict = Edict(
            title=title_from_goal(goal, None),
            goal=goal,
            context=context or None,
            submitter=auth_context.principal.id,
        )
        memorial = submit_new_edict(
            app.state.storage,
            app.state.event_bus,
            edict,
            producer=f"mcp:{auth_context.principal.id}",
        )
        return {"edict_id": edict.id, "memorial_id": memorial.id, "status": "submitted"}

    @mcp.tool()
    def get_edict_status(edict_id: str) -> dict:
        """查询诏令状态与各次执行(奏折)概要。"""
        _require_scope("mcp:read")
        storage = app.state.storage
        edict = storage.get_edict(edict_id)
        if not edict:
            return {"error": f"edict {edict_id} not found"}
        memorials = storage.list_memorials_by_edict(edict_id)
        return {
            "edict_id": edict.id,
            "title": edict.title,
            "status": edict.status.value,
            "memorials": [_memorial_brief(m) for m in memorials],
        }

    @mcp.tool()
    def get_memorial(memorial_id: str) -> dict:
        """取一份奏折(执行记录)的结果全文与审计结论。"""
        _require_scope("mcp:read")
        storage = app.state.storage
        m = storage.get_memorial(memorial_id)
        if not m:
            return {"error": f"memorial {memorial_id} not found"}
        return {
            **_memorial_brief(m),
            "result": _clip(m.final_output or m.result),
            "error": _clip(m.error),
            "audit": m.audit.model_dump(mode="json") if m.audit else None,
        }

    @mcp.tool()
    def list_recent_edicts(limit: int = 10) -> dict:
        """列出最近的诏令(默认 10 条)。"""
        _require_scope("mcp:read")
        storage = app.state.storage
        edicts, total = storage.list_edicts(limit=min(limit, 50), exclude_assistant_chat=True)
        return {
            "total": total,
            "edicts": [
                {
                    "edict_id": e.id,
                    "title": e.title,
                    "status": e.status.value,
                    "created_at": e.created_at.isoformat(),
                }
                for e in edicts
            ],
        }

    @mcp.tool()
    def list_pending_approvals() -> dict:
        """列出等待人工批红的奏折(只读;批红本身请在天枢 Web/飞书端完成)。"""
        _require_scope("mcp:read")
        storage = app.state.storage
        memorials, total = storage.list_memorials(status="needs_review", limit=20)
        return {"total": total, "pending": [_memorial_brief(m) for m in memorials]}

    return mcp
