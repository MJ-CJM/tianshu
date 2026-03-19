"""PersonaMetrics — performance metrics model."""

from __future__ import annotations

from pydantic import BaseModel


class PersonaMetrics(BaseModel):
    persona_id: str
    total_executions: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    success_rate: float = 0.0
    total_tokens: int = 0
    avg_tokens_per_execution: float = 0.0
    total_cost_cny: float = 0.0
    avg_duration_seconds: float = 0.0
