# Phase 1-3 实现文档

> 分支: `feat_phase1` | 快照日期: 2026-03-19
>
> 本文档接续 [phase0.md](phase0.md)，记录 Phase 1（治理与异步调度）、Phase 2（平台化能力）、Phase 3（多 Agent 与 DAG）的完整实现。三个阶段在 `feat_phase1` 分支上一次性实现。

---

## 1. 阶段概览

| Phase | 目标 | 一句话总结 |
|-------|------|-----------|
| 1 | 治理与异步调度 | EventBus 驱动流水线、Scheduler/Planner/Executor 解耦、Auditor 审计、Decree 审批、Hooks 生命周期 |
| 2 | 平台化能力 | Memory 记忆系统、Cost 成本管理（CNY）、多 Provider 路由、Plugin 插件系统、通知通道扩展 |
| 3 | 多 Agent 与 DAG | DAG 执行引擎、WorkerPool 并发、Lane 背压、取消/重试/检查点、会商系统、绩效评估 |

### 新增模块总表

| 模块 | 目录 | Phase |
|------|------|-------|
| EventBus | `bus/` | 1 |
| Scheduler | `scheduler/` | 1 |
| Planner | `planner/` | 1 |
| Executor 重构 | `executor/` | 1 |
| Auditor | `auditor/` | 1 |
| Notifier + WebSocket | `notifier/` | 1 |
| Hooks 系统 | `executor/hooks.py` | 1 |
| Approval (Decree) | `executor/approvals.py` | 1 |
| Persona 基础 | `persona/` | 1 |
| Memory | `memory/` | 2 |
| Cost 管理 | `cost/` | 2 |
| Provider 路由 | `providers/` | 2 |
| Plugin 系统 | `plugins/` | 2 |
| 通知通道 | `notifier/channels/` | 2 |
| DAG 引擎 | `dag/` | 3 |
| Worker / WorkerPool | `executor/worker.py, worker_pool.py` | 3 |
| Lane 并发 | `executor/lanes.py` | 3 |
| 取消/重试/检查点 | `executor/cancel.py, retry.py, checkpoint.py` | 3 |
| 会商系统 | `consultation/` | 3 |
| 绩效评估 | `persona/evaluator.py` | 3 |

---

## 2. 目录结构

### 后端新增 `src/tianshu/`

```
src/tianshu/
├── bus/
│   └── event_bus.py              # 异步 EventBus（优先级 handler）
├── scheduler/
│   └── scheduler.py              # 调度器（immediate / once / cron）
├── planner/
│   ├── planner.py                # 任务分解（LLM 规划 / 直通）
│   └── prompts.py                # 规划 prompt 模板
├── executor/
│   ├── executor.py               # 事件驱动编排（单任务 / DAG）
│   ├── agent.py                  # Agent ReAct 引擎（含 persona/hooks 扩展）
│   ├── hooks.py                  # HookRegistry + 10 种 HookType
│   ├── approvals.py              # ApprovalManager（Decree 审批流）
│   ├── worker.py                 # Worker — 单节点 Agent 执行
│   ├── worker_pool.py            # WorkerPool — Semaphore 并发管理
│   ├── lanes.py                  # SessionLane / GlobalLane / LaneManager
│   ├── dag_scheduler.py          # DAGScheduler — 就绪节点调度
│   ├── cancel.py                 # CascadeCanceller — 级联取消
│   ├── retry.py                  # PartialRetrier — 部分重试
│   └── checkpoint.py             # CheckpointManager — 状态快照
├── auditor/
│   ├── auditor.py                # 两层审计（规则引擎 + LLM）
│   ├── rules.py                  # RulesEngine — 快速规则检查
│   └── reviewer.py               # LLMReviewer — LLM 复审
├── notifier/
│   ├── notifier.py               # WebSocket 广播 + Webhook 投递
│   ├── channel_registry.py       # 通道注册中心（含限流）
│   ├── renderer.py               # 多通道消息渲染
│   ├── digest.py                 # 摘要生成
│   └── channels/
│       ├── base.py               # NotificationChannel Protocol
│       ├── feishu.py             # 飞书通道
│       ├── dingtalk.py           # 钉钉通道
│       └── email.py              # 邮件通道（SMTP）
├── persona/
│   ├── model.py                  # AgentPersona 数据模型
│   ├── loader.py                 # Persona 发现 + frontmatter 解析
│   ├── prompt_builder.py         # 8 层 system prompt 注入
│   ├── selector.py               # OfficialSelector — 任务→官员匹配
│   ├── evaluator.py              # PerformanceEvaluator — 绩效评估
│   └── metrics.py                # PersonaMetrics 模型
├── memory/
│   ├── manager.py                # MemoryManager（CRUD + Hook/Event 集成）
│   ├── models.py                 # MemoryEntry / CompactionResult / MemoryQuery
│   ├── access_control.py         # MemoryAccessControl（读写权限策略）
│   ├── compactor.py              # MemoryCompactor（LLM 压缩）
│   ├── reflect.py                # Reflector（观察→洞察）
│   └── backends/
│       └── sqlite_backend.py     # SQLite 存储后端
├── cost/
│   ├── manager.py                # CostManager（Hook/Event 集成）
│   ├── models.py                 # CostRecord / CostSummary / BudgetStatus
│   ├── tracker.py                # CostTracker（按 edict 累积）
│   └── budget.py                 # BudgetChecker（预算断路器）
├── providers/
│   ├── manager.py                # ProviderManager — 多 Provider 路由
│   ├── protocol.py               # ProviderInfo / ProviderCapability / TaskRequirements
│   └── litellm_provider.py       # LiteLLM 封装工厂
├── plugins/
│   ├── loader.py                 # PluginLoader — manifest.json 发现
│   ├── manifest.py               # PluginManifest 模型
│   └── api.py                    # PluginApi — 统一注册门面
├── consultation/
│   ├── session.py                # ConsultationSession — 多 persona 并行分析
│   ├── models.py                 # ConsultationRequest/Response/PersonaOpinion
│   └── synthesizer.py            # Synthesizer — 综合研判
├── dag/
│   ├── models.py                 # DAGExecution / DAGNode / DAGNodeStatus
│   └── graph.py                  # DAG — 拓扑排序、就绪检测、失败传播
└── models/                       # Phase 0 models.py 拆分为包
    ├── __init__.py               # 向后兼容导出
    ├── common.py                 # 枚举 + 共享类型
    ├── edict.py                  # Edict + EdictSchedule/Dispatch/Runtime
    ├── memorial.py               # Memorial
    ├── decree.py                 # Decree
    ├── events.py                 # EventEnvelope + make_event
    ├── plan.py                   # Plan + PlanTask
    └── api.py                    # 请求/响应模型
```

### Persona 目录 `personas/`

```
personas/
├── court/          # 共享上下文（COURT.md, MEMORY.md）
├── bingbu/         # 兵部 — 执行（SOUL.md, ROLE.md, MEMORY.md）
├── ducha/          # 都察 — 审计
├── hubu/           # 户部 — 成本
├── neige/          # 内阁 — 规划/综合
├── tongzheng/      # 通政 — 通知
└── wenyuan/        # 文渊 — 记忆/知识
```

### 前端新增 `web/src/`

```
web/src/
├── pages/
│   ├── ApprovalQueuePage.tsx     # 审批队列
│   ├── AuditDashboardPage.tsx    # 审计仪表盘
│   ├── ConsultationPage.tsx      # 会商页面
│   ├── CostDashboardPage.tsx     # 成本仪表盘
│   ├── DagBattleMapPage.tsx      # DAG 战图
│   ├── MemoryDashboardPage.tsx   # 记忆管理
│   ├── PersonaDashboardPage.tsx  # 官员绩效
│   ├── ProviderDashboardPage.tsx # Provider 管理
│   └── SchedulerPage.tsx         # 调度监控
├── components/
│   ├── common/ConnectionIndicator.tsx  # WebSocket 连接状态
│   ├── cost/                     # 成本图表组件
│   ├── dag/                      # DAG 可视化组件
│   └── decree/                   # 审批操作组件
├── hooks/
│   ├── useApprovals.ts           # 审批 hooks
│   ├── useAudit.ts               # 审计 hooks
│   ├── useConsultation.ts        # 会商 hooks
│   ├── useCost.ts                # 成本 hooks
│   ├── useDag.ts                 # DAG hooks
│   ├── useMemory.ts              # 记忆 hooks
│   ├── usePersonas.ts            # 官员 hooks
│   ├── useProviders.ts           # Provider hooks
│   ├── useScheduler.ts           # 调度 hooks
│   ├── useWebSocket.ts           # WebSocket 连接管理
│   └── useWsQueryInvalidation.ts # WS 驱动的查询失效
├── api/
│   ├── audit.ts                  # 审计 API
│   ├── consultations.ts          # 会商 API
│   ├── cost.ts                   # 成本 API
│   ├── dag.ts                    # DAG API
│   ├── decrees.ts                # 审批 API
│   ├── memory.ts                 # 记忆 API
│   ├── personas.ts               # 官员 API
│   ├── providers.ts              # Provider API
│   └── scheduler.ts              # 调度 API
└── utils/
    └── edictPhase.ts             # 敕令阶段判定工具
```

---

## 3. 数据模型演进

### Phase 1 — Edict 扩展字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `idempotency_key` | `str?` | 幂等键 |
| `source` | `"cli" \| "api" \| "channel" \| "scheduler"` | 来源 |
| `submitter` | `str?` | 提交者 |
| `priority` | `"urgent" \| "normal" \| "low"` | 优先级 |
| `review_policy` | `"never" \| "on_failure" \| "on_flag" \| "always"` | 审核策略 |
| `output_format` | `str?` | 输出格式 |
| `constraints` | `list[str]` | 约束条件 |
| `schedule` | `EdictSchedule` | 调度配置 |
| `dispatch` | `EdictDispatch?` | 通知分发配置 |
| `runtime` | `EdictRuntime` | 运行时配置 |
| `metadata` | `dict` | 扩展元数据 |

**EdictSchedule**:
```python
type: "immediate" | "once" | "cron"  # 调度类型
at: datetime | None                   # once 模式的执行时间
cron: str | None                      # cron 表达式
timezone: str = "UTC"
```

**EdictRuntime**:
```python
timeout_seconds: int = 300
max_iterations: int = 20
max_concurrency: int = 1              # DAG 并发度
retry_limit: int = 0
token_budget: int | None              # Token 预算
cost_budget_cny: float | None         # 成本预算（CNY）
approval_required_tools: list[str]    # 需审批的工具
```

**EdictDispatch**:
```python
channels: list[str]                   # 通知通道
mode: "broadcast" | "first"           # 通知模式
notify_on_failure: bool = True
target: str | None                    # 目标
```

### Phase 1 — Memorial 扩展字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `attempt` | `int` | 执行次数 |
| `parent_memorial_id` | `str?` | 重试时的父奏折 ID |
| `review_status` | `"not_required" \| "pending" \| "approved" \| "rejected"` | 审核状态 |
| `audit` | `AuditResult?` | 审计结果 |
| `artifacts` | `list[ArtifactRef]` | 产物引用 |
| `timeline` | `list[TimelineItem]` | 时间线事件 |

### Phase 2 — 新模型

| 模型 | 文件 | 关键字段 |
|------|------|----------|
| `CostRecord` | `cost/models.py` | edict_id, provider_name, model, prompt/completion/total_tokens, cost_cny |
| `CostSummary` | `cost/models.py` | total_records, total_tokens, total_cost_cny |
| `BudgetStatus` | `cost/models.py` | scope, budget_cny, spent_cny, remaining_cny, exceeded |
| `MemoryEntry` | `memory/models.py` | persona_id, category, content, source, confidence, access_level |
| `MemoryQuery` | `memory/models.py` | persona_id, query, category, include_shared |
| `CompactionResult` | `memory/models.py` | original_count, compacted_count, summary, tokens_saved |
| `ProviderInfo` | `providers/protocol.py` | name, model, api_base, capabilities, rpm_limit, priority, cost_per_1k |
| `PluginManifest` | `plugins/manifest.py` | name, version, type, entry_point, permissions, sha256 |

### Phase 3 — 新模型

| 模型 | 文件 | 关键字段 |
|------|------|----------|
| `DAGExecution` | `dag/models.py` | id, edict_id, plan_json, status, root_memorial_id, max_concurrency, nodes |
| `DAGNode` | `dag/models.py` | node_id, dag_execution_id, description, depends_on, status, assigned_official, memorial_id, checkpoint_json |
| `DAGNodeStatus` | `dag/models.py` | pending, ready, running, completed, failed, cancelled |
| `ConsultationRequest` | `consultation/models.py` | topic, context, persona_ids, synthesizer_persona_id |
| `ConsultationResponse` | `consultation/models.py` | id, status, opinions, synthesis, decision |
| `PersonaOpinion` | `consultation/models.py` | persona_id, opinion, confidence, key_points |
| `PersonaMetrics` | `persona/metrics.py` | total_executions, success_rate, total_tokens, total_cost_cny, avg_duration_seconds |
| `Plan` / `PlanTask` | `models/plan.py` | tasks(依赖+工具+assigned_official), priority_order, to_dag() |
| `EventEnvelope` | `models/events.py` | event_type, edict_id, memorial_id, producer, payload |
| `Decree` | `models/decree.py` | memorial_id, action(approve/reject/retry/amend/cancel), actor |

### Phase 3 — Memorial 新增字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `dag_node_id` | `str?` | 关联的 DAG 节点 ID |
| `persona_id` | `str?` | 执行此奏折的官员 ID |

### 枚举扩展

`TaskStatus` 新增 4 个值：

```python
class TaskStatus(str, Enum):
    SUBMITTED = "submitted"     # Phase 0
    RUNNING = "running"         # Phase 0
    COMPLETED = "completed"     # Phase 0
    FAILED = "failed"           # Phase 0
    CANCELLED = "cancelled"     # Phase 0
    SCHEDULED = "scheduled"     # Phase 1 ← NEW
    PLANNING = "planning"       # Phase 1 ← NEW
    AUDITING = "auditing"       # Phase 1 ← NEW
    NEEDS_REVIEW = "needs_review"  # Phase 1 ← NEW
```

### 存储迁移清单

```python
# Phase 1 edict 扩展
"ALTER TABLE edicts ADD COLUMN idempotency_key TEXT"
"ALTER TABLE edicts ADD COLUMN source TEXT NOT NULL DEFAULT 'api'"
"ALTER TABLE edicts ADD COLUMN submitter TEXT"
"ALTER TABLE edicts ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'"
"ALTER TABLE edicts ADD COLUMN review_policy TEXT NOT NULL DEFAULT 'never'"
"ALTER TABLE edicts ADD COLUMN output_format TEXT"
"ALTER TABLE edicts ADD COLUMN constraints_json TEXT NOT NULL DEFAULT '[]'"
"ALTER TABLE edicts ADD COLUMN schedule_json TEXT NOT NULL DEFAULT '{}'"
"ALTER TABLE edicts ADD COLUMN dispatch_json TEXT"
"ALTER TABLE edicts ADD COLUMN runtime_json TEXT NOT NULL DEFAULT '{}'"
"ALTER TABLE edicts ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"

# Phase 1 memorial 扩展
"ALTER TABLE memorials ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1"
"ALTER TABLE memorials ADD COLUMN parent_memorial_id TEXT"
"ALTER TABLE memorials ADD COLUMN review_status TEXT NOT NULL DEFAULT 'not_required'"
"ALTER TABLE memorials ADD COLUMN audit_json TEXT"
"ALTER TABLE memorials ADD COLUMN artifacts_json TEXT NOT NULL DEFAULT '[]'"
"ALTER TABLE memorials ADD COLUMN timeline_json TEXT NOT NULL DEFAULT '[]'"

# Phase 3 memorial 扩展
"ALTER TABLE memorials ADD COLUMN dag_node_id TEXT"
"ALTER TABLE memorials ADD COLUMN persona_id TEXT"

# USD → CNY 列重命名
"ALTER TABLE cost_ledger RENAME COLUMN cost_usd TO cost_cny"
"ALTER TABLE cost_budgets RENAME COLUMN budget_usd TO budget_cny"
"ALTER TABLE cost_budgets RENAME COLUMN spent_usd TO spent_cny"
```

### Phase 1-3 新增表

| 表名 | Phase | 用途 |
|------|-------|------|
| `decrees` | 1 | 审批决策记录 |
| `memory_entries` | 2 | Persona 记忆存储 |
| `cost_ledger` | 2 | LLM 调用成本记录 |
| `cost_budgets` | 2 | 预算配置 |
| `providers` | 2 | LLM Provider 注册 |
| `plugins` | 2 | 插件元数据 |
| `dag_executions` | 3 | DAG 执行记录 |
| `dag_nodes` | 3 | DAG 节点状态 |

---

## 4. Phase 1 — 治理与异步调度

### 4.1 EventBus (`bus/event_bus.py`)

**职责**：进程内异步事件总线，所有模块通过事件解耦。

```
EventBus.emit(event) → 持久化到 events 表 → fan-out 到注册 handler（按 priority 排序）
```

**核心接口**：

| 方法 | 说明 |
|------|------|
| `on(event_type, handler, priority=100)` | 注册 handler，数字越小优先级越高 |
| `off(event_type, handler)` | 注销 handler |
| `emit(EventEnvelope)` | 发射事件（先持久化，再 fan-out） |

**设计要点**：
- Handler 超时 30 秒自动跳过
- 异常隔离：单个 handler 失败不影响其他 handler
- 自动持久化：emit 时写入 `events` 表

### 4.2 Scheduler (`scheduler/scheduler.py`)

**职责**：接收 `edict.submitted` 事件，按调度策略发射 `edict.scheduled`。

```
edict.submitted → Scheduler.handle_submitted
  ├── type=immediate → 立即 emit edict.scheduled
  ├── type=once → 延迟后 emit（asyncio.sleep）
  └── type=cron → 循环定时 emit（croniter 解析）
```

**依赖**：`croniter` 解析 cron 表达式

**核心接口**：

| 方法 | 说明 |
|------|------|
| `schedule(edict, memorial_id?)` | 按 schedule 配置调度 |
| `cancel(job_id)` | 取消调度任务 |
| `list_jobs()` | 列出活跃任务 |
| `start()` / `stop()` | 生命周期管理 |

### 4.3 Planner (`planner/planner.py`)

**职责**：接收 `edict.scheduled`，分解任务生成 Plan，发射 `plan.completed`。

```
edict.scheduled → Planner.handle_scheduled
  ├── needs_planning(edict) == False → passthrough plan（单任务）
  └── LLM 规划 → 返回多任务 Plan（含 depends_on / tools_required / assigned_official）
```

**规划启发**：
- `skip_planning` 元数据标记 → 跳过
- 目标 < 100 字符 + 无约束 + 无输出格式 → 直通
- 其他 → LLM 规划

**官员分配**：`OfficialSelector` 自动匹配任务→官员映射，默认 `bingbu`。

### 4.4 Executor 重构 (`executor/executor.py`)

**职责**：接收 `plan.completed`，编排 Agent 执行。

```
plan.completed → Executor.handle_plan_completed
  ├── 单任务 Plan → execute_edict()（单任务快路径）
  └── 多任务 Plan → _execute_dag()（创建 DAG → DAGScheduler.run）
```

**单任务执行流**：
```
execute_edict(edict, plan, memorial)
  ├── SESSION_START hook
  ├── BEFORE_AGENT_START hook（注入 memory_history）
  ├── Agent.execute(edict, on_event, history, user_content)
  ├── emit execution.completed / execution.failed
  ├── AGENT_END hook
  └── SESSION_END hook
```

### 4.5 Auditor (`auditor/`)

**职责**：接收 `execution.completed`，执行两层审计，发射 `audit.completed`。

```
execution.completed → Auditor.handle_execution_completed
  ├── review_policy="never" → verdict=pass
  ├── review_policy="always" → rules + LLM review → 强制 needs_review
  ├── review_policy="on_flag" → rules engine → 若 flag 则 LLM review
  └── review_policy="on_failure" → 仅失败时审计
```

**Layer 1 — RulesEngine** (`auditor/rules.py`): 快速规则检查（Token 超预算、超时等）

**Layer 2 — LLMReviewer** (`auditor/reviewer.py`): LLM 复审（仅 flag 时触发）

**审计结果影响**：

| verdict | 效果 |
|---------|------|
| `pass` | memorial 状态不变 |
| `flag` | memorial → `needs_review`，review_status → `pending` |
| `block` | memorial → `failed`，附 audit reasons |

**自动结案**：审计通过 + 非 cron 敕令 → 自动设 edict 为 completed。

### 4.6 Notifier + WebSocket (`notifier/`)

**职责**：接收 `audit.completed` 和 `execution.failed`，通过 WebSocket 和外部通道推送。

```
audit.completed → Notifier.handle_audit_completed → debounced WS broadcast + external channels
execution.failed → Notifier.handle_execution_failed → WS broadcast + external channels
```

**WebSocket**：
- `GET /api/ws` — WebSocket 端点
- `register_ws()` / `unregister_ws()` — 连接管理
- 防抖广播（0.5 秒去重同一 memorial）

**外部通道分发**：
- 根据 `edict.dispatch.channels` 配置决定发送到哪些通道
- 每种通道有对应的 renderer（`render_feishu`, `render_dingtalk`, `render_email`）

**ChannelRegistry** (`channel_registry.py`):
- 统一注册中心
- 内置限流：每通道每分钟最多 10 条
- 支持 `send_all()` 和 `send_to()` 两种分发模式

### 4.7 Decree 审批系统 (`executor/approvals.py`)

**职责**：管理 `needs_review` 状态的奏折审批流。

```python
class Decree(BaseModel):
    memorial_id: str
    action: "approve" | "reject" | "retry" | "amend" | "cancel"
    comment: str | None
    amended_goal: str | None
    actor: str = "human"
```

**审批动作**：

| 动作 | 效果 |
|------|------|
| `approve` | memorial → completed, review_status → approved |
| `reject` | memorial → failed, review_status → rejected |
| `retry` | 创建新 memorial（attempt+1），触发 decree.retry 事件 |
| `amend` | 创建新 edict（amended_goal），触发 edict.submitted |
| `cancel` | memorial → cancelled |

**实时审批**：
- `wait_for_approval(memorial_id)` — 阻塞等待（asyncio.Event），超时 5 分钟
- `submit_decree(decree)` — 唤醒等待者

### 4.8 生命周期 Hooks (`executor/hooks.py`)

**职责**：Agent 执行过程中的拦截点。

```python
class HookType(str, Enum):
    BEFORE_AGENT_START = "before_agent_start"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    AGENT_END = "agent_end"
    BEFORE_ITERATION = "before_iteration"
    BEFORE_COMPACTION = "before_compaction"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
```

**HookResult**：
- `block: bool` — 是否阻断执行
- `reason: str | None` — 阻断原因
- `modified_args: dict | None` — 修改参数（如注入 memory_history）

**当前注册的 hooks**:

| Hook 点 | Handler | 优先级 | 说明 |
|----------|---------|--------|------|
| `BEFORE_AGENT_START` | `memory_manager.on_before_agent_start` | 50 | 注入记忆上下文 |
| `BEFORE_ITERATION` | `cost_manager.on_before_iteration` | 10 | 预算断路器 |
| `LLM_OUTPUT` | `cost_manager.on_llm_output` | 50 | 累积成本 |
| `AGENT_END` | `memory_manager.on_agent_end` | 100 | 持久化执行记忆 |

### 4.9 Persona 系统基础 (`persona/`)

**AgentPersona 模型**：
```python
class AgentPersona(BaseModel):
    id: str           # "bingbu" | "ducha" | "neige" | ...
    name: str
    department: str
    soul_path: Path   # SOUL.md — 性格/身份
    role_path: Path   # ROLE.md — 职责/能力
    memory_path: Path # MEMORY.md — 记忆
    skills_dir: Path | None
    tools_allowed / tools_denied: list[str]
    tool_tier_max: int
    can_delegate: bool
    delegates_to: list[str]
```

**PersonaLoader**：扫描 `personas/` 目录，读取 SOUL.md frontmatter 解析元数据。

**PromptBuilder — 8 层 System Prompt 注入**：
```
Layer 1: Base Identity（通用 AI 助手定义）
Layer 2: COURT.md（共享朝廷上下文）
Layer 3: SOUL.md（官员性格身份）
Layer 4: ROLE.md（官员职责能力）
Layer 5: MEMORY.md（官员个人记忆）
Layer 6: Court MEMORY.md（共享记忆）
Layer 7: Skills（技能注入）
Layer 8: Task Context（当前任务 ID）
```

**OfficialSelector — 任务→官员映射**：

| 任务类型 | 默认官员 |
|----------|---------|
| plan | neige（内阁） |
| execute | bingbu（兵部） |
| audit | ducha（都察） |
| notify | tongzheng（通政） |
| memory | wenyuan（文渊） |
| cost | hubu（户部） |

也支持基于关键词的智能匹配（`select_for_task`）。

---

## 5. Phase 2 — 平台化能力

### 5.1 Memory 模块 (`memory/`)

**职责**：为每个 Persona 提供记忆存储、检索、压缩、反思。

**MemoryEntry 模型**：
```python
class MemoryEntry(BaseModel):
    persona_id: str
    category: "observation" | "insight" | "entity" | "summary"
    content: str
    source: "agent" | "compaction" | "reflection"
    confidence: float = 1.0
    access_level: "private" | "shared" | "court"
```

**核心操作**：

| 方法 | 说明 |
|------|------|
| `store(entry, writer?)` | 存储记忆（含写权限检查） |
| `recall(query, requestor?)` | 检索记忆（含读权限过滤） |
| `compact(persona_id)` | LLM 压缩旧记忆为摘要 |
| `reflect(persona_id)` | 从 observation 生成 insight |
| `delete(id)` / `delete_batch(ids)` | 删除记忆 |

**Hook 集成**：
- `BEFORE_AGENT_START` → 注入相关记忆到 agent history
- `AGENT_END` → 提取 completed 执行的摘要存为 observation（仅 COMPLETED 状态 + 有 summary）

**EventBus 集成**：
- `execution.completed` → no-op（避免与 AGENT_END hook 重复）
- `audit.completed` → 若 flag/block，存 insight 到 ducha 记忆

**Access Control**：
- `MemoryAccessPolicy` — 每个 persona 的读写权限
- `MemoryAccessControl.filter_readable()` — 按权限过滤

### 5.2 Cost 管理 (`cost/`) — CNY 结算

**职责**：跟踪 LLM 调用成本，执行预算断路器。

```
LLM_OUTPUT hook → CostTracker 按 edict 累积
BEFORE_ITERATION hook → BudgetChecker 断路检查
execution.completed/failed → CostRecord 持久化
```

**CostTracker**：内存中按 edict_id 累积 token 和成本，执行结束后持久化。

**BudgetChecker**：
- 检查 `edict.runtime.cost_budget_cny`（单敕令预算）
- 检查 global / edict scope 的 `cost_budgets` 表
- 超预算时返回 `HookResult(block=True)`，触发 `cost.budget_exceeded` 事件

**API 端点**：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/cost/summary` | 成本汇总（支持 period/edict_id 过滤） |
| `GET` | `/api/cost/records` | 成本记录列表 |
| `GET` | `/api/cost/budget` | 预算状态 |
| `PUT` | `/api/cost/budget` | 设置预算 |
| `GET` | `/api/cost/export` | 导出成本数据 |

### 5.3 多 Provider 路由 (`providers/`)

**职责**：管理多个 LLM Provider，按策略路由请求。

**ProviderInfo 模型**：
```python
class ProviderInfo(BaseModel):
    name: str
    model: str
    api_base: str | None
    capabilities: list[ProviderCapability]  # chat | function_calling | vision | embedding
    status: str = "active"
    priority: int = 100    # 数字越小优先级越高
    rpm_limit / tpm_limit  # 配额限制
    cost_per_1k_prompt / cost_per_1k_completion  # 单价
```

**路由策略**：

| 策略 | 说明 |
|------|------|
| `priority` | 按 priority 字段排序（默认） |
| `cheapest` | 按 cost_per_1k_prompt 排序 |

**选择流程**：
```
get_client(requirements?)
  ├── 过滤 status=active 的 provider
  ├── 按 capabilities 过滤
  ├── 按 RPM/TPM 配额过滤（1 分钟窗口）
  ├── 按 strategy 选择最优
  └── 无匹配 → fallback 到 ConfigManager 活跃配置
```

### 5.4 Plugin 系统 (`plugins/`)

**职责**：可扩展的插件发现、加载和注册。

**PluginManifest**：
```python
class PluginManifest(BaseModel):
    name: str
    version: str
    type: "tool" | "hook" | "channel" | "provider" | "skill" | "command"
    entry_point: str
    permissions: list[str]
    sha256: str
```

**PluginApi — 统一注册门面**：
- `register_tool()` → ToolRegistry
- `register_hook()` → HookRegistry
- `register_channel()` → ChannelRegistry
- `register_provider()` → ProviderManager
- `register_skill()` → SkillsLoader
- `register_command()` → CLI 扩展

**发现机制**：`PluginLoader` 扫描 `plugins/` 目录下的 `manifest.json`。

### 5.5 通知通道扩展 (`notifier/channels/`)

| 通道 | 文件 | 配置环境变量 |
|------|------|-------------|
| 飞书 | `feishu.py` | `TIANSHU_FEISHU_WEBHOOK` |
| 钉钉 | `dingtalk.py` | `TIANSHU_DINGTALK_WEBHOOK` + `TIANSHU_DINGTALK_SECRET` |
| 邮件 | `email.py` | `TIANSHU_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/TO` |

**Protocol**：
```python
class NotificationChannel(Protocol):
    name: str
    async def send(self, message: dict, rendered: str) -> bool: ...
    async def close(self) -> None: ...
```

通道在 `lifespan` 中根据环境变量条件注册到 `ChannelRegistry`。

### 5.6 Persona 扩展

Phase 2 增加两个专职官员：

| 官员 | 目录 | 职能 |
|------|------|------|
| wenyuan（文渊） | `personas/wenyuan/` | 知识管理 + 记忆 |
| hubu（户部） | `personas/hubu/` | 成本/预算管理 |

每个官员的 `SOUL.md` 含 frontmatter 定义名称、部门、工具权限等。

---

## 6. Phase 3 — 多 Agent 与 DAG

### 6.1 DAG 执行引擎 (`dag/`)

**DAG 模型**：
```python
class DAGExecution(BaseModel):
    id: str                    # ULID
    edict_id: str
    plan_json: str             # Plan 原始 JSON
    status: str                # pending | running | completed | failed | cancelled
    root_memorial_id: str?     # 根奏折（聚合所有节点 usage）
    max_concurrency: int
    nodes: list[DAGNode]

class DAGNode(BaseModel):
    node_id: str               # 对应 PlanTask.task_id
    depends_on: list[str]      # 前置依赖
    status: DAGNodeStatus      # pending → running → completed/failed/cancelled
    assigned_official: str?    # 分配的官员
    memorial_id: str?          # 节点执行产生的奏折
    checkpoint_json: str?      # 执行检查点
```

**DAG 图操作** (`dag/graph.py`):

| 方法 | 说明 |
|------|------|
| `topological_sort()` | Kahn 算法拓扑排序，检测环 |
| `get_ready_nodes()` | 返回所有依赖已完成的 PENDING 节点 |
| `mark_running/completed/failed()` | 状态变更 |
| `propagate_failure()` | BFS 级联取消下游节点 |
| `is_complete()` | 检查所有节点是否终态 |

**Plan → DAG 转换** (`models/plan.py`):
```python
Plan.to_dag(edict_id, max_concurrency) → DAGExecution
```

### 6.2 Worker Pool (`executor/worker_pool.py`)

**职责**：`asyncio.Semaphore` 管理全局并发槽位。

```python
class WorkItem:
    id: str
    dag_execution_id: str
    node_id: str
    coro_factory: Callable[[], Awaitable[None]]

class WorkerPool:
    submit(item, on_complete?) → asyncio.Task   # 提交任务
    cancel(work_id) → bool                       # 取消任务
    shutdown() → None                            # 全部取消并等待
    status() → WorkerStatus                      # 状态快照
```

默认 `max_concurrency=4`（可通过 `TIANSHU_MAX_GLOBAL_CONCURRENCY` 配置）。

### 6.3 Lane 并发控制 (`executor/lanes.py`)

**职责**：两级 Semaphore 实现背压控制。

```
GlobalLane（全局并发上限，默认 8）
  └── SessionLane（每个 edict 的并发上限，默认 = edict.runtime.max_concurrency）
```

**LaneManager**：
- `get_session_lane(edict_id, max_concurrency)` — 获取/创建会话 lane
- `remove_session(edict_id)` — 清理
- `status()` — 全局 + 所有会话 lane 状态

### 6.4 DAGScheduler (`executor/dag_scheduler.py`)

**职责**：监控 DAG 状态，提交就绪节点到 WorkerPool。

```
DAGScheduler.run(edict, execution)
  ├── DAG.topological_sort()（环检测）
  ├── status → running
  ├── 调度循环:
  │   ├── get_ready_nodes() → 提交到 WorkerPool
  │   ├── on_node_complete:
  │   │   ├── 成功 → mark_completed → 调度下一批
  │   │   └── 失败 → mark_failed → propagate_failure → 取消下游
  │   └── is_complete() → 退出循环
  ├── 聚合 usage 到 root memorial
  └── emit execution.completed / execution.failed
```

**Worker** (`executor/worker.py`) — 单节点执行：
- 创建节点 memorial（dag_node_id + persona_id）
- 注入上游节点结果到 history
- 调用 `Agent.execute()` 并回收结果

### 6.5 取消/重试/检查点

**CascadeCanceller** (`executor/cancel.py`):
```
cancel(execution) → 遍历所有 RUNNING/PENDING 节点
  ├── RUNNING → pool.cancel(work_id) → mark CANCELLED → 级联下游
  └── PENDING/READY → mark CANCELLED
  最终 → execution.status = "cancelled"
```

**PartialRetrier** (`executor/retry.py`):
```
prepare_retry(execution, from_node_ids?)
  ├── 收集目标节点（默认所有 FAILED）
  ├── BFS 收集所有下游 CANCELLED/FAILED 节点
  ├── 重置为 PENDING（清空 error/时间戳）
  └── execution.status → "pending"
  → 调用方重新运行 DAGScheduler.run()
```

**CheckpointManager** (`executor/checkpoint.py`):
```python
class Checkpoint:
    iteration: int         # ReAct 循环进度
    messages: list[dict]   # 对话历史
    usage: UsageSummary    # 已消耗 Token

CheckpointManager.save(dag_execution_id, node_id, checkpoint)
CheckpointManager.load(dag_execution_id, node_id) → Checkpoint | None
```

### 6.6 会商系统 (`consultation/`)

**职责**：多 Persona 并行分析 + LLM 综合研判。

```
ConsultationSession.start(request)
  ├── 确定参与官员列表（request.persona_ids 或 全部）
  ├── asyncio.gather: 并行获取每个官员的 PersonaOpinion
  ├── Synthesizer.synthesize(request, opinions) → synthesis + decision
  └── 返回 ConsultationResponse
```

**数据模型**：
```python
ConsultationRequest:
    topic, context, edict_id?, persona_ids[], synthesizer_persona_id="neige"

PersonaOpinion:
    persona_id, persona_name, department, opinion, confidence, key_points[]

ConsultationResponse:
    id, status, opinions[], synthesis, decision
```

### 6.7 绩效评估 (`persona/evaluator.py`)

**职责**：从 memorials + cost_ledger 聚合 Persona 执行指标。

```python
class PersonaMetrics(BaseModel):
    persona_id: str
    total_executions: int
    completed / failed / cancelled: int
    success_rate: float       # 完成率 %
    total_tokens: int
    avg_tokens_per_execution: float
    total_cost_cny: float
    avg_duration_seconds: float
```

通过 SQL 聚合查询 `memorials WHERE persona_id = ?` 和 JOIN `cost_ledger` 计算。

---

## 7. Web 前端

### 7.1 新增页面（9 页）

| 页面 | 路径 | 功能 |
|------|------|------|
| `ApprovalQueuePage` | `/approvals` | 审批队列 — 查看待审核奏折，提交 Decree |
| `AuditDashboardPage` | `/audit` | 审计仪表盘 — 全局统计/per-edict Token 用量/近期审计 |
| `ConsultationPage` | `/consultation` | 会商 — 发起多 Persona 并行分析 |
| `CostDashboardPage` | `/cost` | 成本仪表盘 — 汇总/记录/预算/导出（CNY） |
| `DagBattleMapPage` | `/dag/:id` | DAG 战图 — 可视化 DAG 节点状态和依赖关系 |
| `MemoryDashboardPage` | `/memory` | 记忆管理 — 按 Persona 查看/搜索/删除/批量删除 |
| `PersonaDashboardPage` | `/personas` | 官员绩效 — 查看所有官员及其执行指标 |
| `ProviderDashboardPage` | `/providers` | Provider 管理 — 注册/配置/状态 |
| `SchedulerPage` | `/scheduler` | 调度监控 — 查看/取消调度任务 |

### 7.2 新增组件

| 目录 | 组件 | 说明 |
|------|------|------|
| `common/` | `ConnectionIndicator` | WebSocket 连接状态指示器 |
| `cost/` | 成本图表组件 | Token 用量/成本趋势图 |
| `dag/` | DAG 可视化组件 | 节点状态展示 + 依赖连线 |
| `decree/` | 审批操作组件 | Decree 提交表单（approve/reject/retry/amend/cancel） |

### 7.3 API 层 + Hooks 层

**API 层（`web/src/api/`）**：

| 文件 | 对应后端 |
|------|---------|
| `audit.ts` | `/api/audit/stats` |
| `consultations.ts` | `/api/consultations` |
| `cost.ts` | `/api/cost/*` |
| `dag.ts` | `/api/dag/*` |
| `decrees.ts` | `/api/decrees` |
| `memory.ts` | `/api/memory/*` |
| `personas.ts` | `/api/personas` |
| `providers.ts` | `/api/providers` |
| `scheduler.ts` | `/api/scheduler/*` |

**Hooks 层（`web/src/hooks/`）**：

| Hook | 说明 |
|------|------|
| `useApprovals` | 审批队列数据 + 提交 decree |
| `useAudit` | 审计统计数据 |
| `useConsultation` | 会商状态轮询 |
| `useCost` | 成本汇总/记录/预算 |
| `useDag` | DAG 执行详情 + 节点状态 |
| `useMemory` | 记忆 CRUD + 批量删除 |
| `usePersonas` | 官员列表 + 绩效指标 |
| `useProviders` | Provider CRUD |
| `useScheduler` | 调度任务列表 + 取消 |
| `useWebSocket` | WebSocket 连接管理 |
| `useWsQueryInvalidation` | WS 事件驱动 React Query 缓存失效 |

### 7.4 WebSocket 实时通信

```
前端 useWebSocket → 连接 /api/ws
  → 收到 audit.completed / execution.failed 消息
  → useWsQueryInvalidation 触发相关 query 刷新
  → ConnectionIndicator 显示连接状态
```

---

## 8. CLI 扩展

### 新增命令

| 命令文件 | 子命令 | 说明 |
|---------|--------|------|
| `cost.py` | `tianshu cost summary/records/budget` | 成本查询/预算管理 |
| `dag.py` | `tianshu dag get/cancel/retry` | DAG 管理 |
| `decree.py` | `tianshu decree submit` | 提交审批决策 |
| `event.py` | `tianshu event list` | 事件查询 |
| `plugin.py` | `tianshu plugin list/install` | 插件管理 |
| `provider.py` | `tianshu provider list/add/rm` | Provider 管理 |
| `schedule.py` | `tianshu schedule list/cancel` | 调度管理 |
| `watch.py` | `tianshu watch` | WebSocket 实时监控 |
| `worker.py` | `tianshu worker status` | Worker 状态 |

---

## 9. API 路由（Phase 1-3 新增）

### Scheduler

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/scheduler/jobs` | 列出调度任务 |
| `DELETE` | `/api/scheduler/jobs/{job_id}` | 取消调度任务 |

### Audit

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/audit/stats` | 审计统计 |

### Decree

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/decrees` | 提交审批决策 |

### Memory

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/memory/{persona_id}` | 查看 persona 记忆 |
| `POST` | `/api/memory/recall` | 搜索记忆 |
| `DELETE` | `/api/memory/{entry_id}` | 删除单条记忆 |
| `POST` | `/api/memory/batch-delete` | 批量删除记忆 |
| `GET` | `/api/memory/policies` | 获取访问策略 |
| `PUT` | `/api/memory/policies/{persona_id}` | 设置访问策略 |

### Cost

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/cost/summary` | 成本汇总 |
| `GET` | `/api/cost/records` | 成本记录列表 |
| `GET` | `/api/cost/budget` | 预算状态 |
| `PUT` | `/api/cost/budget` | 设置预算 |
| `GET` | `/api/cost/export` | 导出成本数据 |

### Provider

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/providers` | 列出 Provider |
| `POST` | `/api/providers` | 注册 Provider |
| `PUT` | `/api/providers/{name}` | 更新 Provider |
| `DELETE` | `/api/providers/{name}` | 删除 Provider |
| `GET` | `/api/providers/{name}/status` | Provider 状态 |

### Plugin

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/plugins` | 列出插件 |
| `GET` | `/api/plugins/{name}` | 获取插件 |
| `POST` | `/api/plugins/install` | 安装插件 |
| `PUT` | `/api/plugins/{name}/status` | 更新插件状态 |

### DAG

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/dag/by-edict/{edict_id}` | 按 edict 查 DAG |
| `GET` | `/api/dag/{dag_id}` | 获取 DAG 详情 |
| `POST` | `/api/dag/{dag_id}/cancel` | 取消 DAG |
| `POST` | `/api/dag/{dag_id}/retry` | 重试 DAG |

### Worker

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/workers` | 列出活跃 Worker |
| `GET` | `/api/workers/status` | Worker + Lane 状态 |

### Consultation

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/consultations` | 发起会商 |
| `GET` | `/api/consultations/{id}` | 获取会商结果 |

### Persona

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/personas` | 列出所有官员 |
| `GET` | `/api/personas/{id}/metrics` | 获取官员绩效 |

### WebSocket

| 方法 | 路径 | 说明 |
|------|------|------|
| `WS` | `/api/ws` | WebSocket 实时推送 |

---

## 10. 本轮优化（feat_phase1 尾声修复）

| 优化项 | 说明 |
|--------|------|
| 记忆写入过滤 | `on_agent_end` 仅对 COMPLETED + 有 summary 的执行写入；`handle_execution_completed` 改为 no-op 去冗余 |
| 记忆批量删除 | 新增 `POST /memory/batch-delete` 端点 |
| 事件分组修复 | memorial_id 在事件流中正确传播（gateway → scheduler → planner → executor） |
| 敕令详情布局 | 结案/废除按钮分离，操作更清晰 |
| USD → CNY | 全局切换 — 数据库列重命名 + 模型字段统一为 `cost_cny` / `budget_cny` |
| Persona frontmatter | 补全 hubu、wenyuan 的 SOUL.md frontmatter |
| Token 标签中文化 | 前端 Token 标签改为"输入/输出" |

---

## 11. App 启动流程（Phase 1-3 完整版）

### Lifespan 初始化序列

```
lifespan(app) → yield → shutdown
  1.  TianshuSettings()
  2.  Storage(db_path).init_db()       ← 建表 + 迁移（含 Phase 1-3 新表和列）
  3.  EventBus(storage)
  4.  HookRegistry()
  5.  ToolRegistry + register_builtins()
  6.  SkillsLoader
  7.  PersonaLoader(personas/).load_all()
  8.  PromptBuilder(personas_dir, skills)
  9.  ConfigManager(initial_state, agent_config)
  10. ProviderManager(storage, config_manager)
  11. Agent(config_manager, tools, skills, hooks, prompt_builder, provider_manager)
  12. WorkerPool(max_concurrency=8)
  13. LaneManager(max_global_concurrency=8)
  14. Executor(event_bus, storage, config_manager, hooks)
      → set_agent(agent), set_dag_scheduler(...), set_lane_manager(...)
  15. DAGScheduler(worker_pool, agent, storage, event_bus, persona_loader, prompt_builder)
  16. Auditor(event_bus, storage, config_manager)
  17. ChannelRegistry + 条件注册通道（feishu / dingtalk / email）
  18. Notifier(storage, channel_registry)
  19. ApprovalManager(event_bus, storage)
  20. MemoryManager(storage, config_manager, hook_registry)
  21. CostManager(storage, event_bus)
  22. ConsultationSession(persona_loader, config_manager, provider_manager)
  23. PerformanceEvaluator(storage)
  24. OfficialSelector(persona_loader)
  25. Planner(event_bus, storage, config_manager, official_selector)
  26. Scheduler(event_bus, storage)
  ─── EventBus 订阅注册 ───
  ─── PluginApi + PluginLoader 发现 ───
  ─── Hook 注册 ───
  ─── SkillsWatcher 启动 ───
  ─── Scheduler.start() ───
```

### EventBus 订阅清单

| 事件 | Handler | 优先级 |
|------|---------|--------|
| `edict.submitted` | `scheduler.handle_submitted` | 100 |
| `edict.scheduled` | `planner.handle_scheduled` | 50 |
| `plan.completed` | `executor.handle_plan_completed` | 100 |
| `execution.completed` | `auditor.handle_execution_completed` | 100 |
| `execution.completed` | `cost_manager.handle_execution_completed` | 150 |
| `execution.completed` | `memory_manager.handle_execution_completed` | 200 |
| `execution.failed` | `notifier.handle_execution_failed` | 100 |
| `execution.failed` | `cost_manager.handle_execution_failed` | 150 |
| `audit.completed` | `notifier.handle_audit_completed` | 100 |
| `audit.completed` | `memory_manager.handle_audit_completed` | 200 |
| `cost.budget_exceeded` | `notifier.handle_execution_failed` | 100 |

### Hook 注册清单

| Hook 类型 | Handler | 优先级 |
|-----------|---------|--------|
| `BEFORE_AGENT_START` | `memory_manager.on_before_agent_start` | 50 |
| `BEFORE_ITERATION` | `cost_manager.on_before_iteration` | 10 |
| `LLM_OUTPUT` | `cost_manager.on_llm_output` | 50 |
| `AGENT_END` | `memory_manager.on_agent_end` | 100 |

### 完整事件流

```
Gateway: POST /api/edicts
  │
  ├── 保存 Edict + Memorial
  └── emit: edict.submitted
         │
         ▼
    Scheduler.handle_submitted
         │
         ├── immediate → emit: edict.scheduled
         ├── once → delay → emit: edict.scheduled
         └── cron → loop → emit: edict.scheduled
                               │
                               ▼
                    Planner.handle_scheduled
                               │
                               ├── 简单任务 → passthrough plan
                               └── 复杂任务 → LLM 规划
                                      │
                                      └── emit: plan.completed
                                                  │
                                                  ▼
                                    Executor.handle_plan_completed
                                                  │
                                    ┌─────────────┴───────────────┐
                                    ▼                             ▼
                             单任务快路径                     多任务 DAG
                                    │                             │
                             execute_edict()              DAGScheduler.run()
                                    │                             │
                      ┌─────────────┤                   ┌─────────┤
                      ▼             ▼                   ▼         ▼
               HOOKS: START   Agent.execute       WorkerPool   Worker.execute_node
                      │             │                   │         │
                      ▼             ▼                   ▼         ▼
               HOOKS: END    emit: execution.*    on_complete → 调度下一批
                                    │
                      ┌─────────────┴──────────────┐
                      ▼                            ▼
              execution.completed          execution.failed
                      │                            │
         ┌────────────┼──────────┐        ┌───────┤
         ▼            ▼          ▼        ▼       ▼
      Auditor     CostMgr    MemoryMgr  Notifier CostMgr
         │
         └── emit: audit.completed
                      │
              ┌───────┤
              ▼       ▼
          Notifier  MemoryMgr
```

### Graceful Shutdown

```
shutdown:
  1. agent.request_shutdown()
  2. skills_watcher.stop()
  3. scheduler.stop()         ← 取消所有 cron/delayed 任务
  4. worker_pool.shutdown()   ← 取消所有 worker
  5. executor.shutdown()      ← 取消所有 running_tasks
  6. storage.close()
```

---

## 12. 新增环境变量

| 变量 | 默认值 | 说明 | Phase |
|------|--------|------|-------|
| `TIANSHU_MAX_GLOBAL_CONCURRENCY` | 8 | 全局最大并发 | 3 |
| `TIANSHU_FEISHU_WEBHOOK` | "" | 飞书 Webhook URL | 2 |
| `TIANSHU_DINGTALK_WEBHOOK` | "" | 钉钉 Webhook URL | 2 |
| `TIANSHU_DINGTALK_SECRET` | "" | 钉钉签名密钥 | 2 |
| `TIANSHU_SMTP_HOST` | "" | SMTP 服务器 | 2 |
| `TIANSHU_SMTP_PORT` | 587 | SMTP 端口 | 2 |
| `TIANSHU_SMTP_USERNAME` | "" | SMTP 用户名 | 2 |
| `TIANSHU_SMTP_PASSWORD` | "" | SMTP 密码 | 2 |
| `TIANSHU_SMTP_FROM` | "" | 发件人 | 2 |
| `TIANSHU_SMTP_TO` | "" | 收件人（逗号分隔） | 2 |

---

## 13. 新增依赖

| 依赖 | 用途 | Phase |
|------|------|-------|
| `croniter` | cron 表达式解析 | 1 |
| `watchdog` | 技能文件热重载（可选） | 1 |
