"""卡片构造：/list /menu /budget。

按钮 value 协议（统一）：
{
  "command": "select" | "list" | "budget" | "help" | "new" | "cancel",
  "edict_id"?: str,
  "goal"?: str,
  "filter"?: str,
}
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
        storage: "Storage",
        cost_manager: "CostManager | None" = None,
    ) -> None:
        self._storage = storage
        self._cost_manager = cost_manager

    # --- /list 卡片 ---

    def build_list_card(
        self,
        edicts: list["Edict"],
        current_anchor: str | None = None,
    ) -> dict:
        """每条敕令一行 markdown + 一个主按钮"切换到此敕令"。"""
        elements: list[dict] = []
        for i, e in enumerate(edicts):
            star = "★ " if e.id == current_anchor else ""
            title_short = (e.title or "(无标题)")[:30]
            elements.append({
                "tag": "markdown",
                "content": f"{star}**#{e.id[:8]}** · {e.status} · {title_short}",
            })
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "切换到此敕令"},
                        "type": "primary" if e.id == current_anchor else "default",
                        "value": {"command": "select", "edict_id": e.id},
                    }
                ],
            })
            if i < len(edicts) - 1:
                elements.append({"tag": "hr"})

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

    def build_menu_card(self) -> dict:
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "purple",
                "title": {"tag": "plain_text", "content": "🏛️ 主菜单"},
            },
            "elements": [
                {"tag": "markdown", "content": "_请选择操作 ↓_"},
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "📋 查看列表"},
                            "value": {"command": "list", "filter": "open"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "💰 成本概览"},
                            "value": {"command": "budget"},
                        },
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "❓ 帮助"},
                            "value": {"command": "help"},
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
        """Step 6 占位：仅显示当前 budget；Step 7 加近 7 天 + Top 5。"""
        budget = self._cost_manager.get_budget("global") if self._cost_manager else None
        if budget is None:
            return self._budget_unavailable_card()
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "💰 预算概览"},
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        f"**当前预算**：¥{budget.budget_cny:.2f}\n"
                        f"**已花费**：¥{budget.spent_cny:.2f}\n"
                        f"**剩余**：¥{budget.budget_cny - budget.spent_cny:.2f}"
                    ),
                }
            ],
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
                {"tag": "markdown", "content": "_暂时无法获取成本数据，请稍后重试或在 web 端查看。_"},
            ],
        }


__all__ = ["CardBuilder"]
