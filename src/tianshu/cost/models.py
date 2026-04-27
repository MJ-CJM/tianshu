"""Cost data models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from ulid import ULID


class CostRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    edict_id: str
    memorial_id: str | None = None
    provider_name: str = "default"
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cost_cny: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CostSummary(BaseModel):
    total_records: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_cny: float = 0.0


class BudgetStatus(BaseModel):
    scope: str
    budget_cny: float
    spent_cny: float = 0.0
    remaining_cny: float = 0.0
    period: Literal["daily", "weekly", "monthly"] = "monthly"
    exceeded: bool = False
