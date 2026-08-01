# LLM 成本接入 UsageSummary 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** 让 LLM 调用结束时 `UsageSummary.cost_cny` 自动填入实际成本，使 outer loop 的预算上限真正生效。

**Architecture:** 在 `cost/tracker.py` 加 module-level `estimate_cost()`；`LLMClient.chat()` 调它写进 `UsageSummary`；orchestrator 直接读。**不动** `CostManager` 跨 edict 累计逻辑（独立的两层）。

**Tech Stack:** Python 3.12+, pydantic v2, litellm。

**Spec：** `docs/superpowers/specs/2026-04-27-llm-cost-attribution-design.md`

---

## File Structure

**修改：**
```
src/tianshu/cost/tracker.py            # +estimate_cost / +lookup_pricing module-level fn；CostTracker.accumulate 复用
src/tianshu/models/common.py           # UsageSummary +cost_cny 字段
src/tianshu/llm.py                     # chat() 调 estimate_cost 填 UsageSummary
src/tianshu/executor/orchestrator/loop.py  # 删 getattr 兜底，直接 usage.cost_cny
```

**新增：**
```
tests/test_cost_estimation.py          # 单元测试 6 个
```

---

## Task 1: 抽 estimate_cost 到 module-level + 补 deepseek pricing

**Files:**
- Modify: `src/tianshu/cost/tracker.py`

- [ ] **Step 1: 在 _DEFAULT_PRICING 末尾加 deepseek/qwen/moonshot**

```python
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o": (0.018, 0.072),
    # ... 现有保留 ...
    "claude-haiku-4-5": (0.0072, 0.036),
    # 2026-04-27: 国产常用模型
    "deepseek-chat": (0.001, 0.008),
    "deepseek-reasoner": (0.004, 0.016),
    "qwen-max": (0.04, 0.12),
    "qwen-plus": (0.004, 0.012),
    "moonshot-v1-8k": (0.012, 0.012),
}
```

- [ ] **Step 2: 在 _DEFAULT_PRICING 之后、CostTracker class 之前加 module-level 函数**

```python
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
```

> **顺序注意**：`lookup_pricing` 和 `estimate_cost` 内部引用 `CostTracker._lookup_pricing`，所以**必须在 CostTracker 之后定义**。把这两个函数挪到文件末尾（CostTracker 类之后）。

- [ ] **Step 3: 重构 CostTracker.accumulate 复用 estimate_cost**

```python
def accumulate(self, model, prompt_tokens, completion_tokens, provider_pricing=None) -> float:
    """Add token usage and return incremental cost in CNY."""
    cost = estimate_cost(model, prompt_tokens, completion_tokens, provider_pricing)
    self._prompt_tokens += prompt_tokens
    self._completion_tokens += completion_tokens
    self._total_tokens += prompt_tokens + completion_tokens
    self._cost_cny += cost
    return cost
```

- [ ] **Step 4: 烟雾验证**

```bash
cd <repo> && python -c "
from tianshu.cost.tracker import estimate_cost, lookup_pricing, CostTracker

# 1. deepseek 现在有价格
p = lookup_pricing('deepseek-chat')
assert p == (0.001, 0.008), f'deepseek pricing wrong: {p}'

# 2. estimate_cost 计算正确
c = estimate_cost('deepseek-chat', 1000, 500)
# 1.0 × 0.001 + 0.5 × 0.008 = 0.001 + 0.004 = 0.005
assert abs(c - 0.005) < 1e-6, f'cost mismatch: {c}'

# 3. CostTracker.accumulate 行为一致
t = CostTracker()
incr = t.accumulate('deepseek-chat', 1000, 500)
assert abs(incr - 0.005) < 1e-6
assert abs(t.cost_cny - 0.005) < 1e-6

# 4. provider_pricing 覆盖
c2 = estimate_cost('anything', 1000, 500, provider_pricing=(0.01, 0.02))
assert abs(c2 - 0.02) < 1e-6  # 0.01 + 0.01 = 0.02

# 5. unknown model 落 fallback
c3 = estimate_cost('totally-unknown', 1000, 500)
assert abs(c3 - 0.0144) < 1e-6  # 0.0072 + 0.0072

print('OK')
"
```

期望：`OK`

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/cost/tracker.py
git commit -m "refactor(cost): 抽 estimate_cost / lookup_pricing 到 module-level + 补 deepseek/qwen/moonshot pricing"
```

---

## Task 2: UsageSummary 加 cost_cny 字段

**Files:**
- Modify: `src/tianshu/models/common.py`

- [ ] **Step 1: 加 cost_cny 字段**

Read `src/tianshu/models/common.py` 找到 `class UsageSummary(BaseModel):`，加字段：

```python
class UsageSummary(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cny: float = 0.0   # 新增
```

- [ ] **Step 2: 烟雾验证（向后兼容）**

```bash
cd <repo> && python -c "
from tianshu.models.common import UsageSummary

# 1. 默认 cost_cny=0
u = UsageSummary()
assert u.cost_cny == 0.0

# 2. 老 JSON 缺字段也能反序列化
u2 = UsageSummary.model_validate({'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150})
assert u2.cost_cny == 0.0

# 3. 新 JSON 含 cost_cny
u3 = UsageSummary.model_validate({'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150, 'cost_cny': 0.123})
assert u3.cost_cny == 0.123

# 4. 序列化 / 反序列化 round-trip
data = u3.model_dump_json()
u4 = UsageSummary.model_validate_json(data)
assert u4.cost_cny == 0.123

print('OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/models/common.py
git commit -m "feat(models): UsageSummary +cost_cny 字段（默认 0.0，向后兼容）"
```

---

## Task 3: LLMClient.chat() 写入 cost

**Files:**
- Modify: `src/tianshu/llm.py`

- [ ] **Step 1: 加 import**

`src/tianshu/llm.py` 顶部 import 区加：

```python
from tianshu.cost.tracker import estimate_cost
```

- [ ] **Step 2: 修改 chat() 内构造 UsageSummary 的位置（约 line 152-158）**

把：
```python
usage = UsageSummary()
if response.usage:
    usage = UsageSummary(
        prompt_tokens=response.usage.prompt_tokens or 0,
        completion_tokens=response.usage.completion_tokens or 0,
        total_tokens=response.usage.total_tokens or 0,
    )
```

改成：
```python
usage = UsageSummary()
if response.usage:
    pt = response.usage.prompt_tokens or 0
    ct = response.usage.completion_tokens or 0
    cost = estimate_cost(model, pt, ct)
    usage = UsageSummary(
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=response.usage.total_tokens or 0,
        cost_cny=cost,
    )
```

> 注意：`model` 已是 `_resolve_model()` 返回的实际生效 model（line 125），价格查询与实际计费模型一致。

- [ ] **Step 3: chat_stream() 同样处理**

Read `chat_stream()` 找到 final usage chunk 构造处，**仅在含完整 usage 的 chunk** 加同样的 estimate_cost 计算（部分 chunk 不算）。

> 注：如果 chat_stream 当前没构造 UsageSummary（流式不返回 usage），跳过这步。先 grep 确认。

```bash
cd <repo> && grep -n "UsageSummary\|response.usage" src/tianshu/llm.py
```

如果 chat_stream 内有 UsageSummary 构造，按 chat() 同样改；如果没有就跳过。

- [ ] **Step 4: 烟雾验证**（不调真 LLM，mock litellm.acompletion）

```bash
cd <repo> && python -c "
import asyncio
from unittest.mock import patch, MagicMock
from tianshu.llm import LLMClient

async def main():
    fake_response = MagicMock()
    fake_response.choices = [MagicMock(message=MagicMock(
        content='hi', tool_calls=None,
    ), finish_reason='stop')]
    fake_response.usage = MagicMock(
        prompt_tokens=1000, completion_tokens=500, total_tokens=1500,
    )

    c = LLMClient(model='deepseek-chat', api_key='x')
    with patch('tianshu.llm.litellm.acompletion', return_value=fake_response):
        resp = await c.chat(messages=[{'role':'user','content':'x'}])

    assert resp.usage.prompt_tokens == 1000
    assert resp.usage.completion_tokens == 500
    # deepseek-chat: 1000×0.001 + 500×0.008 = 0.001 + 0.004 = 0.005
    assert abs(resp.usage.cost_cny - 0.005) < 1e-6, f'cost wrong: {resp.usage.cost_cny}'
    print('OK')

asyncio.run(main())
"
```

期望：`OK`

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/llm.py
git commit -m "feat(llm): LLMClient.chat() 自动算成本写进 UsageSummary.cost_cny"
```

---

## Task 4: orchestrator/loop.py 直接读 cost_cny

**Files:**
- Modify: `src/tianshu/executor/orchestrator/loop.py`

- [ ] **Step 1: 删 getattr 兜底**

Read `src/tianshu/executor/orchestrator/loop.py` 找到（约 run() 内 actor 调用之后）：

```python
actor_cost = float(getattr(actor_result.usage, "cost_cny", 0.0) or 0.0)
```

改成：

```python
actor_cost = actor_result.usage.cost_cny if actor_result.usage else 0.0
```

- [ ] **Step 2: 跑 orchestrator 全测试套确认零回归**

```bash
cd <repo> && pytest tests/test_orchestrator_loop.py tests/test_outer_loop_resume.py -v 2>&1 | tail -20
```

期望：所有 PASS（10 个集成测试）。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/executor/orchestrator/loop.py
git commit -m "refactor(orchestrator): 直接读 usage.cost_cny（UsageSummary 已保证有此字段）"
```

---

## Task 5: 单元测试

**Files:**
- Create: `tests/test_cost_estimation.py`

- [ ] **Step 1: 写测试**

```python
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
```

- [ ] **Step 2: 跑测试**

```bash
cd <repo> && pytest tests/test_cost_estimation.py -v 2>&1 | tail -20
```

期望：11 个测试全 PASS。

- [ ] **Step 3: Commit**

```bash
git add tests/test_cost_estimation.py
git commit -m "test(cost): estimate_cost + UsageSummary.cost_cny 单元测试"
```

---

## Task 6: 端到端 + 回归

- [ ] **Step 1: 老路径回归**

```bash
cd <repo> && pytest tests/test_executor.py tests/test_agent.py tests/test_backward_compat.py 2>&1 | tail -5
```

期望：22 个 PASS（无回归）。

- [ ] **Step 2: outer loop 测试套**

```bash
cd <repo> && pytest tests/test_orchestrator_loop.py tests/test_outer_loop_resume.py tests/test_archive.py tests/test_escalation.py tests/test_orchestrator_state.py tests/test_orchestrator_checks.py tests/test_orchestrator_critic.py 2>&1 | tail -5
```

期望：40 个 PASS。

- [ ] **Step 3: 现有 cost 测试套（如有）**

```bash
cd <repo> && pytest tests/ -k "cost" -v 2>&1 | tail -15
```

期望：现有 cost 相关测试全 PASS（验证 CostTracker.accumulate 重构后行为不变）。

- [ ] **Step 4: 真 LLM 端到端**（可选，需要 deepseek API key）

跑修复后的 e2e 脚本（之前的 `/tmp/e2e_outer_loop.py` 或迁移到 tests）：

```bash
cd <repo> && python /tmp/e2e_outer_loop.py 2>&1 | grep -A1 "cost_cny\|total_cost"
```

期望：`total_cost_cny` 现在 > 0（之前是 0.0）。

- [ ] **Step 5: 更新 spec 验收清单**

修改 `docs/superpowers/specs/2026-04-27-llm-cost-attribution-design.md` §6 的 6 个 `[ ]` 改 `[x]`。

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-04-27-llm-cost-attribution-design.md
git commit -m "docs(spec): LLM 成本接入验收完成"
```

---

## Self-Review

**1. Spec coverage：**

| spec 章节 | task |
|----------|------|
| §3.1 抽 module-level helper | Task 1 |
| §3.2 补 deepseek pricing | Task 1 |
| §3.3 UsageSummary +cost_cny | Task 2 |
| §3.4 LLMClient 接入 | Task 3 |
| §3.5 orchestrator 直接读 | Task 4 |
| §3.6 cost_manager 协调（不动） | 无需 task —— 决策是 "不动" |
| §5 测试 | Task 5 |
| §6 验收 | Task 6 |

✅ 全覆盖。

**2. Type 一致性：**
- `estimate_cost(model, prompt_tokens, completion_tokens, provider_pricing=None) -> float` 在 Task 1 定义，Task 3 import 时一致
- `UsageSummary.cost_cny: float = 0.0` 全文一致

**3. Placeholder 扫描：** 无 TBD/TODO。

**4. 风险已显式列出**（spec §8）：
- deepseek 价格表会过时 —— 用户手工维护
- cost_manager 重复计算？—— 不会，已说明
