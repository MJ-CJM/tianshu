# llm · 模型客户端与配置路由

> 契约层：调用方只面对 `LLMClient` + `UsageSummary`，所有 provider 差异在此吸收。

## 1. LLMClient 契约

`LLMClient` 是 LiteLLM 之上的薄封装，对外只暴露两个入口与一个统一返回类型。

| 方法 | 语义 |
|---|---|
| `chat(messages, tools)` | 阻塞式一次完成，返回单个 `LLMResponse` |
| `chat_stream(messages, tools)` | 异步生成器，逐 chunk yield，**末 chunk 携带完整 usage** |

返回类型 `LLMResponse`：`content` / `tool_calls`（`[{id,name,args}]`）/ `usage`(`UsageSummary`) / `reasoning_content`（thinking-mode 模型）/ `finish_reason`。

设计要点：**单轮 chat 与多轮编排解耦**。Client 不持有对话历史，不做 ReAct 循环，那是 Agent 的职责；Client 只保证「一次请求 → 一个结构化响应 + 准确 usage」。

## 2. Anthropic Prompt Caching

仅对 `claude*` / `anthropic/*` 模型生效，目标是命中 Anthropic 缓存省约 75% 输入 token。

| 决策 | 内容 |
|---|---|
| 标记位置 | 所有 `system` 消息 + 最后 3 条非 system 消息（user/assistant）的末尾 block |
| 标记内容 | `cache_control: {"type": "ephemeral"}` |
| 内容归一 | str / list / dict 统一转成 block list（`[{"type":"text","text":...}]`）后在最后一块插标记 |
| 非 Anthropic | 原样返回，零开销 |

为什么是「最后 3 条」：缓存断点数量有上限，且越靠近末尾的消息越可能被下一轮复用；system + 近 3 条覆盖了「长系统提示 + 滚动上下文头部」这两段最值钱的稳定前缀。

### 缓存断点策略细节

`_apply_prompt_caching` 的归一流程：先 `dict(m)` 浅拷每条消息（不改原 `messages`），给所有 `system` 消息打标，再取「非 system 消息」的尾部 3 条（限 `user`/`assistant`）打标。打标即 `_add_cache_control` 把 str/list/dict 内容统一转成 block list 后在末块插 `cache_control: {"type":"ephemeral"}`。断点是「稳定前缀」语义：只要前缀逐字不变就命中，任何前缀改写都会击穿后续所有缓存——所以断点要落在**不会回头改的位置**（system + 滚动窗口头部），而不是会被压缩重写的中段。

### 与压缩的交互

主循环三种压缩（见 [../agent/react-loop.md](../agent/react-loop.md) §2）都改 `messages`，与缓存断点是此消彼长的关系：

| 压缩 | 触发 | 对缓存的影响 |
|---|---|---|
| `micro_compact` | 每轮预防性收缩 | 只动尾部 tool 结果等可丢内容，**尽量不碰** system + 头部前缀，保住断点命中 |
| `auto_compact` | 接近阈值（`should_auto_compact`） | 重写中段历史 → 前缀变化击穿缓存，但换来上下文不溢出，是有意的取舍 |
| `reactive_compact` | 已溢出的最后救急 | 大改 messages，缓存必然失效；此时「不 400」优先于「省钱」 |

设计立场：**缓存是锦上添花，压缩是保命**。任何压缩都不为保缓存而妥协正确性；断点策略只负责在「未触发压缩的稳态轮次」里尽量多命中。

### 收益度量

省下的不是凭空估计，而是从上游 usage 真实回读。`_extract_cache_read_tokens` 按 provider 各取字段（claude `cache_read_input_tokens` / deepseek `prompt_cache_hit_tokens` / openai 兼容 `prompt_tokens_details.cached_tokens`），写入 `UsageSummary.cache_read_tokens`，再由 `estimate_cost` 用三维价（miss/hit/out，命中部分走 hit 折扣价）算回 `cost_cny`。即「省了多少」是按上游回显的命中 token × 折扣价精算的，不靠断点数量推断。

### provider 支持矩阵

| 模型族 | 断点注入 | 命中字段 | 说明 |
|---|---|---|---|
| `claude*` / `anthropic/*` | ✅ 注入 `cache_control` | `cache_read_input_tokens` | 唯一需要显式断点的 provider（`_is_anthropic_model` 判定） |
| deepseek | ❌ 不注入 | `prompt_cache_hit_tokens` | 上游自动缓存，本层不插断点，仅回读命中度量 |
| openai / 兼容 | ❌ 不注入 | `prompt_tokens_details.cached_tokens` | 同上，自动缓存 |
| 其它 | ❌ 不注入 | 0（保守） | 原样返回，零开销 |

即「断点注入」只对 Anthropic 生效；其余 provider 的缓存由上游自动管理，本层只负责**把命中度量回读进成本归因**。

## 3. 重试与 fallback

两层容错，职责分离：

| 层 | 机制 | 覆盖 |
|---|---|---|
| Client 内 | `tenacity` retry：指数退避 `min=1,max=4` × 3 次 | 仅 `RateLimitError / Timeout / ServiceUnavailableError`，`reraise=True` 让最终失败逃逸给上层 |
| Provider 间 | `ProviderManager` 选不到合适 provider → fallback 到 active 配置 | 配置缺失、限速超额、能力不匹配 |

只对「瞬态可重试错误」自动重试；4xx 这类确定性错误立即上抛，不浪费配额。

## 4. 模型回显校验

中转网关偶尔静默改写或降级模型，仅靠请求侧字段无法发现。Client 从响应提取上游真实回显并分级告警：

| 情况 | 日志级别 |
|---|---|
| 请求模型 base ≠ 回显模型 base | WARNING（疑似改写/降级） |
| 该路由首次见到 | INFO |
| 回显较缓存发生变化 | INFO |
| 路由 + 回显都没变 | DEBUG（避免日志风暴） |

进程级缓存 `(api_base, requested_model) → (actual, provider)` 用于降噪。回显字段写入 `UsageSummary.actual_model` / `upstream_provider`，供成本归因与审计。

## 5. ConfigManager · 多配置

线程安全的运行时配置管理，所有命名配置存 `llm_configs` 表，内存态用 `LLMConfigState`（frozen dataclass）。

| 能力 | 方法 |
|---|---|
| 启动加载 | 从 DB 载入全部命名配置，取 `is_active` 行为当前激活 |
| 取配置 | `state`（active）/ `get_config(name)` |
| 列举 | `list_configs() -> (list, active_name)` 供前端渲染 |
| 增改删 | `add_config` / `update_config` / `delete_config`（不可删 active、不可删最后一个） |
| 切换 | `set_active(name)` |

`AgentConfigState` 是另一组运行参数（迭代上限、超时、skill 预算、`fallback_llm_config_name`、平行位面/代码变体开关等），与 LLM 配置同由 ConfigManager 持有但独立更新。

**契约**：配置不可变。任何「修改」都是用新 `LLMConfigState` 替换字典中的引用，一把 `threading.Lock` 保护并发读写 —— 调用方拿到的 state 永远是某一时刻的一致快照。

## 6. ProviderManager · 路由与限速

包裹 `ConfigManager`（不替代），把命名配置同步成 `providers` 表行并做路由决策。

| 维度 | 契约 |
|---|---|
| 同步 | `sync_from_config` / `sync_all`：active 配置 `priority=0`，其余 `priority=100`，孤儿 provider 自动清理 |
| 选择 | `get_client(requirements, config_name_override)`：persona 覆盖 > 能力过滤 > 配额过滤 > 策略选择 > fallback active |
| 策略 | `priority`（默认，priority 小者先）/ `cheapest`（prompt 价最低） |
| 限速 | `rpm_limit` / `tpm_limit`，按 1 分钟滑窗，`record_usage` 累加、`_within_quota` 判定，超额则跳过该 provider |
| 容量描述 | `ProviderCapability`（chat/streaming/function_calling/vision/long_context）参与能力过滤 |

设计立场：**路由是降级链，不是负载均衡器**。任何环节选不到 provider 都退回 active 配置兜底，保证「永远能发出请求」优先于「选到最优 provider」。

### 选择决策树（`get_client`）

```text
config_name_override?  (persona.llm_config_name)
  → 命中且 enabled → 直接用该命名配置（per-task 路由，跳过所有过滤）
  → 未命中/disabled → WARNING，继续往下
list_providers() 取 status=active
  → 空 或 requirements is None → _fallback_client()（active 配置）
能力过滤：requirements.capabilities ⊆ provider.capabilities
配额过滤：_within_quota（RPM/TPM 滑窗未超）
  → 过滤后为空 → _fallback_client()
策略选择 _select(strategy)：priority(默认,小者先) / cheapest(prompt 价最低)
  → 选不到 → _fallback_client()
  → 选中 → 用其 api_base + active 配置的 api_key/采样参数建 client
```

### 何时中途换模型 vs fail-fast

两个层级，时机不同：

| 层 | 何时切 | 机制 |
|---|---|---|
| **请求前路由** | 发请求之前选 provider | `get_client` 决策树，配额超额/能力不匹配的 provider 在选择阶段就被剔除 |
| **运行中 fallback** | 主模型**已经报错**后 | Agent 循环捕获异常 → 若配了 `fallback_llm_config_name` 且本轮未用过 → 用 fallback 配置临时建 `LLMClient` 重发该轮 |

fail-fast 边界：`LLMClient.chat` 内的 `tenacity` 只对瞬态错误（RateLimit/Timeout/ServiceUnavailable）退避重试 3 次（见 §3），4xx 类确定性错误立即上抛；瞬态重试耗尽后才轮到 Agent 层 fallback。**context overflow 不走 fallback**——它先尝试 `reactive_compact` 压缩重试，压不下去才以 `context_overflow` 收尾（换模型救不了上下文超长）。fallback 每个任务**只切一次**（`recovery_attempts` 里 `"fallback"` 作 guard），fallback 也失败则 `ExitReason.LLM_ERROR`，错误信息同时带主/备两次失败原因。

### per-task 路由与动态升降级

- **per-task 路由**：persona 的 `llm_config_name` 透传成 `config_name_override`，让「某类任务/某个人格固定用某模型」——它**短路**能力/配额/策略全部过滤，是显式优先级最高的路由信号。
- **动态升降级（provider 维度）**：`sync_*` 把 active 配置同步为 `priority=0`、其余 `priority=100`，配合 `priority` 策略实现「active 优先、其余兜底」的隐式降级链；`record_usage` 累加用量、`_within_quota` 按 1 分钟滑窗判定超额，超额 provider 在下次 `get_client` 被自动跳过（临时降级），窗口过期自动恢复（升级）。这是**容量驱动**的升降级，非人工。

### 与 tier override 的集成

模型路由（选哪个 provider）与工具治理的 tier override（某次工具调用要不要升级审批，见 [../tools/policy.md](../tools/policy.md) §2）是**两条正交的链路**：前者作用于 LLM 请求侧、由 `ProviderManager` 决策；后者作用于工具执行侧、由 `PolicyEngine` 决策，二者不共享状态。唯一交汇点是 Agent 循环：同一轮里先经请求前路由选出 client 发 LLM 请求，拿到 tool_calls 后再各自过 `BEFORE_TOOL_CALL` 的 policy（含 `tier_overrides`）。换言之 fallback 切模型**不影响**已生效的 tier override，反之亦然——治理决策按工具/敕令绑定，与底层用了哪个 provider 无关。

## 7. drop_params

请求 kwargs 固定带 `drop_params=True`，让 LiteLLM 自动丢弃目标 provider 不支持的参数（如 Anthropic 不吃 `top_p`），避免为每家 provider 手写参数白名单。

**相关实现**：[../../impl/llm/](../../impl/llm/)
