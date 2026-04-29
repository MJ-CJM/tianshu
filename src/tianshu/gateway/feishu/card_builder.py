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
        """近 7 天总消费 + 当前预算 + Top 5 高消费敕令。"""
        if self._cost_manager is None:
            return self._budget_unavailable_card()
        budget = self._cost_manager.get_budget("global")

        # 近 7 天总消费 + Top 5 敕令（直接查 cost_ledger 表）
        recent_total = 0.0
        top_edicts: list[tuple[str, str, float]] = []
        try:
            rows = self._storage._conn.execute(
                "SELECT edict_id, SUM(cost_cny) as total FROM cost_ledger "
                "WHERE created_at >= datetime('now', '-7 days') AND edict_id IS NOT NULL "
                "GROUP BY edict_id ORDER BY total DESC LIMIT 5"
            ).fetchall()
            for row in rows:
                edict_id = row[0]
                total = float(row[1] or 0.0)
                edict = self._storage.get_edict(edict_id)
                title = (edict.title if edict else "(已删)") or "(无标题)"
                top_edicts.append((edict_id, title[:20], total))
            # 整体 7 天累计（含未在 top5 的）
            full_total_row = self._storage._conn.execute(
                "SELECT SUM(cost_cny) FROM cost_ledger "
                "WHERE created_at >= datetime('now', '-7 days')"
            ).fetchone()
            if full_total_row and full_total_row[0]:
                recent_total = float(full_total_row[0])
        except Exception:
            logger.exception("[feishu/card] cost ledger query failed")

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
                {"tag": "markdown", "content": "_暂时无法获取成本数据，请稍后重试或在 web 端查看。_"},
            ],
        }


__all__ = ["CardBuilder"]
