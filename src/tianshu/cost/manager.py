"""CostManager — record costs, check budgets, integrate with hooks and events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.cost.budget import BudgetChecker
from tianshu.cost.models import BudgetStatus, CostRecord, CostSummary
from tianshu.cost.tracker import CostTracker

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus
    from tianshu.models.events import EventEnvelope
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class CostManager:
    """Tracks LLM costs and enforces budgets.

    Integrates with:
    - Hooks: LLM_OUTPUT → accumulate cost per call
             BEFORE_ITERATION → budget circuit breaker
    - Events: execution.completed/failed → persist final cost record
    """

    def __init__(self, storage: Storage, event_bus: EventBus | None = None) -> None:
        self._storage = storage
        self._event_bus = event_bus
        self._budget_checker = BudgetChecker(storage)
        # Per-edict trackers (cleared on execution end)
        self._trackers: dict[str, CostTracker] = {}

    def _get_tracker(self, edict_id: str) -> CostTracker:
        if edict_id not in self._trackers:
            self._trackers[edict_id] = CostTracker()
        return self._trackers[edict_id]

    # --- Core operations ---

    def record(self, record: CostRecord) -> None:
        """Persist a cost record to storage."""
        self._storage.save_cost_record(record)
        # Update budget spent
        if record.cost_cny > 0:
            self._storage.update_budget_spent("global", record.cost_cny)
            self._storage.update_budget_spent(f"edict:{record.edict_id}", record.cost_cny)

    def get_summary(
        self,
        period: str | None = None,
        edict_id: str | None = None,
    ) -> CostSummary:
        """Get cost summary for a period/edict."""
        data = self._storage.get_cost_summary(period=period, edict_id=edict_id)
        return CostSummary(**data)

    def get_records(
        self,
        edict_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        return self._storage.list_cost_records(edict_id=edict_id, limit=limit, offset=offset)

    def get_budget(self, scope: str) -> BudgetStatus | None:
        return self._budget_checker.check_global() if scope == "global" else None

    def set_budget(self, scope: str, budget_cny: float, period: str = "monthly") -> None:
        self._storage.upsert_budget(scope, budget_cny, period)

    # --- Hook handlers ---

    async def on_llm_output(self, **context: object) -> None:
        """LLM_OUTPUT hook — accumulate cost for each LLM call."""
        edict = context.get("edict")
        usage = context.get("usage")
        if not edict or not usage:
            return

        edict_id = getattr(edict, "id", "")
        if not edict_id:
            return

        tracker = self._get_tracker(edict_id)

        prompt_tokens = getattr(usage, "prompt_tokens", 0)
        completion_tokens = getattr(usage, "completion_tokens", 0)
        cache_read_tokens = getattr(usage, "cache_read_tokens", 0)
        provider_name = context.get("provider_name") or None
        model = ""
        # Try to get model from config_manager if available
        state = context.get("config_state")
        if state:
            model = getattr(state, "model", "")

        tracker.accumulate(
            model, prompt_tokens, completion_tokens,
            cache_read_tokens=cache_read_tokens,
            provider_name=provider_name,
        )

    async def on_before_iteration(self, **context: object) -> object:
        """BEFORE_ITERATION hook — circuit breaker for budget enforcement."""
        from tianshu.executor.hooks import HookResult

        edict = context.get("edict")
        if not edict:
            return None

        edict_id = getattr(edict, "id", "")
        if not edict_id:
            return None

        # Check runtime cost_budget_cny
        runtime = getattr(edict, "runtime", None)
        if runtime:
            cost_budget = getattr(runtime, "cost_budget_cny", None)
            if cost_budget and cost_budget > 0:
                tracker = self._get_tracker(edict_id)
                if tracker.cost_cny >= cost_budget:
                    reason = f"Cost budget exceeded: ¥{tracker.cost_cny:.4f} >= ¥{cost_budget:.4f}"
                    logger.warning("Budget circuit breaker: %s", reason)
                    # Emit budget exceeded event
                    if self._event_bus:
                        from tianshu.models.events import make_event
                        await self._event_bus.emit(
                            make_event(
                                "cost.budget_exceeded",
                                edict_id=edict_id,
                                producer="cost_manager",
                                payload={"reason": reason, "cost_cny": tracker.cost_cny},
                            )
                        )
                    return HookResult(block=True, reason=reason)

        # Check global/edict budgets from storage
        submitter = getattr(edict, "submitter", None)
        exceeded, reason = self._budget_checker.is_exceeded(edict_id, submitter)
        if exceeded:
            return HookResult(block=True, reason=reason)

        return None

    # --- EventBus handlers ---

    async def handle_execution_completed(self, event: EventEnvelope) -> None:
        """Persist final cost record when execution completes."""
        await self._finalize_cost(event)

    async def handle_execution_failed(self, event: EventEnvelope) -> None:
        """Persist cost record even on failure."""
        await self._finalize_cost(event)

    async def _finalize_cost(self, event: EventEnvelope) -> None:
        edict_id = event.edict_id
        if not edict_id:
            return

        tracker = self._trackers.pop(edict_id, None)
        if not tracker or tracker.total_tokens == 0:
            return

        record = CostRecord(
            edict_id=edict_id,
            memorial_id=event.memorial_id,
            provider_name=tracker.last_provider_name or "default",
            model="",
            prompt_tokens=tracker.prompt_tokens,
            completion_tokens=tracker.completion_tokens,
            total_tokens=tracker.total_tokens,
            cache_read_tokens=tracker.cache_read_tokens,
            cost_cny=tracker.cost_cny,
        )
        self.record(record)
        logger.info(
            "Cost recorded for edict %s: %d tokens, ¥%.4f",
            edict_id, tracker.total_tokens, tracker.cost_cny,
        )
