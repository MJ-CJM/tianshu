"""PerformanceEvaluator — aggregate persona metrics from memorials + cost."""

from __future__ import annotations

import logging

from tianshu.persona.metrics import PersonaMetrics
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class PerformanceEvaluator:
    """Evaluates persona performance from historical execution data."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def evaluate(self, persona_id: str) -> PersonaMetrics:
        """Compute aggregate metrics for a persona."""
        stats = self._storage.get_persona_stats(persona_id)
        return PersonaMetrics(
            persona_id=persona_id,
            total_executions=stats.get("total_executions", 0),
            completed=stats.get("completed", 0),
            failed=stats.get("failed", 0),
            cancelled=stats.get("cancelled", 0),
            success_rate=stats.get("success_rate", 0.0),
            total_tokens=stats.get("total_tokens", 0),
            avg_tokens_per_execution=stats.get("avg_tokens_per_execution", 0.0),
            total_cost_cny=stats.get("total_cost_cny", 0.0),
            avg_duration_seconds=stats.get("avg_duration_seconds", 0.0),
        )
