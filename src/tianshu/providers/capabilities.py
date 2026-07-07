"""Provider capability model and task requirements."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ProviderCapability(str, Enum):
    CHAT = "chat"
    FUNCTION_CALLING = "function_calling"
    VISION = "vision"
    LONG_CONTEXT = "long_context"
    STREAMING = "streaming"


class ProviderInfo(BaseModel):
    name: str
    model: str
    api_base: str | None = None
    capabilities: list[ProviderCapability] = Field(default_factory=list)
    status: Literal["active", "degraded", "disabled"] = "active"
    priority: int = 100
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    cost_per_1k_prompt: float | None = None
    cost_per_1k_completion: float | None = None
    cost_per_1k_cache_read: float | None = None


class TaskRequirements(BaseModel):
    """Requirements for selecting a provider."""

    capabilities: list[ProviderCapability] = Field(default_factory=list)
    strategy: Literal["cheapest", "fastest", "priority", "round_robin"] = "priority"
    max_cost_per_1k: float | None = None
    min_context_window: int | None = None
