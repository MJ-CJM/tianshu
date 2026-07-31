# llm · 成本治理与预算熔断

> 户部账房。把「这次任务花了多少、会不会超支」变成可实时熔断、可事后审计的数据。

## 1. 设计目标

| 目标 | 手段 |
|---|---|
| 实时止损 | 每轮 LLM 调用前检查预算，超支立即 `block` |
| 精确记账 | 三维定价区分缓存命中/未命中，按 token 实算 CNY |
| 可归因 | 落 `cost_ledger`，带 edict / memorial / provider / model |
| 可治理 | 预算 scope 分层（global / edict / submitter），UI 可配价 |

立场：**成本是横切关注点，靠 hook + event 接入，不侵入 Agent 主循环**。Agent 不知道 CostManager 的存在，只是循环里恰好有钩子。

## 2. hook + event 双轨

| 接入点 | 时机 | 动作 |
|---|---|---|
| `LLMClient` usage observer | 每次有用量的 chat/chat_stream 完成后 | 按 edict/memorial/operation 归因；覆盖 Agent、Planner、Critic、Auditor、Rubric、会诊等 |
| `LLM_OUTPUT` hook | 兼容旧 Agent/集成调用 | 进入同一 per-run tracker |
| `BEFORE_ITERATION` hook | 每轮迭代前 | `check_budget`，超 `runtime.cost_budget_cny` 或 global/edict/submitter 预算 → `HookResult(block=True)` |
| `execution.completed/failed/cancelled` event | 执行结束 | `_finalize_cost`：一次性写 `cost_ledger` + 清理 tracker |

为什么分层：**实时熔断需要每轮可见的累加值**，**根执行落盘只需在终态做一次**，避免
每轮写库。tracker 以 `(edict_id, memorial_id)` 为 run 身份；同一 Edict 的 follow-up 或
调度 run 不再互相覆盖。没有业务上下文的平台 LLM 调用以 `__platform__` 直接记账，不污染
某道任务预算。

## 3. 三维定价模型

每个 provider 的价格是 `(input_miss, input_hit, output)`，单位 CNY/1K tokens：

| 维度 | 含义 |
|---|---|
| `input_miss` | 输入未命中缓存价（普通输入） |
| `input_hit` | 输入命中缓存价（折扣价） |
| `output` | 输出价 |

成本公式（`estimate_cost`）：

```
input_miss_tokens = max(0, prompt_tokens - cache_read_tokens)
cost = input_miss_tokens/1000 × miss_price
     + cache_read_tokens/1000 × hit_price
     + completion_tokens/1000 × output_price
```

### 缓存字段提取（多 provider 适配）

LiteLLM 没统一各家 cache 字段，按 model 名前缀路由提取 `cache_read_tokens`：

| Provider | 字段 |
|---|---|
| `claude*` / `anthropic/*` | `usage.cache_read_input_tokens` |
| `deepseek*` | `usage.prompt_cache_hit_tokens` |
| `gpt*` / OpenAI 兼容 | `usage.prompt_tokens_details.cached_tokens` |
| 其他 | 0（保守） |

### 配价粒度与 fallback

按 **provider name** 配价（`providers` 表三列），每维独立 fallback：

```
provider 自定义字段（非 NULL）
  → _DEFAULT_PRICING[model]（内置默认表）
    → 兜底 (0.0072, 0.0072, 0.0144)
```

`input_hit` 特殊：NULL 且用户已自定义 `input_miss` → hit=miss（视为无折扣）；否则取默认表的 hit 价。

## 4. 预算分层与熔断

`BudgetChecker` 检查三个 scope，任一超支即熔断：

| scope | 含义 |
|---|---|
| `global` | 所有 edict 总预算 |
| `edict:{id}` | 单任务预算 |
| `submitter:{user}` | 按提交者预算 |

另有 `Edict.runtime.cost_budget_cny`：在 `BEFORE_ITERATION` 用内存 tracker 即时比对（不依赖落盘），超支时额外 `emit("cost.budget_exceeded")` 通知。熔断结果是 `HookResult(block=True)`，由 executor 转为 `ExitReason.BUDGET_EXHAUSTED` 终止循环。

预算 period ∈ {daily, weekly, monthly}，`reset_at` 到期归零。

## 5. 成本归因

落 `cost_ledger` 时，同一 run 只有一个 provider/model 则保留真实名称；观察到多个时写
`multiple`，不再用最后一次调用冒充整个 run。归因维度：edict / memorial / provider /
model / prompt、completion、total、cache-read tokens / cost_cny。V23 已把
`cache_read_tokens` 作为正式列持久化并进入汇总。

按 persona 的成本归因经由 memorial → persona_id 间接关联（memorial 记 `persona_id`），不在 cost_ledger 单列。

## 6. 显式不做

- Anthropic `cache_creation_input_tokens`（写缓存费）单独 4 维建模 —— 当前按 miss 价计入
- 多币种（仅 CNY）、model 级配价（仅 provider 级）、价格历史/审计
- 跨 provider/model run 的逐调用明细分摊（当前聚合标签为 `multiple`）
- 把本地估算当作供应商正式账单；实际精度仍取决于 provider 用量字段和配价

**相关实现**：[../../impl/llm/](../../impl/llm/)
