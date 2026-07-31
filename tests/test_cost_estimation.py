"""LLM cost 估算 + UsageSummary 测试（3 维定价）。

3 维定价：(input_miss, input_hit, output) — 单位 CNY/1K tokens。
"""

from __future__ import annotations

import pytest

from tianshu.cost.tracker import CostTracker, estimate_cost, lookup_pricing
from tianshu.models.common import UsageSummary


@pytest.mark.unit
def test_lookup_pricing_known_model():
    """已知模型命中 models.dev 目录价（非兜底），且 hit 有缓存折扣。"""
    p = lookup_pricing("deepseek-chat")
    assert len(p) == 3
    assert p != (0.0072, 0.0072, 0.0144)  # 不是兜底价
    miss, hit, out = p
    assert 0 < hit < miss < out  # deepseek cache hit 深折扣、输出贵于输入


@pytest.mark.unit
def test_lookup_pricing_unknown_falls_back():
    """未知模型返回兜底 3-tuple（hit = miss）。"""
    p = lookup_pricing("totally-unknown-model-xyz")
    assert p == (0.0072, 0.0072, 0.0144)


@pytest.mark.unit
def test_lookup_pricing_strips_provider_prefix():
    """openai/gpt-4o 应剥离 prefix 命中目录里的 gpt-4o。"""
    assert lookup_pricing("openai/gpt-4o") == lookup_pricing("gpt-4o")
    assert lookup_pricing("gpt-4o") != (0.0072, 0.0072, 0.0144)


@pytest.mark.unit
def test_estimate_cost_basic_no_cache():
    """cache_read=0 退化到 prompt × miss + completion × out。"""
    miss, _hit, out = lookup_pricing("deepseek-chat")
    cost = estimate_cost("deepseek-chat", 1000, 500)
    assert abs(cost - (1.0 * miss + 0.5 * out)) < 1e-9


@pytest.mark.unit
def test_estimate_cost_zero_tokens():
    assert estimate_cost("deepseek-chat", 0, 0) == 0.0


@pytest.mark.unit
def test_estimate_cost_full_cache_hit():
    """全部命中：input_miss = 0，所有 prompt 走 hit 价。"""
    _miss, hit, out = lookup_pricing("deepseek-chat")
    cost = estimate_cost("deepseek-chat", 1000, 500, cache_read_tokens=1000)
    assert abs(cost - (1.0 * hit + 0.5 * out)) < 1e-9


@pytest.mark.unit
def test_estimate_cost_partial_cache_hit():
    """部分命中。"""
    miss, hit, out = lookup_pricing("deepseek-chat")
    cost = estimate_cost("deepseek-chat", 1000, 500, cache_read_tokens=600)
    assert abs(cost - (0.4 * miss + 0.6 * hit + 0.5 * out)) < 1e-9


@pytest.mark.unit
def test_estimate_cost_cache_exceeds_prompt_clamped_to_zero():
    """异常：cache_read > prompt → input_miss 不为负。"""
    _miss, hit, out = lookup_pricing("deepseek-chat")
    cost = estimate_cost("deepseek-chat", 100, 50, cache_read_tokens=200)
    assert abs(cost - (0.2 * hit + 0.05 * out)) < 1e-9


@pytest.mark.unit
def test_estimate_cost_2tuple_compat():
    """旧 2-tuple 入参兼容：hit 价默认 = miss（无折扣）。"""
    cost = estimate_cost(
        "any",
        1000,
        500,
        cache_read_tokens=500,
        provider_pricing=(0.01, 0.02),
    )
    # miss = 0.01, hit = miss = 0.01, out = 0.02
    # (1000-500)/1000×0.01 + 500/1000×0.01 + 500/1000×0.02 = 0.005 + 0.005 + 0.01 = 0.02
    assert abs(cost - 0.02) < 1e-6


@pytest.mark.unit
def test_estimate_cost_3tuple_provider_override():
    """3-tuple provider_pricing 完全覆盖默认表。"""
    cost = estimate_cost(
        "ignored",
        1000,
        500,
        cache_read_tokens=600,
        provider_pricing=(0.01, 0.0001, 0.02),
    )
    # 400/1000×0.01 + 600/1000×0.0001 + 500/1000×0.02 = 0.004 + 0.00006 + 0.01 = 0.01406
    assert abs(cost - 0.01406) < 1e-6


@pytest.mark.unit
def test_cost_tracker_accumulate_with_cache():
    """accumulate 接受 cache_read_tokens + provider_name。"""
    t = CostTracker()
    incr = t.accumulate(
        "deepseek-chat",
        1000,
        500,
        cache_read_tokens=600,
        provider_name="test-pn",
    )
    miss, hit, out = lookup_pricing("deepseek-chat")
    expected = 0.4 * miss + 0.6 * hit + 0.5 * out
    assert abs(incr - expected) < 1e-9
    assert abs(t.cost_cny - expected) < 1e-9
    assert t.cache_read_tokens == 600
    assert t.last_provider_name == "test-pn"
    assert t.total_tokens == 1500


@pytest.mark.unit
def test_cost_tracker_accumulate_multiple_provider_name():
    """多次累加，last_provider_name 取最后一次。"""
    t = CostTracker()
    t.accumulate("deepseek-chat", 1000, 500, provider_name="p1")
    t.accumulate("deepseek-chat", 2000, 1000, provider_name="p2")
    assert t.last_provider_name == "p2"
    assert t.total_tokens == 4500


@pytest.mark.unit
def test_cost_tracker_uses_truthful_labels_for_mixed_calls():
    t = CostTracker()
    t.accumulate("model-a", 10, 5, provider_name="provider-a")
    t.accumulate("model-b", 20, 10, provider_name="provider-b")

    assert t.provider_label == "multiple"
    assert t.model_label == "multiple"


@pytest.mark.unit
def test_cost_tracker_reset_clears_provider_name():
    t = CostTracker()
    t.accumulate("deepseek-chat", 100, 50, provider_name="p1")
    t.reset()
    assert t.last_provider_name is None
    assert t.last_model is None
    assert t.provider_label is None
    assert t.model_label is None
    assert t.cache_read_tokens == 0


@pytest.mark.unit
def test_usage_summary_default_fields():
    u = UsageSummary()
    assert u.cost_cny == 0.0
    assert u.cache_read_tokens == 0


@pytest.mark.unit
def test_usage_summary_old_json_no_cache_field_compat():
    """老 JSON 缺 cache_read_tokens 也能反序列化。"""
    u = UsageSummary.model_validate(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
    )
    assert u.cost_cny == 0.0
    assert u.cache_read_tokens == 0


@pytest.mark.unit
def test_usage_summary_round_trip_with_cache():
    u = UsageSummary(
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cache_read_tokens=80,
        cost_cny=0.123,
    )
    data = u.model_dump_json()
    u2 = UsageSummary.model_validate_json(data)
    assert u2.cache_read_tokens == 80
    assert u2.cost_cny == 0.123
