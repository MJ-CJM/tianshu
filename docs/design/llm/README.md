# llm 子系统 · 设计总览

> 模型接入与成本治理。把「调哪个模型、用多少钱」这两件强外部依赖、强不确定性的事，收敛成一组稳定契约。

## 1. 职责定位

| 关注点 | 子系统给出的答案 |
|---|---|
| 用什么模型 | `ConfigManager` 管命名配置，`ProviderManager` 管路由与限速 |
| 怎么调 | `LLMClient` 薄封装 LiteLLM，统一 chat / 流式 / function calling |
| 调用多贵 | `CostManager` 记账，`estimate_cost` 三维定价，`BudgetChecker` 熔断 |
| 钱花在谁身上 | `cost_ledger` 按 edict / memorial / provider / model 归因 |

设计立场：**模型是可替换的外部资源，不是核心**。所有 provider 差异（缓存字段、定价、限速、上游改写）都在边界处吸收，内层只看到 `LLMResponse` + `UsageSummary`。

## 2. 核心设计判断

| 判断 | 取舍 |
|---|---|
| 用 LiteLLM 而非自研多 provider 适配 | 借生态成熟度；差异点（缓存字段、provider 前缀）只补 LiteLLM 没统一的部分 |
| `LLMConfigState` 用 frozen dataclass | 配置不可变，切换=换引用；并发读写靠一把 `threading.Lock` |
| 成本走 usage observer + hook/event | LLMClient 统一观察所有调用；per-run tracker 实时熔断，成功/失败/取消终态 flush |
| 预算熔断放 `BEFORE_ITERATION` | 在每轮 LLM 调用前拦截，超预算即 `block=True`，避免追加花费 |
| 定价三维 `(input_miss, input_hit, output)` | 缓存命中价独立建模；provider 级配价，每维独立 fallback 到默认表 |
| 模型回显校验 | 中转网关偶尔静默改写/降级模型，靠 `response.model` 回显对照请求侧，mismatch 即 WARNING |

## 3. 与相邻子系统关系

| 相邻方 | 关系 |
|---|---|
| scheduling/planner | Planner 在 managed attempt 中规划，显式传 LLM usage context；直接指派/失败 fallback 会标记 planning mode/reason |
| executor/agent | Agent ReAct loop 每轮调 `LLMClient.chat/chat_stream`，触发 `LLM_OUTPUT`/`BEFORE_ITERATION` hook |
| persona | `AgentPersona.llm_config_name` 指向命名配置；`ProviderManager.get_client(config_name_override=...)` 按 persona 取 |
| storage | 配置存 `llm_configs`/`providers`，成本存 `cost_ledger`/`cost_budgets` |
| notifier | `cost.budget_exceeded` 事件触发告警 |

## 4. 本目录子文档

| 文档 | 内容 |
|---|---|
| [client.md](./client.md) | LLMClient 封装、缓存控制、retry/fallback、ProviderManager 路由限速、ConfigManager 多配置、Anthropic prompt caching |
| [cost.md](./cost.md) | CostManager 账本、三维定价、CostBudget 预算熔断、按模型/用户/persona 的成本归因 |

**相关实现**：[../../impl/llm/](../../impl/llm/)
