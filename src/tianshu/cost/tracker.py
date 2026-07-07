"""In-memory per-execution cost accumulator + model pricing.

Pricing 表 3 维语义：(input_miss, input_hit, output) 单位 CNY/1K tokens。
- input_miss = 输入未命中缓存价（普通输入）
- input_hit  = 输入命中缓存价（折扣价）
- output     = 输出价

如果某模型只有 2 维数据，input_hit 默认与 input_miss 相同（无折扣）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Default pricing per 1K tokens (CNY, ≈ USD × 7.2)
# Tuple = (input_miss, input_hit, output)
# cache hit 折扣比例参考各家公开数据：deepseek 1/50、claude 1/10、gpt 1/2
_DEFAULT_PRICING: dict[str, tuple[float, float, float]] = {
    # OpenAI
    "gpt-4o": (0.018, 0.009, 0.072),  # cache hit 1/2
    "gpt-4o-mini": (0.0011, 0.00055, 0.0043),
    "gpt-4-turbo": (0.072, 0.036, 0.216),
    "gpt-3.5-turbo": (0.0036, 0.0018, 0.0108),
    # Anthropic Claude（cache hit 1/10，cache write ≈ 1.25× miss，本期未单独建模）
    "claude-3-opus": (0.108, 0.0108, 0.54),
    "claude-3-sonnet": (0.0216, 0.00216, 0.108),
    "claude-3-haiku": (0.0018, 0.00018, 0.009),
    "claude-sonnet-4-6": (0.0216, 0.00216, 0.108),
    "claude-opus-4-6": (0.108, 0.0108, 0.54),
    "claude-haiku-4-5": (0.0072, 0.00072, 0.036),
    # DeepSeek（cache hit ≈ 1/50；v3 系列；v4-flash/pro 待官方价新鲜度更新）
    "deepseek-chat": (0.001, 0.00002, 0.002),  # 注：旧 output 价 0.008 已纠正为 v4 实际 0.002
    "deepseek-reasoner": (0.004, 0.00008, 0.016),
    "deepseek-v4-flash": (0.001, 0.00002, 0.002),  # 官方原价
    "deepseek-v4-pro": (
        0.012,
        0.0001,
        0.024,
    ),  # 官方原价 (12/0.1/24 元每百万)；限时 2.5 折期间可在户部账房手动覆盖为 (0.003, 0.000025, 0.006)
    # 国产其他（cache 比例未公开则 hit=miss 无折扣）
    "qwen-max": (0.04, 0.04, 0.12),
    "qwen-plus": (0.004, 0.004, 0.012),
    "moonshot-v1-8k": (0.012, 0.012, 0.012),
    # MiniMax（无公开 cache 价，hit=miss）
    "MiniMax-M2.7": (0.001, 0.001, 0.008),
}

# 模型未命中价表时的兜底（保守估计；hit = miss）
_FALLBACK_PRICING: tuple[float, float, float] = (0.0072, 0.0072, 0.0144)


class CostTracker:
    """Tracks costs for a single execution and computes CNY amounts."""

    def __init__(self) -> None:
        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0
        self._cache_read_tokens: int = 0
        self._total_tokens: int = 0
        self._cost_cny: float = 0.0
        self._last_provider_name: str | None = None

    @property
    def prompt_tokens(self) -> int:
        return self._prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self._completion_tokens

    @property
    def cache_read_tokens(self) -> int:
        return self._cache_read_tokens

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def cost_cny(self) -> float:
        return self._cost_cny

    @property
    def last_provider_name(self) -> str | None:
        return self._last_provider_name

    def accumulate(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        provider_pricing: tuple[float, float] | tuple[float, float, float] | None = None,
        provider_name: str | None = None,
    ) -> float:
        """Add token usage and return incremental cost in CNY."""
        cost = estimate_cost(
            model,
            prompt_tokens,
            completion_tokens,
            cache_read_tokens=cache_read_tokens,
            provider_pricing=provider_pricing,
        )
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        self._cache_read_tokens += cache_read_tokens
        self._total_tokens += prompt_tokens + completion_tokens
        self._cost_cny += cost
        if provider_name:
            self._last_provider_name = provider_name
        return cost

    @staticmethod
    def _lookup_pricing(model: str) -> tuple[float, float, float]:
        """Look up pricing for a model, returns (input_miss, input_hit, output)."""
        if model in _DEFAULT_PRICING:
            return _DEFAULT_PRICING[model]
        # Try prefix match (strip openai/ or similar)
        base = model.split("/")[-1] if "/" in model else model
        if base in _DEFAULT_PRICING:
            return _DEFAULT_PRICING[base]
        return _FALLBACK_PRICING

    def reset(self) -> None:
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._cache_read_tokens = 0
        self._total_tokens = 0
        self._cost_cny = 0.0
        self._last_provider_name = None


def lookup_pricing(model: str) -> tuple[float, float, float]:
    """Module-level alias for CostTracker._lookup_pricing。返回 3-tuple。"""
    return CostTracker._lookup_pricing(model)


def _normalize_pricing(
    pricing: tuple[float, float] | tuple[float, float, float] | None,
    fallback: tuple[float, float, float],
) -> tuple[float, float, float]:
    """把 pricing 规整为 3-tuple；2-tuple 入参时 hit 默认 = miss（无折扣兼容）。"""
    if pricing is None:
        return fallback
    if len(pricing) == 2:
        miss, out = pricing
        return (miss, miss, out)
    return pricing  # type: ignore[return-value]


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    provider_pricing: tuple[float, float] | tuple[float, float, float] | None = None,
) -> float:
    """无状态成本估算（CNY）。

    成本公式：
        input_miss = max(0, prompt_tokens - cache_read_tokens)
        cost = input_miss/1000 * miss_price
             + cache_read_tokens/1000 * hit_price
             + completion_tokens/1000 * out_price

    向后兼容：
    - cache_read_tokens 默认 0 → 退化到 prompt × miss + completion × out
    - provider_pricing 接受 2-tuple（旧调用），自动 hit=miss 兼容
    """
    pricing = _normalize_pricing(provider_pricing, lookup_pricing(model))
    miss_price, hit_price, out_price = pricing
    input_miss = max(0, prompt_tokens - cache_read_tokens)
    return (
        (input_miss / 1000.0) * miss_price
        + (cache_read_tokens / 1000.0) * hit_price
        + (completion_tokens / 1000.0) * out_price
    )
