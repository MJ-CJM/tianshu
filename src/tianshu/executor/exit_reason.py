"""Agent loop exit reasons — each maps to a distinct post-exit handling strategy."""

from __future__ import annotations

from enum import StrEnum


class ExitReason(StrEnum):
    """Why the agent loop terminated."""

    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"
    CONTEXT_OVERFLOW = "context_overflow"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    HOOK_BLOCKED = "hook_blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LLM_ERROR = "llm_error"
    OUTPUT_TRUNCATED = "output_truncated"
