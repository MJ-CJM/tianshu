"""In-memory per-execution cost accumulator + model pricing."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Default pricing per 1K tokens (CNY, ≈ USD × 7.2)
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.018, 0.072),
    "gpt-4o-mini": (0.0011, 0.0043),
    "gpt-4-turbo": (0.072, 0.216),
    "gpt-3.5-turbo": (0.0036, 0.0108),
    "claude-3-opus": (0.108, 0.54),
    "claude-3-sonnet": (0.0216, 0.108),
    "claude-3-haiku": (0.0018, 0.009),
    "claude-sonnet-4-6": (0.0216, 0.108),
    "claude-opus-4-6": (0.108, 0.54),
    "claude-haiku-4-5": (0.0072, 0.036),
    # 2026-04-27: 国产常用模型
    "deepseek-chat": (0.001, 0.008),
    "deepseek-reasoner": (0.004, 0.016),
    "qwen-max": (0.04, 0.12),
    "qwen-plus": (0.004, 0.012),
    "moonshot-v1-8k": (0.012, 0.012),
}


class CostTracker:
    """Tracks costs for a single execution and computes CNY amounts."""

    def __init__(self) -> None:
        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0
        self._total_tokens: int = 0
        self._cost_cny: float = 0.0

    @property
    def prompt_tokens(self) -> int:
        return self._prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self._completion_tokens

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def cost_cny(self) -> float:
        return self._cost_cny

    def accumulate(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        provider_pricing: tuple[float, float] | None = None,
    ) -> float:
        """Add token usage and return incremental cost in CNY."""
        cost = estimate_cost(model, prompt_tokens, completion_tokens, provider_pricing)
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        self._total_tokens += prompt_tokens + completion_tokens
        self._cost_cny += cost
        return cost

    @staticmethod
    def _lookup_pricing(model: str) -> tuple[float, float]:
        """Look up pricing for a model, returns (prompt_per_1k, completion_per_1k)."""
        # Try exact match first
        if model in _DEFAULT_PRICING:
            return _DEFAULT_PRICING[model]
        # Try prefix match (strip openai/ or similar)
        base = model.split("/")[-1] if "/" in model else model
        if base in _DEFAULT_PRICING:
            return _DEFAULT_PRICING[base]
        # Default fallback
        return (0.0072, 0.0144)

    def reset(self) -> None:
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._cost_cny = 0.0


def lookup_pricing(model: str) -> tuple[float, float]:
    """Module-level alias for CostTracker._lookup_pricing。"""
    return CostTracker._lookup_pricing(model)


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    provider_pricing: tuple[float, float] | None = None,
) -> float:
    """无状态成本估算（CNY）。"""
    pricing = provider_pricing or lookup_pricing(model)
    return (prompt_tokens / 1000.0) * pricing[0] + (completion_tokens / 1000.0) * pricing[1]
