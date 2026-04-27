# LLM 与成本

覆盖 `src/tianshu/llm.py`、`src/tianshu/config_manager.py`、`src/tianshu/providers/`、`src/tianshu/cost/`。

---

## 1. LLMClient（`llm.py`）

薄封装在 LiteLLM 之上，统一 chat / chat_stream / function calling。

### LLMResponse

```python
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict] | None          # [{id, name, args}]
    usage: UsageSummary                     # prompt/completion/total tokens
    reasoning_content: str | None = None    # DeepSeek-R1 兼容
    finish_reason: str | None = None        # stop / length / tool_calls
```

### 构造参数

`LLMClient(model, api_key, api_base, max_retries=3, temperature=0.7, top_p=1.0, max_tokens=4096, timeout=300)`。

### Anthropic Prompt Caching

`_apply_prompt_caching(messages, model)` 对 `claude*` / `anthropic/*` 模型插入 `cache_control: {"type": "ephemeral"}` 断点：

- 所有 `system` 消息末尾 block 标记
- 非 system 消息中最后 3 条（user/assistant）末尾 block 标记

命中缓存可节省 ~75% 输入 token。非 Anthropic 模型原样返回 messages。

`_add_cache_control(content, marker)` 将 str/list/dict 统一转成 block list（`[{"type": "text", "text": ...}]`），在最后一个 block 插入 `cache_control`。

### Retry 策略

`@retry` 装饰 `chat`：`wait_exponential(min=1, max=4)` × `stop_after_attempt(3)`，仅对 `RateLimitError / Timeout / ServiceUnavailableError` 重试。`reraise=True` 以便异常逃出 retry 后被 Agent 捕获。

### 流式（`chat_stream`）

逐 chunk yield `LLMResponse`，末 chunk 携带完整 usage。Agent 的 `stream_callback` 通过 `StreamCallback.on_text_delta / on_tool_call_delta / on_finish` 分发到 WebSocket。

### `_resolve_model()` 与 provider hints

`_PROVIDER_HINTS`：`deepseek → deepseek`、`minimaxi / minimax → openai`，为 LiteLLM 提供 provider 前缀以正确路由。

### `drop_params=True`

请求 kwargs 固定加 `drop_params=True`，让 LiteLLM 自动丢弃不被目标 provider 支持的参数（如 Anthropic 不支持 `top_p`）。

## 2. ConfigManager（`config_manager.py`）

线程安全的运行时配置管理。

### LLMConfigState（frozen dataclass）

```python
@dataclass(frozen=True)
class LLMConfigState:
    name: str
    model: str
    api_key: str
    api_base: str = ""
    max_retries: int = 3
    temperature: float = 0.7
    top_p: float = 1.0
    max_tokens: int = 4096
    enabled: bool = True
```

### AgentConfigState（frozen dataclass）

```python
@dataclass(frozen=True)
class AgentConfigState:
    agent_max_iterations: int = 20
    agent_timeout_seconds: int = 300
    skills_char_budget: int = 30000
    skill_review_enabled: bool = True
    skill_review_interval: int = 5
    fallback_llm_config_name: str | None = None
```

### 多配置切换

`ConfigManager(initial, agent_config, storage)`：
- 启动时从 `llm_configs` 表加载所有命名配置，取 `active_name`
- `list_configs() -> (dict, active_name)` — 供前端 `SystemManagementPage` 渲染
- `set_active(name)` — 切换激活配置
- `get_active() / get(name)` — 取具体 `LLMConfigState`
- `save(config)` — INSERT OR REPLACE 到 `llm_configs`
- 内部 `threading.Lock` 保护并发读写

persona 级别可通过 `AgentPersona.llm_config_name` 指向某个命名配置，Planner 与 Agent 在需要时按名取，空则用 active。

## 3. ProviderManager（`providers/`）

### protocol.py

```python
@dataclass
class ProviderCapability:
    name: str
    model: str
    chat: bool = True
    streaming: bool = True
    function_calling: bool = True
    max_context: int = 128000
    rpm_limit: int = 0
    tpm_limit: int = 0

@dataclass
class ProviderInfo:
    name: str
    status: str    # active / disabled / degraded
    priority: int  # 小者优先
    capabilities: ProviderCapability

@dataclass
class TaskRequirements:
    streaming: bool = False
    function_calling: bool = False
    context_size: int = 0
```

### manager.py

`ProviderManager(storage, config_manager)`：
- `sync_from_config(config)` — 把 `LLMConfigState` 写入 `providers` 表（active 配置 priority=0，其他 priority=100）
- `sync_all()` — 批量同步
- `list_providers(status_filter=None)` — 供 UI
- `select_best(requirements)` — 按 priority + capability 过滤选最合适（目前实现较简单，未来可加 RPM/TPM 熔断）

LiteLLM 是所有 provider 的底层实现，`litellm_provider.py` 负责 `create_llm_client(config)` 工厂。

## 4. CostManager（`cost/`）

### 数据模型（`cost/models.py`）

- `CostRecord`：`edict_id / memorial_id / provider_name / model / prompt_tokens / completion_tokens / total_tokens / cost_cny / created_at`
- `CostSummary`：period 聚合视图
- `BudgetStatus`：`scope / budget_cny / spent_cny / period / reset_at / exceeded`

### CostManager 核心职责

`CostManager(storage, event_bus)`：

**Hook 订阅**：
- `BEFORE_ITERATION`（priority 较低）→ `check_budget()` 熔断。若 `global` 或 `edict:{id}` 预算已超，返回 `HookResult(block=True)` → `ExitReason.BUDGET_EXHAUSTED`
- `LLM_OUTPUT` → `record_call()` 更新 tracker，usage.tokens × provider pricing 计费

**Event 订阅**：
- `execution.completed` / `execution.failed`（priority=150，见 overview.md）→ 落盘 `cost_ledger` + 清理 `_trackers[edict_id]`
- `cost.budget_exceeded` → notifier handler 发告警

### BudgetChecker（`cost/budget.py`）

- `global` scope：所有 edicts 总预算
- `edict:{id}` scope：单次任务预算
- `period` ∈ {hour, day, month, total}，`reset_at` 到期自动归零

### CostTracker（`cost/tracker.py`）

per-edict 累加器，避免频繁 DB 写：
- `add(usage, cost)` — 本地累加
- `snapshot()` → CostRecord，写库时一次性 flush

定价：LiteLLM 内置 `cost_per_token(model, prompt/completion)`，乘以 CNY 汇率（`USD_TO_CNY` 常量）转成人民币。

## 5. TokenEstimator

位置：`executor/compaction/token_estimator.py`（见 `executor.md` §2）。

## 代码路径索引

- `src/tianshu/llm.py`
- `src/tianshu/config_manager.py`
- `src/tianshu/providers/protocol.py`
- `src/tianshu/providers/manager.py`
- `src/tianshu/providers/litellm_provider.py`
- `src/tianshu/cost/manager.py`
- `src/tianshu/cost/tracker.py`
- `src/tianshu/cost/budget.py`
- `src/tianshu/cost/models.py`

## 多维度计费 (2026-04-27)

### 三维定价模型

每个 provider 的价格由 3 个维度构成（CNY/1K tokens）：

| 维度 | 含义 |
|------|------|
| `input_miss` | 输入未命中缓存价（普通输入） |
| `input_hit`  | 输入命中缓存价（折扣价） |
| `output`     | 输出价 |

成本公式：

```
input_miss_tokens = max(0, prompt_tokens - cache_read_tokens)
cost = input_miss_tokens / 1000 × miss_price
     + cache_read_tokens / 1000 × hit_price
     + completion_tokens / 1000 × output_price
```

### Cache 字段提取（多 provider 适配）

`litellm` 没统一各 provider 的 cache 字段，`_extract_cache_read_tokens(usage, model)` 按 model 名前缀路由：

| Provider | 字段路径 |
|----------|---------|
| `claude*` / `anthropic/*` | `usage.cache_read_input_tokens` |
| `deepseek*` | `usage.prompt_cache_hit_tokens` |
| `gpt*` / OpenAI 兼容 | `usage.prompt_tokens_details.cached_tokens` |
| 其他 | 0（保守） |

注：Anthropic 的 `cache_creation_input_tokens`（写入缓存费）当前不单独建模，按 `input_miss` 价计入。

### 配置粒度与 fallback 链

按 **provider name** 配价（`providers.cost_per_1k_*` 三列），每维独立 fallback：

```
provider 自定义字段 (非 NULL) →
  _DEFAULT_PRICING[model] (3-tuple) →
    (0.0072, 0.0072, 0.0144) 兜底
```

`cost_per_1k_cache_read` 特殊：NULL 时
- 若 `cost_per_1k_prompt` 已自定义 → hit = miss（无折扣）
- 否则 → 默认表的 hit 价

### 户部账房可视化配置

`CostDashboardPage` 含 "提供方计价" 卡片：每行展示 provider 三维生效价 + 来源 badge（`custom` / `mixed` / `default`）。点击编辑弹 Modal 三个 InputNumber，留空字段落 `_DEFAULT_PRICING`。

### API endpoints

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/providers/{name}/pricing/effective` | 当前生效价 + 来源 |
| `PUT` | `/api/providers/{name}/pricing` | 部分更新（None 字段不动） |
| `DELETE` | `/api/providers/{name}/pricing` | 重置三维为 NULL |

### 数据流

```
LLM 调用结束 → litellm 返 response.usage
   ↓
_extract_cache_read_tokens(usage, model) 提取 cache_read
   ↓
estimate_cost(model, pt, ct, cache_read, provider_pricing=override)
   ↓
UsageSummary { ..., cache_read_tokens, cost_cny }
   ↓
LLM_OUTPUT hook (含 provider_name) → CostManager.on_llm_output
   ↓
CostTracker.accumulate (持久 last_provider_name)
   ↓
execution.completed → CostManager._finalize_cost
   ↓
cost_ledger 记录 (provider_name = tracker.last_provider_name 而非硬编码 default)
```

### 显式不做（v2 议题）

- Anthropic `cache_creation_input_tokens` 单独 4 维建模
- 多币种支持（仅 CNY）
- model 级配价（仅 provider 级）
- 价格历史 / 审计日志
- 跨 provider edict 时的精确成本归属（用 last_provider_name 简化）
- `cost_ledger` 表加 `cache_read_tokens` 列（当前 cache 维度数据存于 `outer_loop_iterations.cost_cny`，跨 edict 聚合不需要细化）
