"""Serialized production reconciliation for durable Evolution rollbacks."""

from __future__ import annotations

import logging
from threading import Lock

from tianshu.evolution.promotion import PromotionConflict, PromotionService

logger = logging.getLogger(__name__)


class EvolutionRollbackReconciler:
    """Drive only the existing PromotionService rollback authority."""

    def __init__(self, promotion_service: PromotionService, *, limit: int = 50) -> None:
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self._promotion_service = promotion_service
        self._limit = limit
        self._lock = Lock()
        self._last_error_code: str | None = None

    @property
    def last_error_code(self) -> str | None:
        return self._last_error_code

    def reconcile_once(self) -> int:
        with self._lock:
            try:
                completed = self._promotion_service.reconcile_pending_rollbacks(limit=self._limit)
            except PromotionConflict as exc:
                self._last_error_code = str(exc)
                logger.warning("Evolution rollback reconciliation deferred: %s", exc)
                return 0
            errors = self._promotion_service.reconciliation_error_codes
            self._last_error_code = errors[0] if errors else None
            if errors:
                logger.warning(
                    "Evolution rollback candidates remain pending: %s",
                    ",".join(errors),
                )
            return completed

    def readiness_probe(self) -> bool:
        return not self._promotion_service.has_pending_rollbacks()


__all__ = ["EvolutionRollbackReconciler"]
