"""LLM cost 估算 + UsageSummary.cost_cny 测试。"""

from __future__ import annotations

import pytest

from tianshu.cost.tracker import CostTracker, estimate_cost, lookup_pricing
from tianshu.models.common import UsageSummary


@pytest.mark.unit
def test_lookup_pricing_known_model():
    assert lookup_pricing("deepseek-chat") == (0.001, 0.008)


@pytest.mark.unit
def test_lookup_pricing_unknown_falls_back():
    p = lookup_pricing("totally-unknown-model-xyz")
    assert p == (0.0072, 0.0144)


@pytest.mark.unit
def test_lookup_pricing_strips_provider_prefix():
    """openai/gpt-4o 应剥离 prefix 找到 gpt-4o。"""
    assert lookup_pricing("openai/gpt-4o") == (0.018, 0.072)


@pytest.mark.unit
def test_estimate_cost_basic():
    cost = estimate_cost("deepseek-chat", 1000, 500)
    # 1.0 × 0.001 + 0.5 × 0.008 = 0.001 + 0.004 = 0.005
    assert abs(cost - 0.005) < 1e-6


@pytest.mark.unit
def test_estimate_cost_zero_tokens():
    assert estimate_cost("deepseek-chat", 0, 0) == 0.0


@pytest.mark.unit
def test_estimate_cost_provider_pricing_override():
    cost = estimate_cost("ignored", 1000, 500, provider_pricing=(0.01, 0.02))
    # 1.0 × 0.01 + 0.5 × 0.02 = 0.01 + 0.01 = 0.02
    assert abs(cost - 0.02) < 1e-6


@pytest.mark.unit
def test_cost_tracker_accumulate_uses_estimate_cost():
    """重构后 accumulate 行为应与之前一致。"""
    t = CostTracker()
    incr = t.accumulate("deepseek-chat", 1000, 500)
    assert abs(incr - 0.005) < 1e-6
    assert abs(t.cost_cny - 0.005) < 1e-6
    assert t.total_tokens == 1500


@pytest.mark.unit
def test_cost_tracker_accumulate_multiple():
    """多次 accumulate 累加。"""
    t = CostTracker()
    t.accumulate("deepseek-chat", 1000, 500)
    t.accumulate("deepseek-chat", 2000, 1000)
    # 第二次：2.0 × 0.001 + 1.0 × 0.008 = 0.002 + 0.008 = 0.010
    # 总：0.005 + 0.010 = 0.015
    assert abs(t.cost_cny - 0.015) < 1e-6
    assert t.total_tokens == 4500


@pytest.mark.unit
def test_usage_summary_default_cost_cny():
    u = UsageSummary()
    assert u.cost_cny == 0.0


@pytest.mark.unit
def test_usage_summary_old_json_no_cost_field_compat():
    """老 JSON 缺 cost_cny 也能反序列化。"""
    u = UsageSummary.model_validate({
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    })
    assert u.cost_cny == 0.0


@pytest.mark.unit
def test_usage_summary_round_trip():
    u = UsageSummary(prompt_tokens=100, completion_tokens=50, total_tokens=150, cost_cny=0.123)
    data = u.model_dump_json()
    u2 = UsageSummary.model_validate_json(data)
    assert u2.cost_cny == 0.123
    assert u2.total_tokens == 150
