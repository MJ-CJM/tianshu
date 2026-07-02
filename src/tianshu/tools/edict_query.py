"""敕令查询工具 —— 让助手 LLM 能在对话中"查阅"系统中已颁敕令。

提供两个只读工具：
- ``list_edicts``：列敕令（默认列未完成的业务敕令；过滤掉聊天会话敕令）。
- ``get_edict_status``：按短 ID 前缀查询某道敕令的最新执行状态。

权限模型与 ``submit_edict`` 一致：默认在 startup 时禁用，由通政司
``enable_edict_submission`` toggle 统一启用/禁用，构成"敕令管理"工具集。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tianshu.models.common import EDICT_STATUS_LABELS, MEMORIAL_STATUS_LABELS
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, error_result, ok_result

if TYPE_CHECKING:
    from tianshu.models.edict import Edict
    from tianshu.models.memorial import Memorial
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

_VALID_LIST_STATUS = ("open", "completed", "cancelled", "all")
_MIN_PREFIX_LEN = 6   # 与 feishu 助手 /select /status 命令保持一致


def _label_edict_status(value: str) -> str:
    return EDICT_STATUS_LABELS.get(value, value)


def _label_memorial_status(value: str) -> str:
    return MEMORIAL_STATUS_LABELS.get(value, value)


def _humanize_age(created_at: datetime) -> str:
    """把 created_at 渲染成"5 分钟前 / 2 小时前 / 3 天前"。"""
    now = datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    delta = now - created_at
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds} 秒前"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} 小时前"
    days = hours // 24
    if days < 30:
        return f"{days} 天前"
    months = days // 30
    if months < 12:
        return f"{months} 个月前"
    return f"{days // 365} 年前"


def _edict_brief(edict: Edict, memorial: Memorial | None) -> dict:
    """供 details 字段使用的紧凑结构。"""
    brief: dict = {
        "edict_id": edict.id,
        "short_id": edict.id[:8],
        "title": edict.title or "(无标题)",
        "goal": edict.goal,
        "status": edict.status.value,
        "priority": edict.priority,
        "execution_profile": edict.execution_profile,
        "assigned_persona_id": edict.assigned_persona_id,
        "created_at": edict.created_at.isoformat(),
    }
    if memorial is not None:
        brief["memorial_status"] = memorial.status.value
        brief["memorial_summary"] = memorial.summary
        brief["memorial_error"] = memorial.error
    return brief


def register_edict_query(
    registry: ToolRegistry,
    *,
    storage: Storage,
) -> None:
    """注册 list_edicts / get_edict_status 到 ToolRegistry。"""

    async def list_edicts(
        status: str = "open",
        search: str | None = None,
        limit: int = 10,
    ) -> ToolResult:
        if status not in _VALID_LIST_STATUS:
            return error_result(
                f"list_edicts: status 必须是 {'|'.join(_VALID_LIST_STATUS)}（实得 {status}）",
            )
        if limit < 1 or limit > 50:
            return error_result("list_edicts: limit 取值范围 1~50")

        status_filter = None if status == "all" else status
        edicts, total = storage.list_edicts(
            status=status_filter,
            search=search,
            limit=limit,
            offset=0,
            exclude_assistant_chat=True,   # 隐藏聊天敕令，仅列业务敕令
        )

        if not edicts:
            scope = _label_edict_status(status) if status != "all" else "全部状态"
            keyword_hint = f"（关键字「{search}」）" if search else ""
            return ok_result(
                f"未查到{scope}的敕令{keyword_hint}。",
                details={"total": 0, "items": []},
            )

        scope = _label_edict_status(status) if status != "all" else "全部状态"
        lines = [f"共 {total} 道{scope}的敕令（显示前 {len(edicts)} 道）：", ""]
        items: list[dict] = []
        for idx, e in enumerate(edicts, start=1):
            memorial = storage.get_memorial_by_edict(e.id)
            items.append(_edict_brief(e, memorial))
            mem_label = (
                f" · 进度：{_label_memorial_status(memorial.status.value)}"
                if memorial else ""
            )
            persona_label = f" · 执行：{e.assigned_persona_id}" if e.assigned_persona_id else ""
            lines.append(
                f"{idx}. #{e.id[:8]} 「{e.title or '(无标题)'}」"
                f"\n   状态：{_label_edict_status(e.status.value)}{mem_label}"
                f" · 优先级：{e.priority}{persona_label}"
                f" · {_humanize_age(e.created_at)}",
            )
        if total > len(edicts):
            lines.append("")
            lines.append(f"（还有 {total - len(edicts)} 道未显示，可用 search/limit 进一步筛选）")

        return ok_result(
            "\n".join(lines),
            details={"total": total, "items": items},
        )

    registry.register(
        "list_edicts",
        list_edicts,
        ToolDefinition(
            name="list_edicts",
            description=(
                "查阅系统中已颁敕令的清单（默认列出尚未完成的业务敕令）。"
                "用户问'还有哪些敕令没完成'/'帮我看看任务列表'/'列一下今天的敕令'时调用。"
                "返回每道敕令的短 ID、标题、当前状态与最近一次执行进度。"
                "若需查某道敕令详情，再用 get_edict_status。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": list(_VALID_LIST_STATUS),
                        "description": (
                            "状态过滤：open=未完成（默认）；completed=已完成；"
                            "cancelled=已取消；all=不限状态。"
                        ),
                    },
                    "search": {
                        "type": "string",
                        "description": "可选：标题或目标的关键字模糊匹配。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 50,
                        "description": "返回条数上限，1~50，默认 10。",
                    },
                },
                "required": [],
            },
            tier=ToolTier.T0_READONLY.value,
            max_result_chars=4096,
        ),
    )

    async def get_edict_status(edict_id: str) -> ToolResult:
        if not edict_id or not edict_id.strip():
            return error_result("get_edict_status: edict_id 不能为空")
        prefix = edict_id.strip().lstrip("#")
        if len(prefix) < _MIN_PREFIX_LEN:
            return error_result(
                f"get_edict_status: ID 前缀至少 {_MIN_PREFIX_LEN} 字符以避免歧义",
            )

        # 优先精确命中（用户给完整 26 字符 ULID 时）
        edict = storage.get_edict(prefix) if len(prefix) >= 20 else None
        if edict is None:
            # 前缀匹配：扫一定范围（含已完成/已取消，以便用户能查历史敕令）
            candidates, _ = storage.list_edicts(
                limit=500, offset=0, exclude_assistant_chat=True,
            )
            matches = [e for e in candidates if e.id.startswith(prefix)]
            if not matches:
                return error_result(
                    f"get_edict_status: 未找到敕令 #{prefix}（可先用 list_edicts 浏览）",
                )
            if len(matches) > 1:
                preview = ", ".join(f"#{e.id[:12]}" for e in matches[:5])
                return error_result(
                    f"get_edict_status: 短 ID '{prefix}' 命中多道：{preview}，请用更长前缀",
                )
            edict = matches[0]

        memorial = storage.get_memorial_by_edict(edict.id)
        all_memorials = storage.list_memorials_by_edict(edict.id)
        attempts = len(all_memorials)

        lines: list[str] = [
            f"📋 #{edict.id[:8]} 「{edict.title or '(无标题)'}」",
            f"状态：{_label_edict_status(edict.status.value)}"
            f" · 优先级：{edict.priority}"
            f" · 模式：{edict.execution_profile}",
            f"创建：{_humanize_age(edict.created_at)}"
            + (f" · 指派：{edict.assigned_persona_id}" if edict.assigned_persona_id else ""),
            f"目标：{edict.goal}",
        ]
        if edict.context:
            ctx_preview = edict.context if len(edict.context) <= 200 else edict.context[:200] + "…"
            lines.append(f"上下文：{ctx_preview}")

        if memorial is not None:
            lines.append("")
            lines.append(
                f"最近一次执行（共 {attempts} 次）："
                f"\n- 状态：{_label_memorial_status(memorial.status.value)}",
            )
            if memorial.summary:
                summary_preview = (
                    memorial.summary if len(memorial.summary) <= 300
                    else memorial.summary[:300] + "…"
                )
                lines.append(f"- 摘要：{summary_preview}")
            if memorial.error:
                err_preview = (
                    memorial.error if len(memorial.error) <= 200
                    else memorial.error[:200] + "…"
                )
                lines.append(f"- 错误：{err_preview}")
            if memorial.usage and memorial.usage.total_tokens:
                lines.append(
                    f"- 用量：{memorial.usage.total_tokens} tokens"
                    f" · 成本 ¥{memorial.usage.cost_cny:.4f}",
                )
        else:
            lines.append("")
            lines.append("（尚无执行记录）")

        return ok_result(
            "\n".join(lines),
            details=_edict_brief(edict, memorial),
        )

    registry.register(
        "get_edict_status",
        get_edict_status,
        ToolDefinition(
            name="get_edict_status",
            description=(
                "按短 ID 前缀（≥6 字符）查询某道敕令的当前状态、最近一次执行摘要与错误信息。"
                "用户说'#abc123 进度怎么样'/'帮我看看那道敕令完成了吗'时调用。"
                "返回敕令本体信息（标题、目标、优先级、模式）+ 最近一次 memorial 的状态与摘要。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "edict_id": {
                        "type": "string",
                        "description": (
                            "敕令的短 ID 前缀（至少 6 字符，例如 'abc12345'）"
                            "或完整 ULID。允许前置 # 号。"
                        ),
                    },
                },
                "required": ["edict_id"],
            },
            tier=ToolTier.T0_READONLY.value,
            max_result_chars=2048,
        ),
    )


__all__ = ["register_edict_query"]
