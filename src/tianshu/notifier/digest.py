"""Digest — periodic summary notifications (daily/weekly)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class DigestGenerator:
    """Generates digest reports from memorials and cost data."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def generate_daily(self) -> dict:
        """Generate a daily summary."""
        summary = self._storage.get_cost_summary(period="day")
        memorials, total = self._storage.list_memorials(limit=20)

        completed = sum(1 for m in memorials if m.status.value == "completed")
        failed = sum(1 for m in memorials if m.status.value == "failed")

        return {
            "title": "Tianshu Daily Digest",
            "type": "digest.daily",
            "stats": {
                "total_memorials": total,
                "completed": completed,
                "failed": failed,
                "tokens_used": summary.get("total_tokens", 0),
                "cost_cny": summary.get("total_cost_cny", 0.0),
            },
        }

    def generate_weekly(self, since_iso: str | None = None) -> dict:
        """实录馆·自动汇编(迭代 7):把本周敕令/成本/执行/代批数据汇编成《实录》周报。

        since_iso 缺省取近 7 天;窗口统计走 report_window_stats(敕令/执行/司礼监代批),
        成本走 get_cost_summary。structured 汇编(LLM 润色《实录》文体为后续增量)。
        """
        if since_iso is None:
            from datetime import UTC, datetime, timedelta

            since_iso = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        summary = self._storage.get_cost_summary(period="week")
        window = self._storage.report_window_stats(since_iso)
        return {
            "title": "实录馆·本周实录",
            "type": "digest.weekly",
            "narrative": (
                f"本周颁敕 {window['edicts']} 道,执行 {window['memorials_total']} 件"
                f"(成 {window['completed']} / 败 {window['failed']}),"
                f"司礼监代批 {window['auto_approvals']} 笔;耗资 ¥{summary.get('total_cost_cny', 0.0):.2f}。"
            ),
            "stats": {
                **window,
                "tokens_used": summary.get("total_tokens", 0),
                "cost_cny": summary.get("total_cost_cny", 0.0),
            },
        }
