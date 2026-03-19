"""Digest — periodic summary notifications (daily/weekly)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.notifier.channel_registry import ChannelRegistry
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

    def generate_weekly(self) -> dict:
        """Generate a weekly summary."""
        summary = self._storage.get_cost_summary(period="week")
        return {
            "title": "Tianshu Weekly Digest",
            "type": "digest.weekly",
            "stats": {
                "tokens_used": summary.get("total_tokens", 0),
                "cost_cny": summary.get("total_cost_cny", 0.0),
            },
        }
