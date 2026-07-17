"""Truthful pre-S5 Evolution Center read service."""

from __future__ import annotations

from tianshu.models.evolution_view import EvolutionCenterSnapshotV1
from tianshu.models.principal import AuthContext

EVOLUTION_NOT_ENABLED_REASON_CODE = "s5_governed_evolution_not_enabled"


class EvolutionCenterUnavailable(RuntimeError):
    """The authoritative Evolution Center source could not be read."""


class EvolutionCenterQueryService:
    """Return the only truthful pre-S5 state without inventing repositories or data."""

    def get_snapshot(self, auth: AuthContext) -> EvolutionCenterSnapshotV1:
        del auth
        return EvolutionCenterSnapshotV1(
            status="not_enabled",
            reason_code=EVOLUTION_NOT_ENABLED_REASON_CODE,
            candidates=(),
            routing=(),
            last_gate_hash=None,
        )


__all__ = [
    "EVOLUTION_NOT_ENABLED_REASON_CODE",
    "EvolutionCenterQueryService",
    "EvolutionCenterUnavailable",
]
