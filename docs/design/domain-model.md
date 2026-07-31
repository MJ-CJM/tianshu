# 01 领域模型与数据契约

## 1. 核心对象

### Edict

`Edict` 是用户下达的任务，也是整个执行链路的根对象。关键字段：

| 字段 | 作用 |
|---|---|
| `id`, `title`, `goal`, `context` | 任务身份和目标 |
| `status` | 业务状态，默认 `open` |
| `source`, `submitter`, `idempotency_key` | 来源、提交者、幂等去重 |
| `priority`, `schedule`, `dispatch` | 调度与通知策略 |
| `runtime` | 超时、迭代、并发、预算、权限 profile、网络白名单、生命周期 |
| `assigned_persona_id` | 直接指定执行 persona；非空时 Planner 跳过 LLM 规划 |
| `planner_persona_id` | 指定内阁规划 persona 和对应 LLM 配置 |
| `plan_review` | 规划需人工审批后再执行 |
| `acceptance` | 长任务 outer loop 验收标准；非空则走 orchestrator |
| `execution_profile` | `foreground`、`checkpointed`、`background` |

`EdictRuntime.lifecycle_phase` 是运行期生命周期，和 `Edict.status` 分离：

```text
active -> paused -> active
active -> winding_down -> complete
active -> complete
```

它用于 pause/resume、预算软着陆、winding_down 工具副作用拦截。

当前生命周期约束：

- `DELETE /api/edicts/{id}` 是归档（tombstone），不是物理删除。存在未结束执行时返回
  `409`；成功后列表隐藏任务，同时取消调度 job，但保留 Edict 身份、事件和治理证据。
- pause/resume/steer 只对仍有未结束 Memorial 的开放深度任务有效。pause 在当前外环轮次
  边界生效；steer 在下一轮 actor 边界吸收。
- 深度任务统一以可检查点模式运行；outer loop 或
  `execution_profile ∈ {checkpointed, background}` 不允许 `cron/interval`，也不允许
  `concurrency_policy=allow`。
- `conversation=true` 的交互任务完成后可保持 Edict 为 `open`，便于 follow-up；自动化入口可
  显式关闭 conversation，让成功任务自动结案。

### Memorial

`Memorial` 是一次执行记录。一个 `Edict` 可有多条 `Memorial`：初次执行、follow-up、DAG 节点、重试都可能产生新奏折。

关键字段：

| 字段 | 作用 |
|---|---|
| `edict_id`, `instruction` | 所属任务和本轮指令 |
| `status` | `submitted`、`planning`、`running`、`completed`、`failed`、`needs_review` 等 |
| `summary`, `result`, `error` | 执行输出和错误 |
| `usage` | token、cache read、成本、实际模型和上游 provider |
| `attempt`, `parent_memorial_id` | 自动重试链 |
| `dag_node_id`, `persona_id` | DAG 节点和执行人格 |
| `runtime_override`, `acceptance_override` | follow-up 单轮覆盖，不回写 Edict |
| `reasoning_content` | thinking-mode 模型多轮回传所需 reasoning |

失败、取消和成功是 Memorial 的真实终态。审计可以把成功结果推进到
`auditing/needs_review`，但不会把执行失败改写为成功；取消时已经发生的 LLM 用量仍会结算。

### Decree

当前人工授权权威是持久化 `Decision`；`Decree` 是旧接口兼容投影，用于工具审批、
旧式奏折审批和 L3 人工决策。用户可见统一称“裁决”。

### Plan / PlanTask

`Planner` 产出 `Plan`。单任务是 passthrough plan，多任务可转换成 DAG。每个 `PlanTask` 可以指定 `assigned_official`、依赖和所需工具。

### AcceptanceCriteria

`AcceptanceCriteria` 是长任务 outer loop 的触发器和验收契约：

| 子对象 | 作用 |
|---|---|
| `checks` | bash/lint/rubric 检查 |
| `critic` | 监督 persona、模型、严苛度、同类问题阈值 |
| `escalation` | L1/L2/L3 升级策略、会诊 persona、模型升级 |
| `min_outer_iterations`, `max_outer_iterations` | 最少优化轮数和最大外循环 |
| `deadline_seconds` | 时间预算 |
| `on_exhaustion` | 耗尽后升级、best effort 或失败 |
| `on_critic_unavailable` | critic 不可用时跳过或升级 |

## 2. 事件契约

事件模型由 `EventEnvelope` 承载，经 `EventBus` 分发并写入 `events` 表。当前主链路事件包括：

| 事件 | 生产者 | 消费者 |
|---|---|---|
| `edict.submitted` | Gateway / tool / channel | Scheduler |
| `edict.scheduled` | Scheduler | Planner |
| `plan.pending_review` | Planner | 前端/人工审批 |
| `plan.completed` | Planner / plan approve API | Executor |
| `execution.started` | Executor | 事件时间线 |
| `execution.completed` | Executor | Auditor, CostManager, MemoryManager |
| `execution.failed` | Executor | Notifier, CostManager |
| `audit.completed` | Auditor | Notifier, MemoryManager |
| `cost.budget_exceeded` | CostManager | Notifier |
| `outer_loop.*` | Orchestrator | Notifier / WebSocket / 审计时间线 |

设计上，事件不是简单日志，而是模块间的稳定协议。新增能力优先订阅事件，减少直接互相调用。

## 3. SQLite 控制面

`Storage.init_db()` 创建和迁移控制面表。当前重点表分组：

| 分组 | 表 |
|---|---|
| 任务核心 | `edicts`, `memorials`, `events`, `decrees` |
| DAG | `dag_executions`, `dag_nodes` |
| 记忆索引 | `memory_entries`, `memory_fts*` |
| 成本 | `cost_ledger`, `cost_budgets` |
| 配置 | `llm_configs`, `providers`, `engine_preferences`, `tool_switches` |
| 人格与 skill | `departments`, `personas`, `persona_metrics`, `skill_metrics` |
| 权限 | `session_rules`, `network_credentials` |
| 长任务 | `outer_loop_iterations`, `outer_loop_checkpoints`, `supervision_reports` |
| 通知可靠性 | `internal_notification_deliveries`（含 V24 per-channel acceptance progress） |
| 飞书 | `feishu_session_anchor`, `feishu_seen_messages`, `feishu_pending_cards`, `feishu_thinking_messages`, `channel_configs` |
| 实验插件清单 | `plugins`（metadata-only，不代表已加载） |

## 4. 数据源分层

| 数据 | 真相源 | 派生/缓存 |
|---|---|---|
| Edict/Memorial/Event/Decree | 主 SQLite | API 响应、前端页面 |
| Persona 打包默认 | `src/tianshu/resources/personas/{id}/` | 只读默认身份 |
| Persona 运行时覆盖 | `~/.tianshu/personas/{id}/` | PromptBuilder 读取 |
| Markdown 记忆 | `~/.tianshu/memory/` | `memory_entries` + FTS |
| Drawer 记忆 | `~/.tianshu/memory/drawers.sqlite3` | Prompt L1、memory-palace API |
| Skill 内容 | builtin/workspace/user skill dirs | `skill_metrics` |
| Provider/LLM 配置 | SQLite + env 初始值 | ProviderManager 客户端 |

这个分层的核心是：控制面强一致地放 SQLite，人格与长期记忆保留文件可读性，检索再建索引。

`cost_ledger` 自迁移 V23 起持久化 `cache_read_tokens`。`internal_notification_deliveries`
自 V24 起持久化 `accepted_channels_json`：某渠道已接受后，后续重试只发送尚未成功的渠道。

## 5. 任务归属与管理员边界

`Edict.submitter` 是任务资源的归属字段。普通 PAT 只能列出、读取或控制与自己 principal
ID 相同的任务，以及由该 Edict 派生的 Memorial、DAG、Scheduler job、决策和证据；访问
其他用户的资源统一表现为 `404`，避免泄露资源是否存在。`admin` scope 可跨提交者访问。

旧数据库里 `submitter IS NULL` 的任务不会对普通 PAT 开放；管理员和 trusted-local
主人可处理这些兼容数据。SystemAudit、全局审计/网络事件、Worker、全局配置、记忆和成本
属于管理面，不因“能访问自己的任务”而自动开放。

## 6. 执行主体本体论：百官（内臣） vs 客卿（外臣）

天枢有两类执行主体，**同为一等公民，但不同品类**。「客卿」一词取历史语义（客·卿：给外来人才卿的高位实权，但始终是「客」——外臣，非本国臣民），精确编码了二者的差别：

| | 百官（内臣） | 客卿（外臣） |
|---|---|---|
| 本质 | 天枢**治理本体自身**（六部官员/persona） | **外聘人才**（外部 coding agent，如 pi/claude-code/codex） |
| 人格 | 有 SOUL.md（人格）+ ROLE.md（职责），有身份/风格/记忆 | **无人格，只有能力声明**（`AgentCapabilities`）——不给外人「魂」 |
| 信任 | 内在可信，进程内运行 | **不可信**，进程外隔离（guard / 凭证网关 / worktree / clean-env） |
| 演化 | 作为天枢**自身**自进化（运行时 SOUL 演化） | 按**自己的节奏**演化（如 pi 发版周期）→ 故**钉死版本** + 漂移告警 |
| 考核 | **京察**（考「为官」本身） | **验收 / 配对评估**（只考**产出**：代码过没过，非官品） |

**承重推论**（这是设计方向，不代表所有隔离部件已落地）：
- 为何有 guard/网关/worktree/clean-env —— 客卿是**外**（不可信），百官是**内**（可信）。
- 为何钉死 pi 版本 + 漂移告警 —— 客卿按**自己节奏**演化，不受天枢自进化管。
- 为何客卿**无 SOUL** —— 不给外人你的魂；给它任务 + 验收标准，然后治理。
- 为何用验收而非京察 —— 评客卿**交付的代码**，不是它「为官是否称职」。

**分工**：百官治理（内阁规划 / 都察院审计 / 太医诊断 / 廷议评议 / 户部记账）· 客卿执行（写代码的手）。客卿在**功能上被抬高**（真正的 coding 交给它），但**本体论上仍是「外」**（被治理对象）。

> **当前实现边界**：客卿页面和路由属于实验能力，默认导航不展示。生产路径只报告 CLI
> 自管凭证；凭证网关未接入实际 executor，尝试开启会返回 `409`。worktree、guard、
> scoped-token 等完整组合仍是演进设计，不能把方案稿当作已验证安全边界。
