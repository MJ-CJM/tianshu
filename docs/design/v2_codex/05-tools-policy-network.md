# 05 工具、权限与外部网络

## 1. ToolRegistry

`ToolRegistry` 是工具注册和执行中心。每个工具有 `ToolDefinition`：

| 字段 | 作用 |
|---|---|
| `name` | OpenAI function name |
| `description` | 给模型看的工具说明 |
| `parameters` | JSON Schema 参数 |
| `tier` | T0-T4 权限等级 |
| `max_result_chars` | 工具结果截断上限 |
| `side_effect` | winding_down 阶段是否拦截 |

执行时会做：

1. 工具是否存在、是否被 admin 禁用。
2. tier 合法性校验，非法视作 T4。
3. winding_down 副作用工具拦截。
4. JSON 参数解析和 schema 校验。
5. T0 fast path 或 ToolHook before/after。
6. 执行异常转 `ToolResult(is_error=True)`。

## 2. Tool Tier

当前治理语义：

| Tier | 含义 |
|---|---|
| T0 | 只读，Agent/Registry fast path |
| T1 | 工作区内低风险写/读 |
| T2 | 外部网络、较高风险读取 |
| T3 | 写操作、敏感操作，通常需要审批 |
| T4 | 危险或未知，fail-secure 倾向 |

Tier 不是唯一判断，最终还要经过 PolicyEngine、session rules 和 ApprovalManager。

## 3. PolicyEngine

`tools/policy.py` 的设计原则：

- `PolicyContext`、`PolicyDecision` 是 frozen dataclass。
- `PolicyRule` 是 Protocol，便于组合和测试。
- 规则按 priority 降序执行。
- `deny` / `require_approval` 短路。
- 单条规则超时或异常会 abstain。
- 引擎整体超时或异常 fail-secure deny。
- 全部弃权时默认 allow。

默认规则由 `tools/policy_rules/build_default_rules()` 组装，覆盖 workspace boundary、bash safety、tier escalation、approval required list、network safety、default tier 等。

## 4. PolicyHook 与 Approval

Agent 在非 T0 工具调用前触发 `BEFORE_TOOL_CALL`：

```text
Agent
  -> PolicyHook(priority=5)
      -> PolicyEngine.evaluate
      -> allow / deny / require_approval
      -> session rule check / approval event
  -> ApprovalManager(priority=10)
      -> 等待人工批红结果
```

审批结果会写 `Decree`，也可以生成 session rule：

| scope | 含义 |
|---|---|
| `edict` | 当前 Edict 内复用 |
| `always` | 跨任务复用，受 `assert_can_grant` 限制 |

任务结束后，Executor 会清理本 Edict 的 edict-scope session rules。

## 5. PolicyProfile

`EdictRuntime.policy_profile` 是任务启动前的 proactive 权限预配，解决长任务频繁审批的问题。

内置模板：

| 模板 | 权限倾向 | 网络 profile |
|---|---|---|
| `safe-explore` | 只读探索 | OFFLINE |
| `refactor-in-place` | 工作区内改动，默认 | DEFAULT |
| `trusted-automation` | 可信自动化，较高自动批准 | RESEARCH |

Executor 启动单任务时会把 profile 展开成 edict-scope session rules，例如允许路径、bash prefix。硬约束是不能创建不安全的 always rule。

## 6. 鸿胪寺网络能力

外部网络工具位于 `tools/hongluisi/`，并通过 PromptBuilder 注入能力提示。主要工具：

| 工具 | 用途 |
|---|---|
| `web_fetch` | 读取网页并转 Markdown |
| `web_search` | 搜索 |
| `api_request` | 调用白名单 API |
| `web_extract` | Firecrawl 结构化抽取 |

网络 profile 由 `resolve_network_for_edict()` 决定，优先级是：

```text
Edict runtime override
  > system engine_preferences
  > PolicyProfile template network preset
```

## 7. 网络安全边界

`api_request` 的关键安全设计：

- `api_request_hosts` 是读方法 host 白名单。
- `api_request_write_hosts` 必须是 `api_request_hosts` 子集。
- 写方法 POST/PUT/DELETE/PATCH 需要额外白名单并触发审批。
- 禁止用户手动传 `Authorization`、`Cookie`、`X-Api-Key` 等认证 header。
- 凭证由系统按 host 注入，LLM 不可见。
- SSRF guard、rate limiter、host whitelist 三层保护。
- provider 凭证和 edict auth 凭证通过 `network_credentials` 区分。

## 8. 工具启停

工具启停状态持久化在 `tool_switches`。启动时 `app.py` 会读取禁用列表并应用到 ToolRegistry。

敕令管理工具 `submit_edict`、`list_edicts`、`get_edict_status` 默认随飞书通政司开关启停，避免助手默认具备自我下旨能力。
