"""多维度计费集成测试 — 验证 provider 自定义价 + cache 折扣端到端流转。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tianshu.config_manager import ConfigManager, LLMConfigState
from tianshu.providers.manager import ProviderManager
from tianshu.providers.capabilities import ProviderInfo
from tianshu.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    if hasattr(s, "init_db"):
        s.init_db()
    return s


@pytest.fixture
def provider_manager(storage):
    initial = LLMConfigState(name="default", model="deepseek-chat", api_key="x")
    cm = ConfigManager(initial, storage=storage)
    return ProviderManager(storage, cm)


class _FakeUsage:
    """模拟不同 provider 的 usage 对象。"""

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_llmclient_uses_custom_pricing_with_cache_hit(provider_manager):
    """LLMClient 用自定义价 + cache 折扣。"""
    pm = provider_manager
    # 注册 provider + 自定义 3 维价
    pm.register(ProviderInfo(
        name="my-deepseek",
        model="deepseek-v4-flash",
        cost_per_1k_prompt=0.001,
        cost_per_1k_cache_read=0.00002,
        cost_per_1k_completion=0.002,
    ))

    # 通过 ProviderManager.get_client 拿 LLMClient（应注入 pricing_override + provider_name）
    # 这里直接构造方便 mock；底层路径已在 ProviderManager 单测覆盖
    from tianshu.llm import LLMClient
    pricing = pm.get_effective_pricing("my-deepseek")
    client = LLMClient(
        model="deepseek-v4-flash",
        api_key="x",
        provider_name="my-deepseek",
        pricing_override=pricing,
    )

    # mock litellm 返回带 deepseek cache hit 的 usage
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(
        message=MagicMock(content="hi", tool_calls=None),
        finish_reason="stop",
    )]
    fake_response.usage = _FakeUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        prompt_cache_hit_tokens=600,
    )

    with patch("tianshu.llm.litellm.acompletion", return_value=fake_response):
        resp = await client.chat(messages=[{"role": "user", "content": "x"}])

    # 验证 1：cache_read_tokens 落到 UsageSummary
    assert resp.usage.cache_read_tokens == 600

    # 验证 2：cost 反映自定义价 + cache 折扣
    # (1000-600)/1000×0.001 + 600/1000×0.00002 + 500/1000×0.002
    # = 0.0004 + 0.000012 + 0.001 = 0.001412
    assert abs(resp.usage.cost_cny - 0.001412) < 1e-6

    # 验证 3：provider_name 透传
    assert client.provider_name == "my-deepseek"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_anthropic_cache_field_extraction(provider_manager):
    """anthropic claude 模型读 cache_read_input_tokens 字段。"""
    from tianshu.llm import LLMClient

    client = LLMClient(model="claude-sonnet-4-6", api_key="x")
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(
        message=MagicMock(content="hi", tool_calls=None),
        finish_reason="stop",
    )]
    fake_response.usage = _FakeUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        cache_read_input_tokens=800,
    )

    with patch("tianshu.llm.litellm.acompletion", return_value=fake_response):
        resp = await client.chat(messages=[{"role": "user", "content": "x"}])

    assert resp.usage.cache_read_tokens == 800
    # claude-sonnet-4-6 默认价 (0.0216, 0.00216, 0.108)
    # (1000-800)/1000×0.0216 + 800/1000×0.00216 + 500/1000×0.108
    # = 0.00432 + 0.001728 + 0.054 = 0.060048
    assert abs(resp.usage.cost_cny - 0.060048) < 1e-6


@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_cache_field_extraction(provider_manager):
    """openai 模型读 prompt_tokens_details.cached_tokens 嵌套字段。"""
    from tianshu.llm import LLMClient

    client = LLMClient(model="gpt-4o", api_key="x")
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(
        message=MagicMock(content="hi", tool_calls=None),
        finish_reason="stop",
    )]
    fake_response.usage = _FakeUsage(
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        prompt_tokens_details=_FakeUsage(cached_tokens=300),
    )

    with patch("tianshu.llm.litellm.acompletion", return_value=fake_response):
        resp = await client.chat(messages=[{"role": "user", "content": "x"}])

    assert resp.usage.cache_read_tokens == 300


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_cache_field_falls_back_to_zero(provider_manager):
    """未识别 model 或缺 cache 字段 → 退化为 cache_read=0。"""
    from tianshu.llm import LLMClient

    client = LLMClient(model="unknown-model", api_key="x")
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(
        message=MagicMock(content="hi", tool_calls=None),
        finish_reason="stop",
    )]
    fake_response.usage = _FakeUsage(
        prompt_tokens=1000, completion_tokens=500, total_tokens=1500,
    )

    with patch("tianshu.llm.litellm.acompletion", return_value=fake_response):
        resp = await client.chat(messages=[{"role": "user", "content": "x"}])

    assert resp.usage.cache_read_tokens == 0
    # fallback (0.0072, 0.0072, 0.0144)
    # 1000/1000×0.0072 + 500/1000×0.0144 = 0.0072 + 0.0072 = 0.0144
    assert abs(resp.usage.cost_cny - 0.0144) < 1e-6


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cost_manager_tracker_uses_provider_name(storage):
    """CostManager._finalize_cost 用 tracker.last_provider_name 而非硬编码 default。"""
    from tianshu.cost.manager import CostManager
    from tianshu.models.common import UsageSummary
    from tianshu.models.events import EventEnvelope

    cm = CostManager(storage)
    # 模拟 LLM_OUTPUT hook 调用
    edict = MagicMock()
    edict.id = "e1"
    usage = UsageSummary(prompt_tokens=1000, completion_tokens=500, total_tokens=1500, cache_read_tokens=600, cost_cny=0.001412)
    config_state = MagicMock()
    config_state.model = "deepseek-v4-flash"
    await cm.on_llm_output(
        edict=edict, usage=usage, config_state=config_state,
        provider_name="my-deepseek",
    )
    # finalize（模拟 execution.completed）
    event = EventEnvelope(
        event_type="execution.completed",
        edict_id="e1",
        memorial_id="m1",
        producer="test",
    )
    await cm.handle_execution_completed(event)
    # 查 cost_ledger — 关键断言是 provider_name 而非硬编码 "default"
    records, total = storage.list_cost_records(edict_id="e1")
    assert total == 1
    rec = records[0]
    assert rec["provider_name"] == "my-deepseek", f"expected 'my-deepseek', got {rec['provider_name']}"
    # 注：cache_read_tokens 当前不入 cost_ledger 表（schema 未加列）。
    # 跨 edict 聚合不需要这一维度；单 edict 内的 cache 数据看 outer_loop_iterations 表。
