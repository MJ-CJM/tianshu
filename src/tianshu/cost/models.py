"""Cost data models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator
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
    total_cache_read_tokens: int = 0
    total_tokens: int = 0
    total_cost_cny: float = 0.0


class BudgetStatus(BaseModel):
    scope: str
    budget_cny: float
    spent_cny: float = 0.0
    remaining_cny: float = 0.0
    period: Literal["daily", "weekly", "monthly"] = "monthly"
    exceeded: bool = False
    reset_at: datetime | None = None
    period_start: datetime | None = None


class CostBudgetUpdate(BaseModel):
    scope: str = "global"
    budget_cny: float = Field(gt=0, allow_inf_nan=False)
    period: Literal["daily", "weekly", "monthly"] = "monthly"
    reset_at: datetime | None = None

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        scope = value.strip()
        if scope == "global":
            return scope
        prefix, separator, subject = scope.partition(":")
        if separator and prefix in {"edict", "submitter"} and subject.strip():
            return f"{prefix}:{subject.strip()}"
        raise ValueError("scope must be global, edict:<id>, or submitter:<id>")

    @field_validator("reset_at")
    @classmethod
    def validate_reset_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reset_at must include a timezone")
        return value.astimezone(UTC)
