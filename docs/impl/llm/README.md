# llm 子系统 · 实现现状

**相关设计**：[../../design/llm/](../../design/llm/)

覆盖 `src/tianshu/llm.py`、`src/tianshu/config_manager.py`、`src/tianshu/providers/`、`src/tianshu/cost/`。

## 1. 代码地图

| 文件 | 关键类/函数 | 职责 |
|---|---|---|
| `llm.py` | `LLMClient`、`LLMResponse`、`_apply_prompt_caching`、`_extract_cache_read_tokens`、`_extract_model_echo`、`_log_model_echo`、`_resolve_model` | LiteLLM 封装、缓存、回显校验 |
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
3. 组装 kwargs（固定带 `drop_params=True`），`@retry` 装饰（`tenacity`，3 次，仅瞬态错误）。
4. `await litellm.acompletion(**kwargs)`。
5. `_extract_model_echo` + `_log_model_echo` 校验上游回显。
6. `_extract_cache_read_tokens` 提缓存命中，`estimate_cost` 算钱，组 `UsageSummary`。

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
  → LLM_OUTPUT hook → CostManager.on_llm_output → CostTracker.accumulate（本地累加，记 last_provider_name）
  → BEFORE_ITERATION hook → CostManager.on_before_iteration → 超预算 HookResult(block=True) [+ emit cost.budget_exceeded]
  → execution.completed/failed event → CostManager._finalize_cost → storage.save_cost_record(cost_ledger) + tracker.pop
```

### 接入点登记

`CostManager` 由 `app.py` 装配，hook 注册 `LLM_OUTPUT` / `BEFORE_ITERATION`，event 订阅 `execution.completed`/`execution.failed`（priority=150，详见 `runtime-flow.md` §2）。`record()` 同时更新 `global` 与 `edict:{id}` 的 `spent_cny`。

## 6. 默认定价表

`_DEFAULT_PRICING`（`cost/tracker.py:20`）按 model 名键入 3-tuple，覆盖 gpt / claude / deepseek / qwen / moonshot / MiniMax；缺失走 `_FALLBACK_PRICING = (0.0072, 0.0072, 0.0144)`。`lookup_pricing` 支持剥 `provider/` 前缀后再查。provider 自定义价（`providers.cost_per_1k_*` 三列）覆盖默认表，每维独立 fallback。

## 7. 待办 / 已知简化

- `cost_ledger` 无 `cache_read_tokens` 列（cache 维度数据未跨 edict 细化聚合）。
- 跨 provider edict 成本归 `last_provider_name`，非逐调用分摊。
- `TaskRequirements` 的 `fastest` / `round_robin` 策略已定义未实现（`_select` 仅 cheapest/priority）。
