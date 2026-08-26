"""Compatibility exports for the application-owned runtime router."""

from tianshu.application.runtime_router import (
    ChallengerRouter,
    EvolutionRuntimeUnavailable,
    FrozenContentViewUnavailable,
    GenerationBindingUnavailable,
    GenerationRetired,
    RunAssignmentUnavailable,
    SystemSnapshotUnavailable,
    allocation_bucket,
    selects_challenger,
)

__all__ = [
    "ChallengerRouter",
    "EvolutionRuntimeUnavailable",
    "FrozenContentViewUnavailable",
    "GenerationBindingUnavailable",
    "GenerationRetired",
    "RunAssignmentUnavailable",
    "SystemSnapshotUnavailable",
    "allocation_bucket",
    "selects_challenger",
]
