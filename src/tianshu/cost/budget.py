"""Budget checking and circuit-breaker logic."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.cost.models import BudgetStatus

if TYPE_CHECKING:
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class BudgetChecker:
    """Check cost budgets and enforce limits."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def check_global(self) -> BudgetStatus | None:
        """Check the global budget, return status or None if no budget set."""
        row = self._storage.get_budget("global")
        if not row:
            return None
        return self._to_status(row)

    def check_edict(self, edict_id: str) -> BudgetStatus | None:
        """Check edict-specific budget."""
        row = self._storage.get_budget(f"edict:{edict_id}")
        if not row:
            return None
        return self._to_status(row)

    def check_submitter(self, submitter: str) -> BudgetStatus | None:
        """Check submitter-specific budget."""
        row = self._storage.get_budget(f"submitter:{submitter}")
        if not row:
            return None
        return self._to_status(row)

    def is_exceeded(self, edict_id: str, submitter: str | None = None) -> tuple[bool, str]:
        """Check all applicable budgets. Returns (exceeded, reason)."""
        # Check global budget
        global_status = self.check_global()
        if global_status and global_status.exceeded:
            return True, f"Global budget exceeded: ¥{global_status.spent_cny:.4f} / ¥{global_status.budget_cny:.4f}"

        # Check edict budget
        edict_status = self.check_edict(edict_id)
        if edict_status and edict_status.exceeded:
            return True, f"Edict budget exceeded: ¥{edict_status.spent_cny:.4f} / ¥{edict_status.budget_cny:.4f}"

        # Check submitter budget
        if submitter:
            sub_status = self.check_submitter(submitter)
            if sub_status and sub_status.exceeded:
                return True, f"Submitter budget exceeded: ¥{sub_status.spent_cny:.4f} / ¥{sub_status.budget_cny:.4f}"

        return False, ""

    @staticmethod
    def _to_status(row: dict) -> BudgetStatus:
        budget = row["budget_cny"]
        spent = row["spent_cny"]
        return BudgetStatus(
            scope=row["scope"],
            budget_cny=budget,
            spent_cny=spent,
            remaining_cny=max(0.0, budget - spent),
            period=row.get("period", "monthly"),
            exceeded=spent >= budget,
        )
