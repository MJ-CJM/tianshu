# llm 子系统 · 实现现状

**相关设计**：[../../design/llm/](../../design/llm/)

覆盖 `src/tianshu/llm.py`、`src/tianshu/config_manager.py`、`src/tianshu/providers/`、`src/tianshu/cost/`。

## 1. 代码地图

| 文件 | 关键类/函数 | 职责 |
|---|---|---|
| `llm.py` | `LLMClient`、`LLMUsageContext`、`set_usage_observer`、缓存/回显 helpers | LiteLLM 封装、统一用量观察 |
| `config_manager.py` | `ConfigManager`、`LLMConfigState`、`AgentConfigState` | 多命名配置 + Agent 运行参数 |
| `providers/manager.py` | `ProviderManager` | 路由、限速、配价生效计算 |
| `providers/capabilities.py` | `ProviderInfo`、`ProviderCapability`、`TaskRequirements` | pydantic 容量/需求模型 |
| `providers/litellm_provider.py` | `create_llm_client` | LLMClient 工厂 |
| `cost/manager.py` | `CostManager` | hook + event 接入、记账 |
| `cost/tracker.py` | `CostTracker`、`estimate_cost`、`lookup_pricing`、`_DEFAULT_PRICING` | 累加器 + 无状态定价 |
| `cost/budget.py` | `BudgetChecker` | 三 scope 预算判定 |
| `cost/models.py` | `CostRecord`、`CostSummary`、`BudgetStatus` | pydantic 数据模型 |

## 2. LLMClient 怎么跑

`LLMClient.__init__` 持有 `model / api_key / api_base / temperature / top_p / max_tokens / timeout / provider_name / pricing_override` 等不可变字段。

`chat()` 流程（`llm.py:253`）：
1. `_resolve_model()` 给裸模型名补 provider 前缀（`_PROVIDER_HINTS`：`deepseek→deepseek`、`minimax(i)→openai`，默认 `openai`）。
2. `_apply_prompt_caching(messages, model)` —— 仅 `claude*`/`anthropic/*` 插 `cache_control`。
3. 组装 kwargs（固定带 `drop_params=True`）；非 Router 路径按
   `max_retries + 1` 对瞬态错误重试，Router 路径不叠加外层重试。
4. `await litellm.acompletion(**kwargs)`。
5. `_extract_model_echo` + `_log_model_echo` 校验上游回显。
6. `_extract_cache_read_tokens` 提缓存命中，`estimate_cost` 算钱，组 `UsageSummary`。
7. `_observe_usage` 把 usage、provider/model 和可选
   `LLMUsageContext(edict_id, memorial_id, operation)` 交给 CostManager。

`chat_stream()`（`llm.py:333`）逐 chunk yield 文本/工具增量，工具调用按 `index` 拼接 `args`，末 chunk 带完整 `UsageSummary`。回显在首个有效 chunk 校验一次。

## 3. ConfigManager 怎么扩展

- 内存态：`_configs: dict[name, LLMConfigState]` + `_active_name`，一把 `threading.Lock`。
- 持久化：`_load_from_db` ← `storage.list_llm_configs()`；`_persist` → `storage.save_llm_config`（INSERT OR REPLACE）。
- 扩展 Agent 运行参数：往 `AgentConfigState` 加 frozen 字段即可，`update_agent_config` 用 `dataclasses.replace` 过滤合法字段后整体替换。
- **新增配置字段**：同时改 `LLMConfigState`、`_load_from_db`、`_persist`、`_update_locked` 与 `llm_configs` 表 schema（storage 迁移）。

## 4. ProviderManager 怎么选

`get_client()`（`manager.py:150`）决策链：
1. `config_name_override`（persona 级）非空且 enabled → 直接用该命名配置造 client。
2. 否则取 `active` providers，按 `requirements.capabilities` 过滤，按 `_within_quota` 过滤限速超额者。
3. `_select(providers, strategy)`：`cheapest` 按 `cost_per_1k_prompt`，否则按 `priority` 取最小。
4. 任一步空 → `_fallback_client()` 用 ConfigManager active 配置。

配价生效计算在 `get_effective_pricing` / `get_pricing_with_source`（返回 `source ∈ {custom, mixed, default}`，供「户部账房」UI 展示）。限速窗口由 `record_usage` 维护 `rpm_window_start`，1 分钟滚动。

## 5. 成本链路（数据流）

```
litellm response.usage
  → _extract_cache_read_tokens(usage, model)
  → estimate_cost(model, pt, ct, cache_read, provider_pricing=override) → UsageSummary
  → process usage observer → CostManager.observe_llm_usage
      → 有业务上下文：按 (edict_id, memorial_id) CostTracker.accumulate
      → 无业务上下文：直接记 __platform__
  → BEFORE_ITERATION hook → CostManager.on_before_iteration → 超预算 HookResult(block=True) [+ emit cost.budget_exceeded]
  → execution.completed/failed/cancelled → _finalize_cost → cost_ledger + tracker.pop
```

### 接入点登记

`CostManager` 由 `bootstrap/wiring_llm.py` 装配并注册为 process usage observer。Planner、
Critic、Auditor、Rubric、会诊等调用显式传 context；managed attempt 中的其他调用可从
ambient authority 归因。旧 `LLM_OUTPUT` hook 仅为兼容入口。

V23 后 `cost_ledger` 正式保存 `cache_read_tokens`。同一 run 观察到多个 provider/model
时分别写聚合标签 `multiple`，不再拿最后一次调用代表整条 run。取消也结算已经发生的用量。

## 6. 默认定价表

`_DEFAULT_PRICING`（`cost/tracker.py:20`）按 model 名键入 3-tuple，覆盖 gpt / claude / deepseek / qwen / moonshot / MiniMax；缺失走 `_FALLBACK_PRICING = (0.0072, 0.0072, 0.0144)`。`lookup_pricing` 支持剥 `provider/` 前缀后再查。provider 自定义价（`providers.cost_per_1k_*` 三列）覆盖默认表，每维独立 fallback。

## 7. 待办 / 已知简化

- 多 provider/model run 目前只保存 `multiple` 聚合标签，不保存逐调用账本明细。
- 无业务上下文的 LLM 调用归 `__platform__`，不能反推到具体用户任务。
- 本地成本取决于 provider 返回的 usage 与配置价格，不等同于供应商正式账单。
- `TaskRequirements` 的 `fastest` / `round_robin` 策略已定义未实现（`_select` 仅 cheapest/priority）。
