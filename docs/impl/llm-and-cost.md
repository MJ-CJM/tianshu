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
