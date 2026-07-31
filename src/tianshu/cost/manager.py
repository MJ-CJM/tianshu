"""CostManager — record costs, check budgets, integrate with hooks and events."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.cost.budget import BudgetChecker
from tianshu.cost.models import BudgetStatus, CostRecord, CostSummary
from tianshu.cost.tracker import CostTracker

if TYPE_CHECKING:
    from tianshu.bus.event_bus import EventBus
    from tianshu.llm import LLMUsageContext
    from tianshu.models.common import UsageSummary
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
        # Per-run trackers. One Edict can have concurrent follow-ups/scheduled runs,
        # so edict_id alone is not a safe accounting identity.
        self._trackers: dict[tuple[str, str | None], CostTracker] = {}

    def _get_tracker(self, edict_id: str, memorial_id: str | None = None) -> CostTracker:
        key = (edict_id, memorial_id)
        if key not in self._trackers:
            self._trackers[key] = CostTracker()
        return self._trackers[key]

    # --- Core operations ---

    def record(self, record: CostRecord) -> None:
        """Persist a cost record to storage."""
        submitter: str | None = None
        if record.edict_id != "__platform__":
            edict = self._storage.get_edict(record.edict_id)
            submitter = edict.submitter if edict is not None else None

        self._storage.save_cost_record(record)
        # Update budget spent
        if record.cost_cny > 0:
            self._storage.update_budget_spent("global", record.cost_cny)
            if record.edict_id != "__platform__":
                self._storage.update_budget_spent(f"edict:{record.edict_id}", record.cost_cny)
            if submitter:
                self._storage.update_budget_spent(f"submitter:{submitter}", record.cost_cny)

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
        return self._budget_checker.check_scope(scope)

    def set_budget(
        self,
        scope: str,
        budget_cny: float,
        period: str = "monthly",
        reset_at: str | None = None,
    ) -> None:
        self._storage.upsert_budget(scope, budget_cny, period, reset_at=reset_at)

    def get_live_usage(self, edict_id: str, memorial_id: str | None) -> UsageSummary | None:
        """Return the complete in-flight usage observed for one durable run."""
        tracker = self._trackers.get((edict_id, memorial_id))
        if tracker is None:
            return None
        from tianshu.models.common import UsageSummary

        return UsageSummary(
            prompt_tokens=tracker.prompt_tokens,
            completion_tokens=tracker.completion_tokens,
            total_tokens=tracker.total_tokens,
            cache_read_tokens=tracker.cache_read_tokens,
            cost_cny=tracker.cost_cny,
        )

    # --- Hook handlers ---

    def observe_llm_usage(
        self,
        usage: UsageSummary,
        context: LLMUsageContext | None,
        provider_name: str | None,
        model: str,
    ) -> None:
        """Capture every LLMClient call, including non-Agent platform work."""

        edict_id, memorial_id = self._resolve_usage_subject(context)
        actual_model = usage.actual_model or model
        if edict_id == "__platform__":
            self.record(
                CostRecord(
                    edict_id=edict_id,
                    memorial_id=None,
                    provider_name=provider_name or "default",
                    model=actual_model,
                    prompt_tokens=usage.prompt_tokens,
                    completion_tokens=usage.completion_tokens,
                    total_tokens=usage.total_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cost_cny=usage.cost_cny,
                )
            )
            return
        self._accumulate(
            edict_id=edict_id,
            memorial_id=memorial_id,
            usage=usage,
            provider_name=provider_name,
            model=actual_model,
        )

    def _resolve_usage_subject(
        self,
        context: LLMUsageContext | None,
    ) -> tuple[str, str | None]:
        if context is not None and context.edict_id:
            return context.edict_id, context.memorial_id

        # Managed planning/critic/check calls share the dispatcher's durable authority.
        from tianshu.executor.managed_tools import get_managed_attempt_authority

        authority = get_managed_attempt_authority()
        if authority is not None:
            memorial = self._storage.get_memorial(authority.memorial_id)
            if memorial is not None:
                return memorial.edict_id, memorial.id

        from tianshu.kernel.ambient import get_current_edict

        edict = get_current_edict()
        if edict is not None:
            return edict.id, None
        return "__platform__", None

    def _accumulate(
        self,
        *,
        edict_id: str,
        memorial_id: str | None,
        usage: UsageSummary,
        provider_name: str | None,
        model: str,
    ) -> None:
        tracker = self._get_tracker(edict_id, memorial_id)
        tracker.accumulate(
            model,
            usage.prompt_tokens,
            usage.completion_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            provider_name=provider_name,
            cost_cny=usage.cost_cny,
        )

    async def on_llm_output(self, **context: object) -> None:
        """Backward-compatible Agent hook used by isolated tests/integrations."""
        edict = context.get("edict")
        usage = context.get("usage")
        if not edict or not usage:
            return

        edict_id = getattr(edict, "id", "")
        if not edict_id:
            return

        memorial = context.get("memorial")
        memorial_id = memorial if isinstance(memorial, str) else getattr(memorial, "id", None)
        provider_name = context.get("provider_name") or None
        # 归因模型：上游真实回显（真身）优先，其次运行时配置的模型名
        model = getattr(usage, "actual_model", None) or ""
        if not model:
            state = context.get("config_state")
            if state:
                model = getattr(state, "model", "")

        self._accumulate(
            edict_id=edict_id,
            memorial_id=memorial_id,
            usage=usage,  # type: ignore[arg-type]
            provider_name=provider_name if isinstance(provider_name, str) else None,
            model=model,
        )

    async def on_before_iteration(self, **context: object) -> object:
        """BEFORE_ITERATION hook — circuit breaker for budget enforcement."""
        from tianshu.kernel.hooks import HookResult

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
                running_cost = sum(
                    tracker.cost_cny
                    for (tracked_edict_id, _), tracker in self._trackers.items()
                    if tracked_edict_id == edict_id
                )
                if running_cost >= cost_budget:
                    reason = f"Cost budget exceeded: ¥{running_cost:.4f} >= ¥{cost_budget:.4f}"
                    logger.warning("Budget circuit breaker: %s", reason)
                    # Emit budget exceeded event
                    if self._event_bus:
                        from tianshu.models.events import make_event

                        await self._event_bus.emit(
                            make_event(
                                "cost.budget_exceeded",
                                edict_id=edict_id,
                                producer="cost_manager",
                                payload={"reason": reason, "cost_cny": running_cost},
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

    async def handle_execution_cancelled(self, event: EventEnvelope) -> None:
        """Persist cost record and release the run tracker on cancellation."""
        await self._finalize_cost(event)

    async def _finalize_cost(self, event: EventEnvelope) -> None:
        edict_id = event.edict_id
        if not edict_id:
            return

        key = (edict_id, event.memorial_id)
        tracker = self._trackers.pop(key, None)
        if tracker is None:
            # Legacy callers did not pass a memorial into the old LLM_OUTPUT hook.
            candidates = [k for k in self._trackers if k[0] == edict_id]
            if len(candidates) == 1:
                tracker = self._trackers.pop(candidates[0])
        if not tracker or tracker.total_tokens == 0:
            return

        record = CostRecord(
            edict_id=edict_id,
            memorial_id=event.memorial_id,
            provider_name=tracker.provider_label or "default",
            model=tracker.model_label or "",
            prompt_tokens=tracker.prompt_tokens,
            completion_tokens=tracker.completion_tokens,
            total_tokens=tracker.total_tokens,
            cache_read_tokens=tracker.cache_read_tokens,
            cost_cny=tracker.cost_cny,
        )
        self.record(record)
        logger.info(
            "Cost recorded for edict %s: %d tokens, ¥%.4f",
            edict_id,
            tracker.total_tokens,
            tracker.cost_cny,
        )
