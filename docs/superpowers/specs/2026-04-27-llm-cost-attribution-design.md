# LLM 成本接入 UsageSummary 设计

**Date**: 2026-04-27
**Status**: Spec — pending implementation plan
**Related**: `docs/superpowers/specs/2026-04-26-long-task-iteration-design.md`（长任务 outer loop，§6.1 提到 cost 上限兜底）

## 1. 背景

长任务 outer loop（commits `23c307b` → `f2521a5`）已落地，但 e2e 实测发现一个洞：

- `LLMClient.chat()` 返回的 `UsageSummary` 只有 `prompt_tokens` / `completion_tokens` / `total_tokens`，**没有 cost_cny 字段**
- orchestrator 用 `getattr(usage, "cost_cny", 0.0)` 兜底取值，永远拿到 `0.0`
- 结果：`Edict.runtime.cost_budget_cny` 上限永远不触发 EXHAUSTED，长任务可能跑爆预算无人察觉

项目其实**已有完整的 pricing 表 + 计算函数**（`src/tianshu/cost/tracker.py:67` 的 `_lookup_pricing`），只是没接到 `LLMClient.chat()` 的返回值上。

本设计补这个洞：每次 LLM 调用结束时，根据 model + tokens 即时算出成本，写进 `UsageSummary.cost_cny`，让 outer loop 的预算上限真正生效。

## 2. 决策

| 维度 | 决策 |
|------|------|
| 计算时机 | LLM 调用完成时即算，写进单次 `UsageSummary`（不延迟到 cost_manager） |
| 价格来源 | 复用 `cost/tracker.py:_DEFAULT_PRICING` 表（已有），新增 deepseek 条目 |
| API 接入点 | `cost/tracker.py` 加 module-level `estimate_cost(model, p, c) -> float`；`LLMClient.chat()` 调它，**不依赖 CostTracker 实例** |
| 向后兼容 | `UsageSummary.cost_cny` 默认 0.0，老代码读 `usage.cost_cny` 不会爆；新增字段不破现有事件序列化 |
| 适用范围 | 所有 `LLMClient.chat()` / `chat_stream()` 调用统一 —— actor / critic / consultation 都受益 |

## 3. 改动点

### 3.1 `cost/tracker.py`：抽出 module-level helper

```python
# 现有：CostTracker._lookup_pricing(model)（staticmethod）
# 新增：抽到 module-level，便于直接调用，无需实例化 CostTracker

def lookup_pricing(model: str) -> tuple[float, float]:
    """Module-level alias for CostTracker._lookup_pricing。"""
    return CostTracker._lookup_pricing(model)


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    provider_pricing: tuple[float, float] | None = None,
) -> float:
    """无状态成本估算（CNY）。复用 lookup_pricing + 同样公式。"""
    pricing = provider_pricing or lookup_pricing(model)
    return (prompt_tokens / 1000.0) * pricing[0] + (completion_tokens / 1000.0) * pricing[1]
```

`CostTracker.accumulate` 重构为复用 `estimate_cost`（DRY）：
```python
def accumulate(self, model, prompt_tokens, completion_tokens, provider_pricing=None) -> float:
    cost = estimate_cost(model, prompt_tokens, completion_tokens, provider_pricing)
    self._prompt_tokens += prompt_tokens
    self._completion_tokens += completion_tokens
    self._total_tokens += prompt_tokens + completion_tokens
    self._cost_cny += cost
    return cost
```

### 3.2 `cost/tracker.py`：补 deepseek 等常用模型 pricing

当前 `_DEFAULT_PRICING` 没收录 deepseek（项目实际默认模型），落到 fallback `(0.0072, 0.0144)`。补：

```python
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # ... 现有条目 ...
    # deepseek 官方价格（2026-04 报价，CNY/1K tokens；命中缓存有折扣，本表用未命中价）
    "deepseek-chat": (0.001, 0.008),
    "deepseek-reasoner": (0.004, 0.016),
    # 国产常见
    "qwen-max": (0.04, 0.12),
    "qwen-plus": (0.004, 0.012),
    "moonshot-v1-8k": (0.012, 0.012),
}
```

价格条目准确性是次要问题（用户可在 `provider_pricing` 参数显式覆盖）；关键是**有数 > 没数**，覆盖最常用的模型即可。

### 3.3 `models/common.py`：扩 `UsageSummary`

```python
class UsageSummary(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cny: float = 0.0   # 新增；老 usage 反序列化时默认 0
```

向后兼容：pydantic v2 默认对未知字段不报错，老的序列化 JSON 缺 `cost_cny` 时构造默认 0。

### 3.4 `llm.py`：`LLMClient.chat()` 末尾算成本

在 `chat()` 内构造 `UsageSummary` 之前：

```python
from tianshu.cost.tracker import estimate_cost  # 顶部 import

# chat() 内（约 line 152-158）：
if response.usage:
    cost = estimate_cost(
        model,                       # 实际生效的 model（_resolve_model() 返回的）
        response.usage.prompt_tokens or 0,
        response.usage.completion_tokens or 0,
    )
    usage = UsageSummary(
        prompt_tokens=response.usage.prompt_tokens or 0,
        completion_tokens=response.usage.completion_tokens or 0,
        total_tokens=response.usage.total_tokens or 0,
        cost_cny=cost,
    )
```

`chat_stream()` 同样处理（最后一个 chunk 含 usage 时算成本）。

### 3.5 `executor/orchestrator/loop.py`：直接读 cost_cny

把当前的：
```python
actor_cost = float(getattr(actor_result.usage, "cost_cny", 0.0) or 0.0)
```
改为：
```python
actor_cost = actor_result.usage.cost_cny if actor_result.usage else 0.0
```

`getattr` 兜底可以删 —— `UsageSummary` 现在保证有 `cost_cny` 字段。

### 3.6 `cost/manager.py`：与现有 cost_manager 协调

现有 `CostManager.handle_execution_completed` 在 `execution.completed` 事件里再算一次成本（grep 确认 —— `_get_tracker(edict_id).accumulate(...)`）。这个不动，原因：
- cost_manager 跨 edict 累计 + 触发 `cost.budget_exceeded` 事件，是宏观预算控制
- `UsageSummary.cost_cny` 是单次调用快照，给 orchestrator 做 per-iteration 决策用
- 两层独立，互不干扰；都用同一份 `estimate_cost` 函数，价格一致

## 4. 错误处理

| 场景 | 处理 |
|------|------|
| 价格表缺该 model | 落到 fallback `(0.0072, 0.0144)`（现有行为）；写一条 `logger.debug`，不告警（避免噪声） |
| `response.usage` 为 None（流式中间 chunk） | usage 字段保持默认 0；终态 chunk 才填 |
| token 数为负 / NaN | LiteLLM 不会返回这种值；不做防御 |

## 5. 测试

### 5.1 单元测试（新增 `tests/test_cost_estimation.py`）

```python
def test_estimate_cost_known_model():
    cost = estimate_cost("deepseek-chat", 1000, 500)
    # 1000 prompt × 0.001 + 500 completion × 0.008 = 0.001 + 0.004 = 0.005
    assert abs(cost - 0.005) < 1e-6

def test_estimate_cost_unknown_model_falls_back():
    cost = estimate_cost("unknown-model", 1000, 500)
    # fallback (0.0072, 0.0144): 0.0072 + 0.0072 = 0.0144
    assert abs(cost - 0.0144) < 1e-6

def test_estimate_cost_with_provider_pricing_override():
    cost = estimate_cost("anything", 1000, 500, provider_pricing=(0.01, 0.02))
    assert abs(cost - 0.02) < 1e-6  # 0.01 + 0.01 = 0.02

def test_cost_tracker_accumulate_uses_estimate_cost():
    """CostTracker.accumulate 重构后行为不变。"""
    t = CostTracker()
    cost = t.accumulate("deepseek-chat", 1000, 500)
    assert t.cost_cny == cost
    assert t.total_tokens == 1500

def test_usage_summary_default_cost_zero():
    u = UsageSummary()
    assert u.cost_cny == 0.0

def test_usage_summary_old_json_no_cost_field():
    """老 JSON 缺 cost_cny 也能反序列化。"""
    u = UsageSummary.model_validate({"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150})
    assert u.cost_cny == 0.0
```

### 5.2 集成测试

跑修复后的 `/tmp/e2e_outer_loop.py`（或迁移到 `tests/test_e2e_cost_live.py` + `@pytest.mark.live`），验证 `result.state.total_cost_cny > 0`。

### 5.3 回归

现有 `cost/manager.py` 测试套要全绿（确认 `accumulate` 重构后行为一致）：
```bash
pytest tests/ -k "cost" -v
```

## 6. 验收标准

- [ ] `estimate_cost()` module-level 函数存在，单测覆盖 known / unknown / provider_override 三分支
- [ ] `UsageSummary.cost_cny` 字段存在，默认 0.0，pydantic 向后兼容
- [ ] `LLMClient.chat()` 实际调用后 `usage.cost_cny > 0`（命中价格表的 model）
- [ ] `_DEFAULT_PRICING` 含 deepseek-chat 条目
- [ ] outer loop 跑 e2e 后 `result.state.total_cost_cny > 0`
- [ ] 老路径回归全绿（22 个）

## 7. 显式不做（v2+）

- 价格表运行时可配（YAML / DB） —— 当前硬编码够用
- 流式 chat_stream 的中间 chunk 部分成本 —— 终态 chunk 即可
- LiteLLM 内置成本（`response._hidden_params["response_cost"]`）—— 部分 provider 才有，不依赖
- 缓存命中折扣（如 deepseek context cache 有 90% 折扣） —— v1 用未命中价（保守上限）

## 8. 风险

1. **deepseek 等价格条目可能过时**：用户可定期手工更新；不在本 spec 范围
2. **LiteLLM 在某些 provider 下 `response.usage` 为 None**：`if response.usage:` 守护已就位（现有代码就是这样写的）
3. **现有 `CostManager.handle_execution_completed` 调 `accumulate(...)` 后会和 `UsageSummary.cost_cny` 双重计算？** —— **不会**：cost_manager 是给跨 edict 预算用的独立账本，和单次 `UsageSummary.cost_cny` 是两个数据流；orchestrator 只读 `UsageSummary.cost_cny` 做单 edict 内决策。
