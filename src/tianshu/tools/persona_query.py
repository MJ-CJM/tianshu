"""list_personas tool —— 让助手 LLM 查阅 DB 里实际注册的 persona 名册。

为什么需要这个工具：
没有它时，LLM 被问"现在有哪些官员可以下发敕令"会去翻代码仓库的 personas/
模板目录（git 跟踪的部门模板，不是运行时实例），把 ducha/hubu/neige/...
等"部门名"当成"官员名"列出来——既不准确又跟 DB 实际状态背离。

数据源是 storage.list_personas（DB personas 表），与 persona_loader.load_all
读的是同一份数据。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.types import ToolResult, ToolTier, ok_result

if TYPE_CHECKING:
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


def register_list_personas(
    registry: ToolRegistry,
    *,
    storage: "Storage",
) -> None:
    """注册 list_personas tool 到 ToolRegistry。"""

    async def list_personas(
        department: str | None = None,
        only_can_delegate: bool = False,
    ) -> ToolResult:
        rows = storage.list_personas()

        # 过滤
        if department:
            dept = department.strip().lower()
            rows = [r for r in rows if (r.get("department") or "").lower() == dept]
        if only_can_delegate:
            rows = [r for r in rows if r.get("can_delegate")]

        # 投影：只暴露给 LLM 决策需要的字段，避免 prompt 噪音。
        # role_path 一并返回 —— LLM 觉得 dept/title 不够判断时，
        # 可主动 read_file(role_path) 看 ROLE.md 详细职责。
        items = [
            {
                "id": r["id"],
                "name": r["name"],
                "department": r["department"],
                "title": r.get("title"),
                "llm_config_name": r.get("llm_config_name"),
                "can_delegate": bool(r.get("can_delegate")),
                "delegates_to": r.get("delegates_to") or [],
                "role_path": r.get("role_path"),
            }
            for r in rows
        ]

        if not items:
            hint = (
                "DB personas 表为空" if not department else
                f"DB 中无 department={department!r} 的 persona"
            )
            return ok_result(
                f"{hint}。请勿凭推测/翻代码模板编造官员名册。",
                details={"count": 0, "personas": []},
            )

        # 文本摘要供 LLM 直接读，details 给程序化下游
        lines = [f"共 {len(items)} 位 persona（来自 DB personas 表）："]
        for it in items:
            seg = f"- {it['id']} | dept={it['department']}"
            if it.get("title"):
                seg += f" | title={it['title']}"
            seg += f" | name={it['name']}"
            if it.get("llm_config_name"):
                seg += f" | llm={it['llm_config_name']}"
            if it.get("can_delegate"):
                seg += " | can_delegate=True"
            lines.append(seg)
        return ok_result(
            "\n".join(lines),
            details={"count": len(items), "personas": items},
        )

    registry.register(
        "list_personas",
        list_personas,
        ToolDefinition(
            name="list_personas",
            description=(
                "查阅当前 DB 里实际注册的 persona（官员）名册。"
                "用户问'有哪些官员/可以指派谁/朝廷里都有谁'时调用此工具；"
                "**submit_edict 未指定 assigned_persona_id 时也必须先调用此工具**，"
                "据各人 department/title 匹配任务擅长领域后再选定指派对象。"
                "返回每位 persona 的 id/部门/职务/绑定 LLM/role_path——"
                "若仅凭 dept/title 不足判断擅长领域，可对感兴趣的 persona "
                "用 read_file(role_path) 查看 ROLE.md 详细职责。"
                "**严禁去翻 personas/ 代码目录或凭训练知识猜测**，"
                "那是部门模板而非运行实例。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "department": {
                        "type": "string",
                        "description": (
                            "可选：按部门过滤（如 neige/ducha/hubu/tongzheng/wenyuan）。"
                            "不传返回全部。"
                        ),
                    },
                    "only_can_delegate": {
                        "type": "boolean",
                        "description": (
                            "可选：True 时只返回可承接转派的 persona "
                            "（用于'谁可以承接敕令'类问题）。默认 False。"
                        ),
                    },
                },
                "required": [],
            },
            tier=ToolTier.T0_READONLY.value,
            max_result_chars=4096,
        ),
    )


__all__ = ["register_list_personas"]
