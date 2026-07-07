"""预算查询：/budget 卡片共享的数据获取（cost_ledger 查询 + top_edicts 计算）。

通道无关——feishu/telegram 的 card_builder 各自调用后按自己的渲染方式
（lark 卡片 JSON vs 文本 + InlineKeyboard）组装展示。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.cost.manager import CostManager
    from tianshu.cost.models import BudgetStatus
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


def query_budget_data(storage: Storage, cost_manager: CostManager) -> dict:
    """近 7 天总消费 + 当前预算 + Top 5 高消费敕令。

    返回 dict：
    - recent_total (float): 近 7 天总消费（cost_ledger 全量求和）
    - budget (BudgetStatus | None): 当前 "global" scope 预算状态
    - top_edicts (list[tuple[str, str, float]]): Top 5 高消费敕令
      (edict_id, title, cost)
    """
    budget: BudgetStatus | None = cost_manager.get_budget("global")

    # 近 7 天总消费 + Top 5 敕令（直接查 cost_ledger 表）
    recent_total = 0.0
    top_edicts: list[tuple[str, str, float]] = []
    try:
        rows = storage._conn.execute(
            "SELECT edict_id, SUM(cost_cny) as total FROM cost_ledger "
            "WHERE created_at >= datetime('now', '-7 days') AND edict_id IS NOT NULL "
            "GROUP BY edict_id ORDER BY total DESC LIMIT 5"
        ).fetchall()
        for row in rows:
            edict_id = row[0]
            total = float(row[1] or 0.0)
            edict = storage.get_edict(edict_id)
            title = (edict.title if edict else "(已删)") or "(无标题)"
            top_edicts.append((edict_id, title[:20], total))
        # 整体 7 天累计（含未在 top5 的）
        full_total_row = storage._conn.execute(
            "SELECT SUM(cost_cny) FROM cost_ledger WHERE created_at >= datetime('now', '-7 days')"
        ).fetchone()
        if full_total_row and full_total_row[0]:
            recent_total = float(full_total_row[0])
    except Exception:
        logger.exception("[gateway/budget] cost ledger query failed")

    return {"recent_total": recent_total, "budget": budget, "top_edicts": top_edicts}


__all__ = ["query_budget_data"]
