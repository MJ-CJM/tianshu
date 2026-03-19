# 天枢（Tianshu）全局架构设计

> 异步 AI 执行平台。白天下旨，夜间办差，早上递折子。

---

## 一、系统目标与设计原则

### 1.1 产品定位

天枢不是聊天 UI，也不是固定流程编排器，而是一个面向复杂任务的异步 AI 执行中枢：

- 用户以自然语言下发任务
- 系统将任务标准化为可治理的 `Edict`
- 执行链路支持规划、执行、审计、通知、复核
- 结果沉淀为可追踪、可复盘、可重跑的 `Memorial`

一句话概括：

```
下旨 -> 排期 -> 办差 -> 稽核 -> 递折 -> 批红
```

### 1.2 设计原则

1. **先闭环，后扩展**：Phase 0 先跑通单 Agent + 工具调用，再逐步拆分为独立模块。
2. **先契约，后模块**：先定义数据模型、状态机、事件和权限边界，再设计各“部院”。
3. **默认可审计**：每次提交、执行、审计、通知都必须有结构化记录。
4. **显式治理优先于隐式智能**：预算、审批、权限、重试、人工复核不交给 LLM 自行猜测。
5. **外层古风，内层现代**：命名服务叙事，不干扰工程实现。

### 1.3 命名原则

| 外层（用户可见） | 内层（代码实现） | 说明 |
|-----------------|-----------------|------|
| 诏令 | `Edict` | 任务输入统一模型 |
| 奏折 | `Memorial` | 任务结果统一模型 |
| 批红 | `Decree` | 用户/审批人反馈 |
| 御案台 | `Gateway` | 任务接入层 |
| 内阁 | `Planner` | 预规划与拆解层 |
| 兵部 | `Executor` | 执行引擎 |
| 都察院 | `Auditor` | 审计与风控层 |
| 通政司 | `Notifier` | 渲染与通知层 |
| 文渊阁 | `Memory` | 记忆与检索层 |
| 户部 | `CostManager` | Token/成本治理层 |

### 1.4 六部治理职责映射

天枢借鉴明朝六部分治制度，将系统治理职责分为六大类。这不是六个独立模块，而是一张**职责归属矩阵**——每项治理职责都有明确的 owner，避免散落无主。

| 六部 | 治理职责 | 落点（模块 / 机制） | 说明 |
|------|---------|-------------------|------|
| **吏部** | Agent / Skill 注册、权限身份、能力声明、配额管理 | `ToolRegistry`（工具注册）、`Security Model`（权限）、`Executor.tool_filter`（能力裁剪）、Phase 2 统一 `PluginApi` [OpenClaw-7]、Phase 3 `AgentManager` | 不独立建模块；Phase 0 由 `ToolRegistry` 承担注册；Phase 2 通过统一 `PluginApi` 管理所有能力注册（§6.4）；Phase 3 多 Agent 时再考虑独立 `AgentManager` |
| **户部** | Token 成本、API 配额、预算熔断、资源账本 | `CostManager`（Phase 2 独立）、Phase 0 内嵌于 `Agent` 的 usage 统计 | 不只统计 token/cost，还管 API 调用配额和资源分配 |
| **礼部** | Prompt 模板、输出规范、汇报格式、通道内容适配 | `Notifier.renderer`（渲染管线）、`Skills`（能力包含 prompt）、System Prompt 管理 | Prompt 工程和输出规范是"礼部"职能，分散在 Notifier 渲染层和 Skills 体系中 |
| **兵部** | 执行引擎、Agent Loop、工具调用、并发调度 | `Executor`（核心 owner） | 直接对应，Phase 0 即 `agent.py` |
| **刑部** | 失败处理、异常升级、风险封禁、安全策略执行 | `Auditor.rules`（规则引擎）、错误恢复策略（§4.5）、工具权限分级 `T3` 拦截（§4.4） | 刑部职能由都察院的规则引擎和错误恢复策略共同承担，不独立建模块 |
| **工部** | 工具链、工作区、环境隔离、存储、基础设施建设 | `ToolRegistry`（工具链）、`Executor.workspace`（工作区隔离）、`Storage`（持久化）、`ConfigManager` | 基础设施类职能，分散在多个共享服务中 |

> **设计决策**：六部是职责分类框架，不是模块切分方案。遵循 `project-analysis.md` 的警告——"不要为了凑齐朝廷结构而硬拆模块"。每项职责挂靠到最自然的 owner 上，而不是为每个部硬造一个包。

**官员映射（Phase 1+）**

制度层定义"做什么"，官员层定义"谁来做、以什么风格做"。每个部院配置一位专属官员（Agent Persona），以人格化的方式承接该部院的治理职责。详见 `agent-persona.md`。

| 部院 | 官员 | 官员 ID | 引入阶段 |
|------|------|---------|---------|
| 内阁 | 内阁首辅 | `neige` | Phase 1 |
| 兵部 | 兵部尚书 | `bingbu` | Phase 1 |
| 都察院 | 都察院左都御史 | `ducha` | Phase 1 |
| 通政司 | 通政使 | `tongzheng` | Phase 1 |
| 文渊阁 | 文渊阁大学士 | `wenyuan` | Phase 2 |
| 户部 | 户部尚书 | `hubu` | Phase 2 |

### 1.5 运行阶段矩阵

| Phase | 目标 | 运行方式 | 新增能力 | Persona 能力 | 系统真相来源 |
|------|------|---------|---------|-------------|-------------|
| Phase 0 | 跑通最小闭环 | Web 服务进程（FastAPI + Uvicorn） | 单 Agent、ReAct、基础工具、Skills、SQLite、Docker | 单一通用 prompt | SQLite |
| Phase 1 | 引入治理与异步调度 | Web 服务 + 事件驱动调度 | `Scheduler`、`EventBus`、`Planner`、`Auditor`、`Notifier`、人工复核 | 4 官员 persona 注入 + 文件级记忆 | SQLite + 事件日志 |
| Phase 2 | 引入平台化能力 | 平台化 Web 服务 | `Memory`、`CostManager`、多 Provider、多通道通知、`PluginApi` | 6 官员 + 完整 R/R/R 记忆循环 | SQLite |
| Phase 3 | 多 Agent 与分布式扩展 | 容器集群 / K8s / Temporal | DAG、多 Agent 并发、PostgreSQL、水平扩缩容 | 多官员并发实例 + 会商协议 | PostgreSQL + Durable Workflow |

### 1.6 技术选型

| 层次 | 选型 | 说明 |
|------|------|------|
| 语言 | Python 3.12+ | asyncio 原生支持，生态完整 |
| 数据模型 | Pydantic v2 | `Edict` / `Memorial` / 事件的强类型建模 |
| LLM 接入 | LiteLLM | 统一 Provider 接口，便于后续路由 |
| API | FastAPI + Uvicorn | Phase 0 起作为主入口 |
| CLI | typer | 可选辅助工具（非主入口） |
| 定时调度 | APScheduler 4.x | Phase 1 起提供即时 / 一次性 / cron 调度 |
| 配置 | Pydantic Settings | 环境变量 + YAML |
| Phase 0 存储 | SQLite | Web 并发安全 |
| Phase 3 存储 | PostgreSQL | 多实例一致性与查询能力 |
| 容器化 | Docker | Phase 0 起支持容器化部署 |

---

## 二、端到端执行流

### 2.1 Phase 0 最小闭环

```
HTTP 请求
  -> 构建 Edict
  -> Agent ReAct Loop
  -> 工具调用
  -> 生成 Memorial
  -> HTTP 响应 + SQLite 持久化
```

Phase 0 有四个核心组件：

- `Gateway API`：FastAPI 接收 HTTP 请求并构建 `Edict`
- `Agent`：通过 ReAct 循环边规划边执行
- `LLM Client`：封装 LiteLLM 调用与工具协议
- `Skills Loader`：扫描并加载 SKILL.md，注入 Agent 系统提示

Phase 0 不引入独立 `Planner`、`EventBus`、`Scheduler`，流程保持同步串行。

### 2.2 Phase 1+ 完整执行链

```
Edict -> Scheduler -> Planner -> Executor -> Auditor -> Notifier
                               \-> CostManager
                               \-> Memory (终态后)
```

职责顺序如下：

1. `Gateway API` 接收请求，创建 `Edict`
2. `Scheduler` 决定立即执行、定时执行或周期执行
3. `Planner` 将复杂任务拆为结构化子任务
4. `Executor` 执行任务、发出过程事件
5. `Auditor` 对执行过程做规则审计与可疑项复核
6. `Notifier` 渲染状态与结果，投递到 Web 端或外部通道
7. `Memory` 和 `CostManager` 消费终态结果与运行数据，沉淀知识和成本账本

### 2.3 Planner 启用策略

`Planner` 不是所有任务都必须经过的一层。默认策略如下：

- **Phase 0**：一律不启用独立 `Planner`
- **Phase 1+ 必须预规划**：
  - 定时或周期任务
  - 明确要求复核的任务
  - 多步骤、跨工具、可并行的复杂任务
- **Phase 1+ 可直接执行**：
  - 简单即时任务
  - 明确只需 1-2 次工具调用的任务

`Planner` 输出的始终是结构化计划，而不是直接执行结果。

---

## 三、运行时公共契约

### 3.1 任务生命周期状态机

天枢统一使用以下状态机，不允许模块各自发明状态：

```
SUBMITTED -> SCHEDULED -> PLANNING -> RUNNING -> AUDITING -> COMPLETED
                                          |            |
                                          |            -> NEEDS_REVIEW
                                          |
                                          -> FAILED
                                          -> CANCELLED
```

状态含义如下：

| 状态 | 说明 | 进入方 |
|------|------|--------|
| `SUBMITTED` | 已接收、已生成 `Edict` | Gateway API |
| `SCHEDULED` | 已交给调度器，等待触发 | Scheduler |
| `PLANNING` | 正在做结构化拆解 | Planner |
| `RUNNING` | 正在执行工具与 LLM 调用 | Executor |
| `AUDITING` | 已有执行结果，正在做规则/复核审计 | Auditor |
| `COMPLETED` | 完成且可对外汇报 | Executor / Auditor |
| `FAILED` | 执行失败或被策略阻断 | Executor / Auditor |
| `CANCELLED` | 被用户或系统取消 | Gateway API / Executor |
| `NEEDS_REVIEW` | 需要人工批红后才能继续 | Auditor |

### 3.2 Edict（诏令）契约

`Edict` 是任务输入的唯一标准形态。外部所有输入都必须先落成 `Edict`。

**核心字段**

| 字段 | 类型 | 说明 | 引入阶段 |
|------|------|------|---------|
| `id` | `str` | ULID，全局唯一 ID | Phase 0 |
| `idempotency_key` | `str` | 幂等键；用于防止重复提交 | Phase 1 |
| `goal` | `str` | 任务目标 | Phase 0 |
| `context` | `str \| None` | 额外上下文 | Phase 0 |
| `source` | `Literal["cli","api","channel","scheduler"]` | 提交来源 | Phase 1 |
| `submitter` | `str \| None` | 提交人或外部主体 ID | Phase 1 |
| `constraints` | `list[str]` | 禁止事项、范围约束 | Phase 1 |
| `output_format` | `str \| None` | 期望输出格式 | Phase 1 |
| `priority` | `Literal["urgent","normal","low"]` | 优先级 | Phase 1 |
| `review_policy` | `Literal["never","on_failure","on_flag","always"]` | 复核策略 | Phase 1 |
| `schedule` | `EdictSchedule` | 调度策略 | Phase 1 |
| `dispatch` | `EdictDispatch` | 结果分发策略 | Phase 1 |
| `runtime` | `EdictRuntime` | 运行时约束 | Phase 1 |
| `metadata` | `dict[str, Any]` | 扩展字段 | Phase 1 |
| `created_at` | `datetime` | 创建时间 | Phase 0 |

**子结构**

`EdictSchedule`

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | `Literal["immediate","once","cron"]` | 即时、一次性、周期 |
| `at` | `datetime \| None` | 一次性定时触发时间 |
| `cron` | `str \| None` | cron 表达式 |
| `timezone` | `str` | IANA 时区 |

`EdictDispatch`

| 字段 | 类型 | 说明 |
|------|------|------|
| `channels` | `list[str]` | 目标通道，如 `console`/`feishu`/`email` |
| `mode` | `Literal["stream","final"]` | 流式推送或仅最终结果 |
| `notify_on_failure` | `bool` | 失败时是否主动通知 |
| `target` | `dict[str, str]` | 通道目标，如用户 ID、会话 ID |

`EdictRuntime`

| 字段 | 类型 | 说明 |
|------|------|------|
| `timeout_seconds` | `int` | 单次任务总超时 |
| `max_iterations` | `int` | ReAct 最大轮数 |
| `max_concurrency` | `int` | 子任务并发上限 |
| `retry_limit` | `int` | 自动重试次数 |
| `token_budget` | `int \| None` | Token 预算 |
| `cost_budget_usd` | `float \| None` | 成本预算 |
| `approval_required_tools` | `list[str]` | 需要人工批准的高风险工具 |

### 3.3 Memorial（奏折）契约

`Memorial` 是任务执行结果和运行轨迹的统一载体，也是后续通知、复盘、记忆沉淀的基础。

| 字段 | 类型 | 说明 | 引入阶段 |
|------|------|------|---------|
| `id` | `str` | ULID，当前尝试的结果 ID | Phase 0 |
| `edict_id` | `str` | 关联 `Edict` | Phase 0 |
| `status` | `TaskStatus` | 任务状态 | Phase 0 |
| `attempt` | `int` | 当前是第几次尝试 | Phase 1 |
| `parent_memorial_id` | `str \| None` | 重试时指向上一份 `Memorial` | Phase 1 |
| `summary` | `str \| None` | 最终摘要 | Phase 0 |
| `result` | `str \| None` | 最终正文或结构化输出 | Phase 0 |
| `review_status` | `Literal["not_required","pending","approved","rejected"]` | 复核状态 | Phase 1 |
| `audit` | `AuditResult \| None` | 审计结论 | Phase 1 |
| `artifacts` | `list[ArtifactRef]` | 产物清单，如文件、链接、报告 | Phase 1 |
| `usage` | `UsageSummary` | Token/成本/耗时统计 | Phase 0 |
| `timeline` | `list[TimelineItem]` | 生命周期时间线 | Phase 1 |
| `error` | `str \| None` | 失败原因 | Phase 0 |
| `created_at` | `datetime` | 创建时间 | Phase 0 |
| `started_at` | `datetime \| None` | 开始执行时间 | Phase 0 |
| `completed_at` | `datetime \| None` | 结束时间 | Phase 0 |

`Memorial` 是系统对外展示的“任务真相”。`Notifier` 只能渲染它，不能替代它。

### 3.4 Decree（批红）契约

`Decree` 表示用户或审批人对 `Memorial` 的反馈。Phase 1 起引入。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | ULID |
| `memorial_id` | `str` | 关联的 `Memorial` |
| `action` | `Literal["approve","reject","retry","amend","cancel"]` | 批红动作 |
| `comment` | `str \| None` | 批注 |
| `amended_goal` | `str \| None` | `amend` 时的追加指令 |
| `actor` | `str` | 触发人 |
| `created_at` | `datetime` | 创建时间 |

**批红决策链路**

批红不只是一个数据模型，而是一条完整的治理闭环：

```
Auditor 输出 flag/block
  -> Memorial 进入 NEEDS_REVIEW
  -> 通政司将待批奏折推送给用户（按优先级排序）
  -> 用户下达 Decree
  -> 系统消费批红并恢复流转
```

各批红动作的系统响应：

| 动作 | 系统行为 | Memorial 状态变迁 |
|------|---------|------------------|
| `approve` | 放行，继续通知和记忆沉淀 | `NEEDS_REVIEW` → `COMPLETED` |
| `reject` | 标记失败，记录驳回原因 | `NEEDS_REVIEW` → `FAILED` |
| `retry` | 创建新 Memorial（`attempt+1`，`parent_memorial_id` 指向当前），重新进入执行 | `NEEDS_REVIEW` → （原件不变）；新 Memorial `RUNNING` |
| `amend` | 基于 `amended_goal` 创建新 Edict，启动全新执行流 | `NEEDS_REVIEW` → `CANCELLED`；新 Edict `SUBMITTED` |
| `cancel` | 终止任务 | `NEEDS_REVIEW` → `CANCELLED` |

Phase 0 不引入批红机制（无 NEEDS_REVIEW 状态）。Phase 1 起，待批奏折由通政司维护推送队列，按 `priority` 和等待时长排序，超时未批红的可配置自动升级策略。

**执行中实时审批**（Phase 1+ 引入）[OpenClaw-3]

批红不仅发生在任务完成后（事后复核），也可以发生在**执行过程中**——Agent 调用 `T3` 高风险工具时暂停等待批红：

1. Agent Loop 的 Acting 阶段检测到 tool_call 命中 `T3` 或 `Edict.runtime.approval_required_tools`
2. 暂停执行，通过通政司将审批请求推送到用户通道
3. 用户下达实时批红：`allow-once`（单次放行）或 `allow-always`（本任务内永久放行）
4. 收到批红后恢复执行；超时未批红则跳过该工具调用，将"未获批准"作为 Observation 返回 LLM

安全约束：审批字段必须从系统内部生成，不得从用户输入中直接解析，防止通过注入 `approved` 字段绕过审批。

### 3.5 事件信封契约

Phase 1 起，所有内部领域事件都必须带统一信封。即便暂时使用内存 `EventBus`，也要有统一结构，避免后续迁移时重新发明协议。

**标准事件信封**

| 字段 | 类型 | 说明 |
|------|------|------|
| `event_id` | `str` | ULID |
| `event_type` | `str` | 事件类型 |
| `edict_id` | `str` | 关联任务 |
| `memorial_id` | `str \| None` | 关联执行结果 |
| `attempt` | `int \| None` | 关联重试序号 |
| `timestamp` | `datetime` | 事件时间 |
| `producer` | `str` | 事件生产模块 |
| `payload` | `dict[str, Any]` | 业务内容 |

**核心事件表**

| 事件 | 生产者 | 主要消费者 | 持久化要求 |
|------|--------|-----------|-----------|
| `edict.submitted` | Gateway API | Scheduler | Phase 1 起写事件日志 |
| `edict.scheduled` | Scheduler | Planner / Executor | Phase 1 起写事件日志 |
| `plan.completed` | Planner | Executor | Phase 1 起写事件日志 |
| `execution.started` | Executor | Auditor / Notifier / CostManager | Phase 1 起写事件日志 |
| `llm.requested` | Executor | Auditor / CostManager | Phase 2 起写事件日志 |
| `llm.responded` | Executor | Auditor / CostManager | Phase 2 起写事件日志 |
| `tool.started` | Executor | Auditor | Phase 1 起写事件日志 |
| `tool.completed` | Executor | Auditor | Phase 1 起写事件日志 |
| `cost.budget_exceeded` | CostManager | Executor / Auditor | Phase 2 起写事件日志 |
| `audit.completed` | Auditor | Notifier / Memory | Phase 1 起写事件日志 |
| `execution.completed` | Executor | Auditor / Notifier / Memory | Phase 1 起写事件日志 |
| `execution.failed` | Executor | Auditor / Notifier / Memory | Phase 1 起写事件日志 |
| `execution.cancelled` | Executor / Gateway | Auditor / Notifier / Memory | Phase 1 起写事件日志 |
| `notification.sent` | Notifier | Auditor | Phase 2 起写事件日志 |

约束如下：

- 同一 `memorial_id` 内的事件顺序必须保持追加顺序
- 事件投递语义为 **at-least-once**
- 任何消费者都不得把自身处理结果当作系统真相覆盖 `Memorial`

### 3.6 幂等、重试、取消与持久化

#### 幂等规则

- `Gateway` 以 `(submitter, idempotency_key)` 作为去重主键
- 同一幂等键在未终态前再次提交，应返回现有任务状态，不创建新运行
- API 调用若未显式传入幂等键，则默认使用生成的 ULID，不做去重

#### 重试规则

- 自动重试只在 `runtime.retry_limit` 范围内进行
- 每次重试创建新的 `Memorial`
- 新的 `Memorial.attempt` 自增，`parent_memorial_id` 指向上一尝试
- 安全策略阻断、人工拒绝、配置错误不自动重试

#### 取消规则

- 取消是显式状态，不等价于失败
- 用户取消后应尽快停止未开始子任务，并对进行中的工具调用做最佳努力终止
- 取消必须生成 `execution.cancelled` 事件并更新 `Memorial.status`

#### 持久化规则

**Phase 0**

- SQLite 为系统真相来源（edicts、memorials、events 表）
- 提交时立即写入 `edicts` 表
- 进入执行时立即创建 `memorials` 记录，状态为 `RUNNING`
- 执行过程以事件记录追加写入 `events` 表
- 终态时回写 `memorials` 记录
- `.tianshu/logs/` 保留用于调试日志和大产物

**Phase 1**

- 调度元数据写入 SQLite `scheduler_jobs` 表
- 事件日志继续追加写入 SQLite `events` 表
- `Memorial` 仍是对外结果真相

**Phase 3**

- PostgreSQL 成为统一真相来源，替代 SQLite
- 支持多实例并发读写

---

## 四、核心执行引擎

### 4.1 Agent Loop（Phase 0 核心）

Agent Loop 采用 ReAct（Reasoning + Acting）模式 [NanoBot-1] [DeepAgents-1]。

```
Thinking -> Acting -> Observing -> Thinking -> ... -> Done / Failed
```

状态定义：

| 状态 | 触发条件 | 行为 |
|------|---------|------|
| `Thinking` | 开始执行或收到工具结果后 | 将完整消息历史发给 LLM，等待决策 |
| `Acting` | LLM 返回 `tool_calls` | 顺序执行工具调用 |
| `Observing` | 工具执行完成 | 将结果追加到消息历史 |
| `Done` | LLM 返回最终答案，无 `tool_calls` | 生成最终输出 |
| `Failed` | 超限、超时、不可恢复异常 | 返回失败结果 |

**Phase 1+ Persona 注入**

Phase 1 起，Agent Loop 在 Thinking 阶段之前增加 Persona 注入步骤：根据任务分配的官员 ID 加载对应的 `SOUL.md` + `ROLE.md`，构建角色化 system prompt。无 Persona 时回退到通用提示（向后兼容 Phase 0）。详见 `agent-persona.md` §4.2 注入顺序。

### 4.2 消息历史管理

**阶段策略**

- **Phase 0**：仅做追加，不压缩
- **Phase 1**：引入 Compaction（会话压缩），当上下文接近窗口上限时自动触发
- **Phase 2**：引入 `Memory` 后，长期经验与短期对话分层管理

**消息角色**统一为 `system` / `user` / `assistant` / `tool`。若 Provider 返回 `reasoning_content`，原样保留在对话历史中，避免后续轮次缺失导致 Provider 拒绝请求。

**Compaction 策略**（Phase 1+ 引入）[OpenClaw-1]

当消息历史的 Token 总量接近模型上下文窗口时，触发压缩：

1. **分片压缩**：将历史消息按时间段分片，每片独立用 LLM 生成摘要
2. **标识符保留**：压缩时严格保留 UUID、hash、文件路径等关键标识符，避免摘要丢失引用
3. **合并摘要**：多个分片摘要合并为一份统一的上下文摘要
4. **最近保留**：保留最近 N 轮消息不压缩（确保最新交互完整）
5. **Context Window Guard**：监控 Token 使用量，低于硬性最低值时拒绝继续执行，强制结束或触发二次压缩

### 4.3 工具调用协议

工具调用使用 LLM function calling 协议：

1. 启动时注册工具定义（名称、描述、JSON Schema）
2. LLM 通过 `tool_calls` 请求调用
3. `Executor` 查 `ToolRegistry` 执行
4. 结果以 `tool` 消息追加回消息历史

单轮多个 `tool_call` 的策略：

- **Phase 0**：顺序执行
- **Phase 1**：仍默认顺序执行，除非来自结构化计划的并行子任务
- **Phase 3**：支持并发执行与聚合回传

### 4.4 工具权限分级

工具必须按风险分级，而不是只按功能注册：

| 级别 | 类型 | 示例 | 默认策略 |
|------|------|------|---------|
| `T0` | 只读工具 | `read_file`、`list_dir`、`web_search` | 允许 |
| `T1` | 工作区内可写工具 | `write_file`、`edit_file` | 允许，但限制工作区 |
| `T2` | 外部副作用工具 | `exec_command`、外部 HTTP、消息发送 | 允许，但必须记录审计事件 |
| `T3` | 高风险或破坏性工具 | 删除、部署、支付、系统级修改 | 默认禁止，需人工批准 |

**规则**

- 子任务执行时必须做工具裁剪，避免递归调用元工具 [NanoBot-3]
- 文件工具默认限制在工作区白名单内 [PicoClaw-1]
- `T3` 工具只能在显式批准后执行（见下文"执行中实时审批"）

**多层 Policy Pipeline**（Phase 1+ 引入）[OpenClaw-2]

T0-T3 是工具的固有风险等级，Policy Pipeline 决定在具体执行场景下哪些工具可用。多层策略依次过滤，后层覆盖前层：

```
全局默认策略（T0-T3 风险分级）
  → 通道级覆盖（如：飞书来源禁用 shell 工具）
    → 任务级覆盖（Edict.runtime.approval_required_tools）
      → Agent 级裁剪（子任务排除元工具）
```

安全兜底：如果过滤后工具集为空或只剩插件工具而无核心工具，自动回退到全局默认策略，防止误禁核心能力。

### 4.5 错误恢复策略

| 场景 | Phase 0 策略 | 后续增强 |
|------|-------------|---------|
| 单轮 LLM 失败 | 指数退避重试，最多 3 次 | Phase 2 按 Provider 能力切换备用模型 |
| 工具异常 | 作为 `Observation` 反馈给 LLM，由 Agent 决策下一步 | 规则化工具级重试 |
| 超过 `max_iterations` | 强制失败 | 可配置告警与复核 |
| 预算超限 | Phase 0 仅记录 | Phase 2 熔断并发出 `cost.budget_exceeded` |
| 安全策略违规 | 立即失败，不重试 [ZeroClaw-1] | 保持不变 |

---

## 五、核心模块职责与边界

### 5.1 御案台（Gateway）

**职责**

- 接收 HTTP API / Channel 输入
- 标准化为 `Edict`
- 做基本权限校验、幂等检查、输入校验

**边界**

- 不负责任务拆解
- 不直接执行业务工具
- 不保存除 `Edict` 之外的任务真相

**阶段**

- Phase 0：FastAPI Gateway API（HTTP 接入）
- Phase 1+：扩展 Scheduler 集成和 Channel 接入
- 可选：typer CLI 作为辅助工具（调用 Gateway API）

### 5.2 内阁（Planner）

内阁不只是"任务拆解器"，而是天枢的**总调度中枢**——理解目标、拆任务、排优先级、选 Agent/Skill、协调冲突、汇总分派。对应明朝内阁首辅"票拟"的完整职能。

**职责**

- 将复杂目标拆成结构化子任务
- 标注依赖关系、可并行性、预算预估
- 过滤不必要上下文，减少执行污染 [DeepAgents-2] [DeepAgents-3]
- **排定子任务优先级**：结合 Edict.priority 和任务依赖关系，决定执行顺序
- **选择 Agent 和工具集**：根据子任务类型匹配最合适的 Skill 和工具组合（Phase 2+ 可查询文渊阁历史经验辅助选择）
- **资源冲突协调**：当多个 Edict 竞争同一资源（如同一工作区、同一外部 API 配额）时，由 Planner 协调分配，而非让 Executor 自行抢占
- **官员选择**（Phase 1+）：根据子任务类型和职责映射，为每个子任务指定执行官员（`assigned_official`），如规划类分配给内阁，执行类分配给兵部，审计类分配给都察院

**输出契约**

`Plan` 至少包含：

- `tasks`：子任务列表
- `depends_on`：依赖关系
- `tools_required`：所需工具集
- `skills_required`：所需 Skill（Phase 2+）
- `can_run_parallel`：是否可并行
- `estimated_tokens`：预估 Token 用量
- `priority_order`：建议执行顺序
- `assigned_official`：各子任务指定的执行官员 ID（Phase 1+）

**边界**

- 只规划，不直接调用业务工具
- 不替代审计判断
- 不直接执行批红决策（批红由通政司推送、用户裁决）

### 5.3 兵部（Executor）

**职责**

- 运行 ReAct Loop
- 执行计划中的子任务
- 发出 `execution.*`、`tool.*`、`llm.*` 事件
- 生成 `Memorial`

**边界**

- 不负责审批结论
- 不负责对外消息格式渲染
- 不直接决定长期记忆写入策略

**演进**

- Phase 0：单 Agent 串行
- Phase 1：按计划顺序执行子任务
- Phase 3：并发 Worker + DAG 调度，采用 Lane-based 并发控制 [OpenClaw-4]——每个 Edict 一条 session lane（保证任务内串行），加一条 global lane（全局背压控制），既避免全局锁的粗粒度，又防止无限并发

### 5.4 都察院（Auditor）

**职责**

- 订阅执行事件，做独立审计
- 检查越权、预算、目标偏离、工具风险、结果异常
- 输出 `pass / flag / block`

**两层审计**

1. **规则引擎**：同步快速判断，覆盖硬规则
2. **LLM 复核**：仅对可疑项做结构化复核 [NanoBot-5]

**结果语义**

| 结论 | 含义 | 后续动作 |
|------|------|---------|
| `pass` | 允许继续流转 | 进入通知/沉淀 |
| `flag` | 可继续但需人工关注 | 标记 `NEEDS_REVIEW` 或高亮通知 |
| `block` | 不允许继续自动流转 | 终止或挂起等待批红 |

**核心生命周期钩子集** [OpenClaw-5]

都察院、文渊阁、户部等模块通过钩子接入 Agent 执行流，而非硬编码到 Agent Loop 中。天枢定义以下核心钩子，每个钩子有明确的 Event / Context / Result 类型：

| 钩子 | 触发时机 | 典型消费者 | 能力 | 引入阶段 |
|------|---------|-----------|------|---------|
| `before_agent_start` | Agent 开始执行前 | 文渊阁（检索历史经验）、户部（预算预检） | 注入上下文、拦截启动 | Phase 1 |
| `before_tool_call` | 工具调用前 | 都察院（规则检查）、批红（T3 审批） | 拦截（`block: true`）、修改参数 | Phase 1 |
| `after_tool_call` | 工具调用后 | 都察院（结果审计）、户部（成本累计） | 记录、告警 | Phase 1 |
| `llm_input` | 发送 LLM 请求前 | 都察院（内容检查） | 检查、修改 | Phase 2 |
| `llm_output` | 收到 LLM 响应后 | 都察院（输出审计）、户部（Token 统计） | 检查、记录 | Phase 1 |
| `agent_end` | Agent 执行结束 | 文渊阁（经验沉淀）、通政司（结果推送） | 后处理、存储 | Phase 1 |
| `before_compaction` | 消息压缩前 | 文渊阁（提取重要信息） | 检查、保留标记 | Phase 2 |
| `session_start` / `session_end` | 会话开始 / 结束 | 户部（会话级统计）| 初始化、清理 | Phase 2 |

钩子执行规则：同一钩子的多个处理器按优先级顺序执行；`before_*` 钩子可返回拦截指令，`after_*` 钩子只能记录不能拦截。

### 5.5 通政司（Notifier）

通政司不只是"通知发送器"，而是天枢的**信息汇报枢纽**——对应明朝通政司"章奏收发、邸报编发、信息过滤"的完整职能。

**职责**

- **结果过滤与摘要**：从 Memorial 中提取用户关心的信息，过滤工具调用细节和 LLM 思考过程，生成简明摘要
- **渲染与格式适配**：将过滤后的内容渲染为各通道的原生格式 [CoPaw-7]
- **优先级递送**：按任务优先级和紧急程度决定推送时序；urgent 任务立即推送，normal 任务可合并为定期汇总
- **待批奏折推送**：维护 `NEEDS_REVIEW` 状态的 Memorial 队列，按优先级和等待时长排序推送给用户，催促批红
- **定期汇总（邸报）**：Phase 2+ 支持日报 / 周报式的任务执行摘要汇总，主动推送系统运行概览
- **防抖、限流、重试**：合并短时间内的连续状态更新，per-channel 速率限制 [CoPaw-8] [PicoClaw-2]

**边界**

- 不维护任务真相（Memorial 是唯一真相来源）
- 不负责决定是否进入 `NEEDS_REVIEW`（这是 Auditor 的职责）
- 通知失败不能回滚执行结果

**阶段**

- Phase 0：HTTP 响应返回结果（同步），可选 SSE 流式推送
- Phase 1：独立模块，支持 WebSocket 实时推送 + 飞书 / 钉钉，维护待批推送队列
- Phase 2：扩展 email 通道，支持邸报式定期汇总

### 5.6 文渊阁（Memory）

文渊阁不只是"后台记账"的被动存储，而是主动参与治理的**知识基础设施**——既沉淀经验，也为规划提供决策依据。

**职责**

- 存储终态任务摘要、经验、可复用知识
- 提供历史检索和上下文召回
- 管理消息压缩与长期记忆
- **规划前检索**：Planner 拆解任务前，查询文渊阁是否有类似任务的历史经验（成功方案、失败教训、最佳工具组合），辅助规划决策
- **模式识别**：识别重复出现的任务模式，沉淀为可复用的 Skill 或 Prompt 模板建议

**输入来源**

- `execution.completed`
- `execution.failed`
- `audit.completed`
- 人工批红后的最终结论

**输出消费者**

- `Planner`：规划前检索历史经验
- `Executor`：Agent Loop 中可选召回相关知识
- `Notifier`：汇总报告中引用历史对比

**边界**

- 不直接消费 Notifier 的发送结果作为业务真相
- 不在 Phase 0 引入，避免 MVP 复杂化
- 检索结果仅作为参考，不强制约束 Planner 的规划决策

**多官员记忆架构（Phase 1+）**

Phase 1 起，文渊阁管理两层记忆：

| 层级 | 存储位置 | 读权限 | 写权限 |
|------|---------|--------|--------|
| 官员私有 | `personas/<id>/MEMORY.md` | 本人 + 内阁 | 本人 |
| 朝堂共享 | `personas/court/MEMORY.md` | 所有官员 | 仅文渊阁 |

Source of Truth = Markdown 文件；派生索引 = SQLite FTS5（Phase 2）+ 可选向量索引（Phase 3）。

**Retain / Recall / Reflect 循环**（Phase 2 引入）

- **Retain**（执行后）：各官员提取域内经验写入私有日志，通过 `agent_end` 钩子触发
- **Recall**（执行前）：查询私有 + 共享记忆注入上下文，通过 `before_agent_start` 钩子触发
- **Reflect**（定期）：文渊阁大学士归纳跨官员共性模式，沉淀到共享记忆，淘汰过时条目

详见 `agent-persona.md` §5-§6。

### 5.7 户部（CostManager）

户部管的不只是"花了多少钱"，而是天枢的**资源账本与配额治理**——对应明朝户部掌管赋税、国库、户籍的完整职能。

**职责**

- **Token 与成本统计**：per-request 和 per-task 的 Token 消耗、API 调用费用累计
- **预算熔断**：Token 或成本超过 Edict 设定的预算时，发射 `cost.budget_exceeded` 事件，触发执行终止
- **API 配额追踪**：各 Provider 的 RPM / TPM 配额监控，接近限额时降速或切换 Provider
- **资源账本**：按 Edict / submitter / 时间段维度的成本汇总报表，为通政司的邸报式汇总提供数据
- **成本路由辅助**：为 Planner 和 Provider 路由提供成本数据，支持"便宜模型做简单任务、贵模型做复杂任务"的策略

**边界**

- Phase 0 只在 Agent Loop 中提取 usage 并打印，不独立建模块
- Phase 2 起独立为 `CostManager`，具备熔断和路由能力
- 不负责业务层面的任务优先级（这是内阁的职责）

### 5.8 共享基础服务

以下服务不对应单独“部院”，但属于核心基础设施：

- `EventBus`
- `Scheduler`
- `ToolRegistry`
- `LLM Client / Provider`
- `Storage`
- `ConfigManager`

---

## 六、基础设施与非功能要求

### 6.1 EventBus

`EventBus` 是模块间的领域事件总线，不等于通道消息队列。

**职责**

- 发射和订阅领域事件
- 让 `Executor`、`Auditor`、`Notifier`、`Memory` 解耦

**阶段语义**

- Phase 0：无 `EventBus`，直接函数调用
- Phase 1：进程内 `EventBus` + SQLite 事件日志

**接口**

| 方法 | 参数 | 说明 |
|------|------|------|
| `emit` | `event` | 发射完整事件信封 |
| `on` | `event_type, handler` | 注册处理器 |
| `off` | `event_type, handler` | 移除处理器 |

### 6.2 Scheduler

`Scheduler` 从 Phase 1 开始引入，因为事件驱动调度必须有稳定调度语义。

**支持模式**

- `immediate`
- `once`
- `cron`

**接口**

| 方法 | 参数 | 说明 |
|------|------|------|
| `schedule` | `edict` | 注册任务并返回 `job_id` |
| `cancel` | `job_id` | 取消调度任务 |
| `start` | — | 启动调度器 |
| `stop` | — | 停止调度器 |

**最小要求**

- 调度状态要持久化
- misfire 要可配置
- 同一 `job_id` 不得并发重复执行

### 6.3 LLM Client 与 Provider

Phase 0 直接使用 `LiteLLM`，但内部接口必须为后续多 Provider 做准备。

**统一能力**

- 普通对话
- function calling
- token usage 提取
- 可选流式响应

**Phase 2+ 增强**

- Provider 能力声明（是否支持原生工具调用、是否支持视觉等）[ZeroClaw-7]
- 路由策略：按成本、能力、任务类型选择模型 [CoPaw-9]

### 6.4 ToolRegistry 与 Skills

工具和 Skill 不是同一层概念：

- `Tool`：可执行原子能力，如文件、Shell、Web、MCP
- `Skill`：**指导文档而非代码**——告诉 Agent 如何组合使用已有 Tools 完成特定任务 [OpenClaw-6]

**Skill = Markdown 文档（与 OpenClaw 格式兼容）**

天枢采用与 OpenClaw 完全兼容的 SKILL.md 格式。同一个 SKILL.md 文件可以同时被两个系统使用，共享 Skills 生态。

**SKILL.md 文件结构**

```yaml
---
name: github
description: "GitHub CLI 操作指导"
metadata:
  openclaw:                          # OpenClaw 兼容命名空间
    emoji: "🐙"
    os: ["darwin", "linux"]          # 操作系统限制
    requires:
      bins: ["gh"]                   # 必须存在的命令行工具（全部满足）
      anyBins: ["claude", "pi"]      # 至少存在一个
      env: ["GITHUB_TOKEN"]          # 必须存在的环境变量
      config: ["github.org"]         # 必须存在的配置项
    install:                         # 安装说明（Phase 2+ 支持自动安装）
      - kind: brew
        formula: gh
        bins: ["gh"]
      - kind: apt
        package: gh
        bins: ["gh"]
    always: false                    # 是否始终注入（跳过资格检查）
  tianshu:                           # 天枢扩展命名空间（可选）
    tool_tier: "T1"                  # 关联工具的权限等级
    estimated_tokens: 500            # 预估 Token 消耗
---

# GitHub Skill

（Markdown 正文：步骤说明、注意事项、输出格式要求）
```

**双命名空间设计**

- `metadata.openclaw`：与 OpenClaw 格式完全兼容，确保 SKILL.md 文件可跨系统复用
- `metadata.tianshu`：天枢特有的扩展字段（可选），如工具权限等级、Token 预估等
- 两个命名空间共存互不干扰

**资格检查机制**（Phase 0 即引入）

加载 SKILL.md 时，必须通过以下全部检查才会注入 Agent 系统提示：

| 检查项 | 方式 | 说明 |
|--------|------|------|
| `requires.bins` | `shutil.which()` | 所有列出的命令行工具都必须存在 |
| `requires.anyBins` | `shutil.which()` | 至少一个命令行工具存在 |
| `requires.env` | `os.environ` | 所有列出的环境变量都必须设置 |
| `requires.config` | 配置管理器查询 | 所有配置路径必须有值 |
| `os` | `sys.platform` | 当前操作系统在允许列表中 |
| `always: true` | — | 跳过上述检查，始终加载 |

**Skills 发现与加载**

目录扫描优先级（高优先级覆盖低优先级的同名 Skill）：

1. 工作区级：`<workspace>/skills/`
2. 项目级：`<workspace>/.agents/skills/`
3. 用户级：`~/.agents/skills/`
4. 内置级：天枢发行包内置 Skills

加载限制（防止资源耗尽）：

| 限制项 | 默认值 | 说明 |
|--------|--------|------|
| 单目录最大候选数 | 300 | 防止扫描过大目录 |
| 注入系统提示的最大 Skill 数 | 150 | 防止 prompt 过长 |
| 系统提示中 Skills 的字符预算 | 30,000 | 控制 Token 消耗 |
| 单个 SKILL.md 最大文件大小 | 256 KB | 防止加载巨型文件 |

**运行时注入流程**

1. 启动时扫描所有 Skills 目录，解析 YAML frontmatter
2. 对每个 SKILL.md 执行资格检查
3. 通过检查的 Skills 按字符预算格式化后拼入 Agent 系统提示
4. Agent 根据任务目标判断适用的 Skill，遵循其中的步骤指引

**阶段策略**

- **Phase 0**：引入 `ToolRegistry`（工具硬编码注册）+ `Skills Loader`（SKILL.md 只读加载与注入）
- **Phase 1**：引入生命周期钩子注册，允许内部预置工具分组；引入 Skills 热重载（文件监听 + 防抖）
- **Phase 2**：引入统一 `PluginApi` + Skills 自动安装（brew/apt/pip/go）+ 安装源安全校验
- 不在早期引入远程 Skill Hub

**统一注册 API**（Phase 2+ 引入）[OpenClaw-7]

Phase 2 起，Tool、Skill、Channel、Provider 等扩展能力通过统一的 `PluginApi` 注册（对应"吏部"统一注册职能）：

| 注册方法 | 能力 | 说明 |
|---------|------|------|
| `register_tool` | 注册 Agent 工具 | Phase 0 由 `ToolRegistry` 承担 |
| `register_hook` | 注册生命周期钩子 | Phase 1 |
| `register_channel` | 注册通知通道 | Phase 1 |
| `register_provider` | 注册 LLM Provider | Phase 2 |
| `register_skill` | 注册 Skill 文档 | Phase 2 |
| `register_command` | 注册用户命令（绕过 LLM 直接执行）| Phase 2 |

独占槽位机制：某些能力类型（如 memory backend、context engine）同一时间只能有一个活跃实例。

### 6.5 Security Model

这是架构强约束，不是实现细节。

**认证与授权**

- API 默认通过 API Key 或 Bearer Token 认证
- Channel 必须有 submitter 身份映射
- 可选 CLI 继承本地用户身份
- 工具执行权限与通道通知权限分开建模

**工作区与副作用控制**

- 文件操作默认限制在工作区白名单
- 外部副作用工具必须打审计事件
- 高风险工具必须显式批准

**密钥管理**

- Provider、Channel、Tool 的 Secret 分域存放
- 日志、事件、错误转储不得输出明文 Secret

### 6.6 Observability

最小可观测性要求如下：

**事件**

- 任务生命周期事件
- LLM 请求/响应摘要
- 工具开始/完成
- 审计结论
- 通知结果

**指标**

| 指标 | 说明 |
|------|------|
| `tasks_total` | 总任务数 |
| `tasks_failed_total` | 失败任务数 |
| `tasks_cancelled_total` | 取消任务数 |
| `task_duration_seconds` | 任务耗时 |
| `llm_tokens_total` | Token 消耗 |
| `llm_cost_usd_total` | 成本消耗 |
| `event_queue_depth` | 事件队列深度 |
| `scheduler_jobs_total` | 调度任务数 |

**日志**

- 结构化日志，按 `edict_id` / `memorial_id` 串联
- 错误转储需包含时间、阶段、事件上下文、异常摘要 [CoPaw-6]

### 6.7 ConfigManager

配置分层：

1. 默认值
2. YAML
3. 环境变量
4. API 请求参数覆盖

Phase 1+ 可增加配置热重载，但热重载不应影响正在执行的任务上下文。

---

## 七、Python 包结构

### 7.1 Phase 0 精简版

```
src/tianshu/
  __init__.py
  app.py              # FastAPI 应用入口
  agent.py
  llm.py
  models.py
  config.py
  storage.py          # SQLite 存储层
  gateway/
    __init__.py
    api.py            # FastAPI 路由
    validator.py      # 输入校验
  tools/
    __init__.py
    registry.py
    web_search.py
    shell.py
    file_ops.py
  skills/
    __init__.py
    loader.py           # SKILL.md 发现、frontmatter 解析、资格检查
    builtin/            # 内置 Skills
      web-search/
        SKILL.md
      file-ops/
        SKILL.md
      shell/
        SKILL.md
personas/               # 官员 Bootstrap 文件（Phase 1+）
  court/
    COURT.md
    MEMORY.md
  neige/ bingbu/ ducha/ tongzheng/
    SOUL.md / ROLE.md / MEMORY.md
Dockerfile              # 容器化支持
docker-compose.yml      # 一键启动
```

### 7.2 完整版（按模块拆分）

```
src/tianshu/
  __init__.py
  app.py

  models/
    __init__.py
    edict.py
    memorial.py
    decree.py
    events.py
    plan.py

  gateway/
    __init__.py
    cli.py
    api.py
    validator.py

  planner/
    __init__.py
    planner.py
    context_filter.py
    prompts.py

  executor/
    __init__.py
    agent.py
    executor.py
    worker.py
    approvals.py
    tool_filter.py
    workspace.py

  auditor/
    __init__.py
    auditor.py
    rules.py
    reviewer.py

  notifier/
    __init__.py
    notifier.py
    renderer.py
    rate_limiter.py

  memory/
    __init__.py
    manager.py
    compactor.py
    backends/
      __init__.py
      file_backend.py
      sqlite_backend.py

  persona/
    __init__.py
    model.py              # AgentPersona 数据模型
    loader.py             # Persona 文件加载器
    prompt_builder.py     # 角色化 system prompt 构建器
    selector.py           # 官员选择器
    memory_manager.py     # Per-agent 记忆管理
    consultation.py       # 会商协议（Phase 3）
    evaluation.py         # 官员绩效评估（Phase 3）

  cost/
    __init__.py
    manager.py
    tracker.py
    budget.py

  bus/
    __init__.py
    event_bus.py

  scheduler/
    __init__.py
    scheduler.py
    job_store.py

  providers/
    __init__.py
    protocol.py
    manager.py
    litellm_provider.py

  storage/
    __init__.py
    edict_repo.py
    memorial_repo.py
    event_journal.py
    job_repo.py

  tools/
    __init__.py
    registry.py
    web_search.py
    shell.py
    file_ops.py

  skills/
    __init__.py
    loader.py
    builtin/

  config/
    __init__.py
    schema.py
    defaults.py
```

---

## 八、Phase 验收标准

### 8.1 Phase 0

- `POST /api/edicts` 可以提交任务并完成一次完整执行
- `GET /api/edicts/{id}` 可以查询任务状态
- `GET /api/memorials/{id}` 可以查询执行结果
- 能生成 `Edict` 与 `Memorial`（SQLite 持久化）
- 工具调用采用标准 function calling
- Skills 系统能加载 SKILL.md 并注入 Agent 系统提示（与 OpenClaw 格式兼容）
- 失败、超时、进程终止都能写出终态结果
- `docker compose up` 可以启动服务并通过端口访问
- SQLite 中能看到可追踪的事件记录

### 8.2 Phase 1

- 支持即时、一次性、cron 三类调度
- 有统一 `EventBus` 和事件日志
- `Planner`、`Auditor`、`Notifier` 可独立订阅事件工作
- 人工复核流可将任务置为 `NEEDS_REVIEW`
- 调度、执行、审计、通知的 Phase 表述在文档与实现中一致

### 8.3 Phase 2

- `Memory`、`CostManager` 已独立模块化
- 成本预算可以熔断
- 多通道通知与 Provider 路由可配置
- 统一 `PluginApi` 注册接口可用

### 8.4 Phase 3

- 支持 DAG 执行和多 Agent 并发
- 支持更强的取消、重试和恢复能力
- 容器化部署下仍保持统一任务契约和事件语义

---

## 附录：参考项目采纳矩阵

### A. 必须采纳

| 参考项目 | 来源文件 | 采纳点 | 落点 |
|---------|---------|-------|------|
| NanoBot | `nanobot/agent/loop.py` | ReAct 循环状态机 | `Executor` |
| NanoBot | `nanobot/agent/subagent.py` | 子任务工具裁剪与级联取消思路 | `Executor` |
| CoPaw | `src/copaw/app/crons/models.py` | `schedule + dispatch + runtime` 一体化任务模型 | `Edict` |
| ZeroClaw | `zeroclaw/src/observability/traits.rs` | 统一观察事件与指标语义 | `EventBus` / `Observability` |
| PicoClaw | `picoclaw/pkg/agent/instance.go` | 工作区隔离 | `Executor` |
| OpenClaw | `src/agents/tool-policy-pipeline.ts` | 多层工具 Policy Pipeline | 工具权限分级 |
| OpenClaw | `src/plugins/types.ts` | 25 个命名生命周期钩子 → 天枢核心钩子集 | `Auditor` / `Hooks` |
| OpenClaw | `src/gateway/node-invoke-system-run-approval.ts` | 执行中实时审批（allow-once / allow-always） | 批红决策链 |
| OpenClaw | `skills/` + `src/agents/skills/` | SKILL.md 格式兼容（frontmatter + 资格检查 + 只读加载） | `Skills Loader`（Phase 0） |

### B. 延后采纳

| 参考项目 | 来源文件 | 延后原因 | 预期阶段 |
|---------|---------|---------|---------|
| DeepAgents | `deepagents/middleware/subagents.py` | 多子代理并行需要先有稳定计划契约 | Phase 3 |
| CoPaw | `agents/memory/memory_manager.py` | 高级记忆压缩依赖 Memory 模块成熟 | Phase 2 |
| CoPaw | `agents/routing_chat_model.py` | 成本路由依赖 Provider 能力声明与成本账本 | Phase 2 |
| NanoBot | `nanobot/bus/` | 完整 MessageBus 过早引入会提高复杂度 | Phase 2 视需要 |
| OpenClaw | `skills/` (SKILL.md 自动安装 + 热重载) | 自动安装和热重载依赖 Phase 1/2 基础设施 | Phase 1（热重载）/ Phase 2（自动安装） |
| OpenClaw | `src/plugins/types.ts` (统一 PluginApi) | 统一注册需要各模块 Protocol 先稳定 | Phase 2 |
| OpenClaw | `src/agents/pi-embedded-runner/run.ts` (Lane-based 并发) | 并发控制依赖多 Agent 架构 | Phase 3 |
| OpenClaw | `src/agents/pi-embedded-runner/compact.ts` (Compaction) | 会话压缩依赖 Memory 模块 | Phase 1 |

### C. 当前不采纳

| 参考项目/能力 | 不采纳原因 |
|--------------|-----------|
| 早期引入完整多通道体系 | 会稀释 MVP，先保证 HTTP API 跑通 |
| 早期引入远程 Skill Hub | 本地内置 Skill 足够，先不引入远程分发和热更新 |
| 早期引入大规模多 Agent 协作 | 当前更需要稳定治理和可审计的单 Agent 执行 |
| OpenClaw Canvas 实时渲染 | Agent 生成 HTML 推送到客户端 WebView；天枢 Phase 0-2 无移动端/桌面端计划 |
| OpenClaw 五维路由绑定 | channel→account→peer→guild→role 路由；天枢 Phase 0-2 为单用户场景，过早引入 |
| OpenClaw Scope-based Gateway 授权 | 5 个 operator scope 的 RPC 授权；Phase 2 Gateway API 时再评估 |

### D. 参考索引

| 编号 | 项目 | 文件 | 设计点 |
|------|------|------|--------|
| NanoBot-1 | NanoBot | `nanobot/agent/loop.py` | ReAct 循环状态机 |
| NanoBot-3 | NanoBot | `nanobot/agent/subagent.py` | 工具裁剪与级联取消 |
| NanoBot-5 | NanoBot | `nanobot/heartbeat/service.py` | 两阶段结构化巡检 |
| DeepAgents-1 | DeepAgents | `deepagents/middleware/react.py` | ReAct 中间件思路 |
| DeepAgents-2 | DeepAgents | `deepagents/middleware/subagents.py` | 结构化任务拆解 |
| DeepAgents-3 | DeepAgents | `deepagents/middleware/subagents.py` | 上下文裁剪分派 |
| CoPaw-6 | CoPaw | `src/copaw/app/runner/query_error_dump.py` | 结构化错误转储 |
| CoPaw-7 | CoPaw | `src/copaw/app/channels/renderer.py` | 消息渲染管线 |
| CoPaw-8 | CoPaw | `src/copaw/app/channels/manager.py` | 防抖与通道治理 |
| CoPaw-9 | CoPaw | `src/copaw/agents/routing_chat_model.py` | Provider 路由 |
| PicoClaw-1 | PicoClaw | `picoclaw/pkg/agent/instance.go` | 工作区隔离 |
| PicoClaw-2 | PicoClaw | `picoclaw/pkg/channels/manager.go` | 通道限速与重试 |
| ZeroClaw-1 | ZeroClaw | `zeroclaw/src/cron/scheduler.rs` | 安全策略违规不重试 |
| ZeroClaw-7 | ZeroClaw | `zeroclaw/src/providers/traits.rs` | Provider 能力声明 |
| OpenClaw-1 | OpenClaw | `src/agents/pi-embedded-runner/compact.ts` | 分片压缩 + 标识符保留 + 合并摘要 + Context Window Guard |
| OpenClaw-2 | OpenClaw | `src/agents/tool-policy-pipeline.ts` | 多层 Tool Policy Pipeline（全局→通道→任务→Agent） |
| OpenClaw-3 | OpenClaw | `src/gateway/node-invoke-system-run-approval.ts` | 执行中实时审批（allow-once / allow-always）+ 审批字段安全隔离 |
| OpenClaw-4 | OpenClaw | `src/agents/pi-embedded-runner/run.ts` | Lane-based 并发控制（session lane + global lane 双层队列） |
| OpenClaw-5 | OpenClaw | `src/plugins/types.ts` | 25 个命名生命周期钩子（before/after 模式 + 强类型 Event/Context/Result） |
| OpenClaw-6 | OpenClaw | `skills/` | Skill = Markdown 文档（YAML frontmatter + 指导正文 + 资格检查） |
| OpenClaw-7 | OpenClaw | `src/plugins/types.ts` (`OpenClawPluginApi`) | 统一 PluginApi 注册（Tool/Hook/Channel/Provider/Skill/Command） |
