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

### Decree

`Decree` 是人工批红记录，用于工具审批、旧式奏折审批和 L3 人工决策。它把“人是否授权”变成可持久化、可审计的数据。

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
| 飞书 | `feishu_session_anchor`, `feishu_seen_messages`, `feishu_pending_cards`, `feishu_thinking_messages`, `channel_configs` |
| 插件 | `plugins` |

## 4. 数据源分层

| 数据 | 真相源 | 派生/缓存 |
|---|---|---|
| Edict/Memorial/Event/Decree | 主 SQLite | API 响应、前端页面 |
| Persona 模板 | `personas/{id}/` | `personas` 表、运行时目录 |
| Persona 运行时覆盖 | `~/.tianshu/personas/{id}/` | PromptBuilder 读取 |
| Markdown 记忆 | `~/.tianshu/memory/` | `memory_entries` + FTS |
| Drawer 记忆 | `~/.tianshu/memory/drawers.sqlite3` | Prompt L1、memory-palace API |
| Skill 内容 | builtin/workspace/user skill dirs | `skill_metrics` |
| Provider/LLM 配置 | SQLite + env 初始值 | ProviderManager 客户端 |

这个分层的核心是：控制面强一致地放 SQLite，人格与长期记忆保留文件可读性，检索再建索引。
