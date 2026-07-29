"""In-memory per-execution cost accumulator + model pricing.

Pricing 3 维语义：(input_miss, input_hit, output) 单位 CNY/1K tokens。
- input_miss = 输入未命中缓存价（普通输入）
- input_hit  = 输入命中缓存价（折扣价）
- output     = 输出价

默认价来源是 models.dev 目录快照（providers/model_catalog.py，打包随发行、
可手动刷新），替代原先手工维护的 _DEFAULT_PRICING 硬编码字典；目录与
litellm.model_cost 都未命中时落保守兜底价。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# 模型未命中目录时的兜底（保守估计；hit = miss）
_FALLBACK_PRICING: tuple[float, float, float] = (0.0072, 0.0072, 0.0144)

# 定价解析器：模型串 → 3 维价 | None。wiring 注入带磁盘缓存与配置汇率的
# 目录实例；未装配时懒建打包快照默认目录（单测/脚本/doctor 同样拿到目录价）。
_pricing_resolver: Callable[[str], tuple[float, float, float] | None] | None = None


def set_pricing_resolver(
    resolver: Callable[[str], tuple[float, float, float] | None] | None,
) -> None:
    global _pricing_resolver
    _pricing_resolver = resolver


def lookup_pricing(model: str) -> tuple[float, float, float]:
    """按模型串查 3 维价（CNY/1K）；目录未命中落兜底价。"""
    resolver = _pricing_resolver
    if resolver is None:
        from tianshu.providers.model_catalog import default_catalog

        resolver = default_catalog().pricing_cny_by_model
    try:
        pricing = resolver(model)
    except Exception:  # noqa: BLE001 - 定价查询失败等同未知，落兜底
        logger.exception("pricing resolver failed for model %r", model)
        pricing = None
    return pricing if pricing is not None else _FALLBACK_PRICING


class CostTracker:
    """Tracks costs for a single execution and computes CNY amounts."""

    def __init__(self) -> None:
        self._prompt_tokens: int = 0
        self._completion_tokens: int = 0
        self._cache_read_tokens: int = 0
        self._total_tokens: int = 0
        self._cost_cny: float = 0.0
        self._last_provider_name: str | None = None
        self._last_model: str | None = None

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

    @property
    def last_model(self) -> str | None:
        return self._last_model

    def accumulate(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cache_read_tokens: int = 0,
        provider_pricing: tuple[float, float] | tuple[float, float, float] | None = None,
        provider_name: str | None = None,
        cost_cny: float | None = None,
    ) -> float:
        """Add token usage and return incremental cost in CNY.

        cost_cny 给定时直接采信（LLM 客户端已按供应商生效价——含订阅归零——
        算好的权威值），不再按模型名重新估价；None 时才落估价路径（旧调用兼容）。
        """
        cost = (
            cost_cny
            if cost_cny is not None
            else estimate_cost(
                model,
                prompt_tokens,
                completion_tokens,
                cache_read_tokens=cache_read_tokens,
                provider_pricing=provider_pricing,
            )
        )
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        self._cache_read_tokens += cache_read_tokens
        self._total_tokens += prompt_tokens + completion_tokens
        self._cost_cny += cost
        if provider_name:
            self._last_provider_name = provider_name
        if model:
            self._last_model = model
        return cost

    def reset(self) -> None:
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._cache_read_tokens = 0
        self._total_tokens = 0
        self._cost_cny = 0.0
        self._last_provider_name = None


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
