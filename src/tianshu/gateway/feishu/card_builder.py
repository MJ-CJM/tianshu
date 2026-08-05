"""卡片构造：/list /menu /budget。

按钮 value 协议（统一）：
{
  "command": "select" | "list" | "budget" | "help" | "new" | "cancel" | "exit" | "clear",
  "edict_id"?: str,
  "goal"?: str,
  "filter"?: str,
}
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.gateway.core.budget import query_budget_data
from tianshu.gateway.core.status_label import format_status_label  # re-export，向后兼容

if TYPE_CHECKING:
    from tianshu.cost.manager import CostManager
    from tianshu.models.edict import Edict
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class CardBuilder:
    """构造各类卡片 payload（dict）。"""

    def __init__(
        self,
        *,
        storage: Storage,
        cost_manager: CostManager | None = None,
    ) -> None:
        self._storage = storage
        self._cost_manager = cost_manager

    # --- /list 卡片 ---

    def build_list_card(
        self,
        edicts: list[Edict],
        current_anchor: str | None = None,
    ) -> dict:
        """每条敕令一行，右侧「切换」按钮直接切 anchor，免去手抄 ULID。

        按钮 value 走 CardActionDispatcher 的通用协议（``{"command": "select"}``），
        点击等价于输入 `/select <完整 ID>`。

        ⚠️ 按钮可用有个**平台侧前提**：飞书开放平台须为该应用开启「卡片回调」
        （未开启时点按钮，飞书会弹「该应用尚未配置卡片回调」并引导一键配置）。
        接收侧已就绪——lark-oapi 1.5.5 不分派 CARD 帧的 bug 由 connection.py 的
        monkey patch 修掉了，但那只解决"收得到"，不解决"飞书愿不愿意推"。
        故底部**始终**保留文本命令提示作为退路，勿删。
        """
        elements: list[dict] = []
        for e in edicts:
            is_current = e.id == current_anchor
            star = "★ " if is_current else ""
            title_short = (e.title or "(无标题)")[:30]
            row: dict = {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"{star}**#{e.id[:8]}** · {format_status_label(e.status)} · {title_short}"
                    ),
                },
            }
            # 当前 anchor 那条不给按钮——点了是空操作，徒增误触
            if not is_current:
                row["extra"] = {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "切换"},
                    "type": "primary",
                    "value": {"command": "select", "edict_id": e.id},
                }
            elements.append(row)

        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "action",
                # 筛选按钮：/list 默认只看 open，已结案的一概不显示。给「全部」
                # 一个入口，免得用户以为敕令丢了。
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "🔄 进行中"},
                        "type": "default",
                        "value": {"command": "list", "filter": "open"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "📚 全部"},
                        "type": "default",
                        "value": {"command": "list", "filter": "all"},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "💰 成本"},
                        "type": "default",
                        "value": {"command": "budget"},
                    },
                ],
            },
        )
        elements.append(
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "也可输入 /select <ID>"}],
            },
        )

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {
                    "tag": "plain_text",
                    "content": f"📋 最近敕令（{len(edicts)} 条）",
                },
            },
            "elements": elements,
        }

    # --- /menu 卡片 ---

    def build_menu_card(self, *, edict_id: str | None = None) -> dict:
        """主菜单。``edict_id`` 非空表示当前在敕令模式，给敕令模式的命令表。

        敕令模式下 `/menu` 也走同一个实现（edict_branch 委托），此前一律渲染
        助手命令表——用户被业务敕令接管后翻菜单，找不到 `/exit` 出口。

        命令表用文字列全，不依赖按钮：按钮需飞书开放平台开启「卡片回调」才生效
        （详见 build_list_card 的说明），没开也得让用户看得到 `/exit` 怎么打。
        """
        if edict_id:
            return self._build_edict_menu(edict_id)
        return self._build_assistant_menu()

    @staticmethod
    def _build_assistant_menu() -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"tag": "plain_text", "content": "🏛️ 主菜单"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "**助手模式可用命令**：\n\n"
                        "📋 `/list [open|completed|all]` 查敕令列表\n"
                        "✏️ `/new <目标>` 新建敕令\n"
                        "🔀 `/select <ID 前缀>` 切到指定敕令\n"
                        "💰 `/budget` 成本概览\n"
                        "🧹 `/clear` 归档对话 + 开新会话\n"
                        "❓ `/help` 完整帮助\n\n"
                        "_直接输入命令即可。纯文本会进入助手对话。_"
                    ),
                },
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "📋 敕令列表"},
                            "type": "primary",
                            "value": {"command": "list", "filter": "open"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "💰 成本"},
                            "type": "default",
                            "value": {"command": "budget"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🧹 新会话"},
                            "type": "default",
                            "value": {"command": "clear"},
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _build_edict_menu(edict_id: str) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": f"📜 敕令模式 #{edict_id[:8]}"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "**当前在敕令模式**，纯文本会续接本敕令：\n\n"
                        "📊 `/status` 查看状态\n"
                        "🛑 `/cancel` 取消本敕令\n"
                        "🚪 `/exit` 退出，回到助手对话\n"
                        "✏️ `/new <目标>` 自动退出 + 新建\n"
                        "📋 `/list` `/budget` 查询（不切换）\n\n"
                        "_想回到助手（按通政司配置的人格答话），点下方「退出敕令」。_"
                    ),
                },
                {"tag": "hr"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "🚪 退出敕令"},
                            "type": "primary",
                            "value": {"command": "exit"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "📊 状态"},
                            "type": "default",
                            "value": {"command": "status", "edict_id": edict_id},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "📋 敕令列表"},
                            "type": "default",
                            "value": {"command": "list", "filter": "open"},
                        },
                    ],
                },
            ],
        }

    # --- /budget 卡片（Step 7 完整实现）---

    async def build_budget_card(self) -> dict:
        """成本概览卡片。Step 6 占位简化版；Step 7 接入 cost_ledger。"""
        if self._cost_manager is None:
            return self._budget_unavailable_card()
        try:
            return await self._build_budget_card_real()
        except Exception:
            logger.exception("[feishu/card] budget card build failed")
            return self._budget_unavailable_card()

    async def _build_budget_card_real(self) -> dict:
        """近 7 天总消费 + 当前预算 + Top 5 高消费敕令。"""
        if self._cost_manager is None:
            return self._budget_unavailable_card()
        data = query_budget_data(self._storage, self._cost_manager)
        recent_total = data["recent_total"]
        budget = data["budget"]
        top_edicts = data["top_edicts"]

        lines = [f"**近 7 天消费**：¥{recent_total:.2f}"]
        if budget is not None:
            lines.append(f"**当前预算**：¥{budget.budget_cny:.2f}")
            lines.append(f"**剩余**：¥{(budget.budget_cny - budget.spent_cny):.2f}")

        elements: list[dict] = [
            {"tag": "markdown", "content": "\n".join(lines)},
        ]
        if top_edicts:
            elements.append({"tag": "hr"})
            top_lines = ["**Top 5 敕令成本（近 7 天）**："]
            for eid, title, cost in top_edicts:
                top_lines.append(f"- #{eid[:8]} ¥{cost:.2f}（{title}）")
            elements.append({"tag": "markdown", "content": "\n".join(top_lines)})

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "💰 成本概览（近 7 天）"},
            },
            "elements": elements,
        }

    @staticmethod
    def _budget_unavailable_card() -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "grey",
                "title": {"tag": "plain_text", "content": "💰 成本概览"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": "_暂时无法获取成本数据，请稍后重试或在 web 端查看。_",
                },
            ],
        }


__all__ = ["CardBuilder"]
