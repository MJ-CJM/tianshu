"""Telegram 卡片构造：/list /menu /budget → (text, InlineKeyboardMarkup)。

镜像 feishu/card_builder.py，但产出 Telegram 原生 inline keyboard 而非 lark 卡片 JSON。
callback_data 协议（≤64 字节）：
  "cmd:list" | "cmd:budget" | "cmd:clear" | "cmd:help" | "cmd:select:<id8>"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tianshu.gateway.core.budget import query_budget_data
from tianshu.gateway.core.status_label import format_status_label

if TYPE_CHECKING:
    from tianshu.cost.manager import CostManager
    from tianshu.models.edict import Edict
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)

TelegramCard = tuple[str, "InlineKeyboardMarkup | None"]


class TelegramCardBuilder:
    def __init__(
        self,
        *,
        storage: Storage,
        cost_manager: CostManager | None = None,
    ) -> None:
        self._storage = storage
        self._cost_manager = cost_manager

    # --- /list ---

    def build_list_card(
        self,
        edicts: list[Edict],
        current_anchor: str | None = None,
    ) -> TelegramCard:
        rows: list[str] = []
        buttons: list[list[InlineKeyboardButton]] = []
        for e in edicts:
            star = "★ " if e.id == current_anchor else ""
            title_short = (e.title or "(无标题)")[:30]
            status_label = format_status_label(e.status)
            short_id = e.id[:8]
            rows.append(f"{star}**#{short_id}** · {status_label} · {title_short}")
            buttons.append(
                [
                    InlineKeyboardButton(
                        f"切到 #{short_id}",
                        callback_data=f"cmd:select:{short_id}",
                    )
                ]
            )
        body = f"📋 **最近敕令（{len(edicts)} 条）**\n\n" + "\n".join(rows)
        kb = InlineKeyboardMarkup(buttons) if buttons else None
        return body, kb

    # --- /menu ---

    def build_menu_card(self, *, edict_id: str | None = None) -> TelegramCard:
        """主菜单。``edict_id`` 非空表示当前在敕令模式，给敕令模式的命令表。

        敕令模式下 /menu 也走同一个实现（edict_branch 委托），此前一律渲染助手
        命令表——用户被业务敕令接管后翻菜单，找不到 `/exit` 出口。
        """
        if edict_id:
            text = (
                f"📜 **敕令模式 #{edict_id[:8]}**\n\n"
                "当前纯文本会续接本敕令：\n"
                "📊 `/status` 查看状态\n"
                "🛑 `/cancel` 取消本敕令\n"
                "🚪 `/exit` 退出，回到助手对话\n"
                "✏️ `/new <目标>` 自动退出 + 新建\n"
                "📋 `/list` `/budget` 查询（不切换）\n\n"
                "_想回到助手（按配置的人格答话），点下方「退出敕令」。_"
            )
            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🚪 退出敕令", callback_data="cmd:exit"),
                        InlineKeyboardButton("📋 列表", callback_data="cmd:list"),
                    ],
                    [
                        InlineKeyboardButton("💰 预算", callback_data="cmd:budget"),
                        InlineKeyboardButton("❓ 帮助", callback_data="cmd:help"),
                    ],
                ]
            )
            return text, kb

        text = (
            "🏛️ **主菜单**\n\n"
            "**助手模式可用命令**：\n"
            "📋 `/list [open|completed|all]` 查敕令列表\n"
            "✏️ `/new <目标>` 新建敕令\n"
            "🔀 `/select <ID 前缀>` 切到指定敕令\n"
            "💰 `/budget` 成本概览\n"
            "🧹 `/clear` 归档对话 + 开新会话\n"
            "❓ `/help` 完整帮助\n\n"
            "_直接输入命令即可。纯文本会进入助手对话。_"
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📋 列表", callback_data="cmd:list"),
                    InlineKeyboardButton("💰 预算", callback_data="cmd:budget"),
                ],
                [
                    InlineKeyboardButton("🧹 新会话", callback_data="cmd:clear"),
                    InlineKeyboardButton("❓ 帮助", callback_data="cmd:help"),
                ],
            ]
        )
        return text, kb

    # --- /budget ---

    async def build_budget_card(self) -> TelegramCard:
        if self._cost_manager is None:
            return self._budget_unavailable()
        try:
            return await self._build_budget_real()
        except Exception:
            logger.exception("[telegram/card] budget card build failed")
            return self._budget_unavailable()

    async def _build_budget_real(self) -> TelegramCard:
        if self._cost_manager is None:
            return self._budget_unavailable()
        data = query_budget_data(self._storage, self._cost_manager)
        recent_total = data["recent_total"]
        budget = data["budget"]
        top_edicts = data["top_edicts"]

        lines = ["💰 **成本概览（近 7 天）**", "", f"**近 7 天消费**：¥{recent_total:.2f}"]
        if budget is not None:
            lines.append(f"**当前预算**：¥{budget.budget_cny:.2f}")
            lines.append(f"**剩余**：¥{(budget.budget_cny - budget.spent_cny):.2f}")
        if top_edicts:
            lines.append("")
            lines.append("**Top 5 敕令成本**：")
            for eid, title, cost in top_edicts:
                lines.append(f"- #{eid[:8]} ¥{cost:.2f}（{title}）")
        return "\n".join(lines), None

    @staticmethod
    def _budget_unavailable() -> TelegramCard:
        return "💰 **成本概览**\n\n_暂时无法获取成本数据，请稍后重试或在 web 端查看。_", None


__all__ = ["TelegramCardBuilder", "TelegramCard"]
