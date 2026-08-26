"""Layer-neutral contracts shared by executor-generation orchestration."""

from __future__ import annotations

from dataclasses import dataclass

PI_GENERATION_SCOPE = "executor:keqing:pi"


class ExecutorGenerationError(RuntimeError):
    """Base error for generation-pinned executor selection."""


class ExecutorGenerationConflict(ExecutorGenerationError):
    """An attempt tried to change an already reserved generation selection."""


class ExecutorGenerationUnavailable(ExecutorGenerationError):
    """A pinned generation cannot be used without falling back to live state."""


@dataclass(frozen=True, slots=True)
class GenerationRecoveryReport:
    materialized_generation_ids: tuple[str, ...]
    failed_generation_ids: tuple[str, ...]


__all__ = [
    "ExecutorGenerationConflict",
    "ExecutorGenerationError",
    "ExecutorGenerationUnavailable",
    "GenerationRecoveryReport",
    "PI_GENERATION_SCOPE",
]
