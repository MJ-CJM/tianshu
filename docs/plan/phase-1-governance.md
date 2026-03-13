# Phase 1：引入治理与异步调度

> 对应架构设计 §1.5 Phase 1、§2.2、§8.2

---

## 目标

在 Phase 0 闭环基础上，引入 EventBus、Scheduler、Planner、Auditor、Notifier 和人工复核机制，将系统从同步串行执行升级为事件驱动的异步调度执行。

## 本阶段制度映射

| 部院 | 模块 | 本阶段实现内容 |
|------|------|--------------|
| 内阁 | `Planner` | LLM 驱动的任务拆解与规划 |
| 兵部（增强） | `Executor` | 事件化改造，订阅事件触发执行 |
| 都察院 | `Auditor` | 规则引擎 + LLM 复核（pass/flag/block） |
| 通政司 | `Notifier` | WebSocket 实时推送、渲染管线、待批推送 |
| 刑部（内嵌） | `Auditor.rules` + 错误恢复 | 失败处理、风险封禁、工具权限拦截 |
| — | `EventBus` | 进程内领域事件总线 |
| — | `Scheduler` | 即时/一次性/cron 调度 |
| — | `Decree`（批红） | 人工复核与审批闭环 |
| 御案台（增强） | `Web Dashboard` | WebSocket 实时 + 批红台 + 事件时间线 |
| 御案台（增强） | `CLI` | 批红操作 + 实时监听 + 事件查询 + 调度管理 |

## 本阶段参考来源

| 参考 | 设计点 | 落点 Step |
|------|--------|----------|
| [NanoBot-5] | 两阶段结构化审计（规则 + LLM 复核） | Step 1.6 |
| [DeepAgents-2][DeepAgents-3] | 结构化任务拆解 + 上下文裁剪 | Step 1.4 |
| [OpenClaw-5] | 生命周期钩子体系（before/after 模式） | Step 1.9 |
| [OpenClaw-3] | 执行中实时审批（allow-once/allow-always） | Step 1.8 |
| [OpenClaw-2] | 多层 Tool Policy Pipeline（实施） | Step 1.9（before_tool_call 钩子） |
| [CoPaw-7] | 消息渲染管线 | Step 1.7 |
| [CoPaw-8] | 防抖与通道治理 | Step 1.7 |
| [ZeroClaw] | 统一观察事件与指标语义 | Step 1.2 |

## 运行方式

Web 服务 + 事件驱动调度。在 Phase 0 的 FastAPI 服务基础上集成 Scheduler 和 EventBus，支持即时/一次性/cron 调度。

## 前置条件

- Phase 0 全部 Step 通过验收
- APScheduler 4.x 依赖可用

## Phase 验收标准（§8.2）

- [ ] 支持即时、一次性、cron 三类调度
- [ ] 有统一 `EventBus` 和事件日志
- [ ] `Planner`、`Auditor`、`Notifier` 可独立订阅事件工作
- [ ] 人工复核流可将任务置为 `NEEDS_REVIEW`
- [ ] 调度、执行、审计、通知的 Phase 表述在文档与实现中一致
- [ ] Web 界面支持 WebSocket 实时状态推送、批红审批、事件时间线
- [ ] CLI 支持 `watch`（实时监听）、`decree submit`（批红）、`event list`（事件查询）、`schedule list/cancel`（调度管理）

---

## Step 拆分

### Step 1.1 — 数据模型扩展：批红（Decree）+ 事件信封（EventEnvelope）

**目标**：扩展 Edict/Memorial 为 Phase 1 完整字段，新增 Decree 和 EventEnvelope 模型。

**涉及文件**
```
src/tianshu/models/
  __init__.py
  edict.py
  memorial.py
  decree.py
  events.py
```

**依赖**：无（可在 Phase 0 models.py 基础上重构为 models/ 包）

**验收条件**
- [ ] `Edict` 扩展 Phase 1 字段：`idempotency_key`、`source`、`submitter`、`constraints`、`output_format`、`priority`、`review_policy`、`schedule`（EdictSchedule）、`dispatch`（EdictDispatch）、`runtime`（EdictRuntime）、`metadata`（§3.2）
- [ ] `Memorial` 扩展 Phase 1 字段：`attempt`、`parent_memorial_id`、`review_status`、`audit`（AuditResult）、`artifacts`、`timeline`（§3.3）
- [ ] 新增 `Decree` 模型：`id`、`memorial_id`、`action`、`comment`、`amended_goal`、`actor`、`created_at`（§3.4）
- [ ] 新增 `EventEnvelope` 模型：`event_id`、`event_type`、`edict_id`、`memorial_id`、`attempt`、`timestamp`、`producer`、`payload`（§3.5）
- [ ] `TaskStatus` 枚举新增 `SCHEDULED`、`PLANNING`、`AUDITING`、`NEEDS_REVIEW`（§3.1）
- [ ] Phase 0 模型字段保持向后兼容（新字段有默认值）
- [ ] 单元测试覆盖新模型与序列化

**复杂度**：中

---

### Step 1.2 — EventBus 实现

**目标**：实现进程内事件总线，支持事件发射、订阅和 SQLite 事件日志持久化。

**涉及文件**
```
src/tianshu/bus/
  __init__.py
  event_bus.py
```

**依赖**：Step 1.1

**验收条件**
- [ ] 实现 `emit(event)` / `on(event_type, handler)` / `off(event_type, handler)` 接口（§6.1）
- [ ] 事件使用 `EventEnvelope` 统一信封（§3.5）
- [ ] 支持异步处理器（`async def handler`）
- [ ] 同一 `memorial_id` 内事件保持追加顺序（§3.5）
- [ ] 事件投递语义为 at-least-once（§3.5）
- [ ] 事件自动持久化到 SQLite `events` 表（Phase 0 已有 SQLite 存储层）
- [ ] 处理器异常不影响其他处理器执行
- [ ] 单元测试覆盖发射、订阅、持久化、异常隔离

**复杂度**：中

---

### Step 1.3 — Scheduler 调度器

**目标**：基于 APScheduler 实现即时/一次性/cron 三类调度，支持调度状态持久化。

**涉及文件**
```
src/tianshu/scheduler/
  __init__.py
  scheduler.py
  job_store.py
```

**依赖**：Step 1.1 + Step 1.2

**验收条件**
- [ ] 实现 `schedule(edict)` / `cancel(job_id)` / `start()` / `stop()` 接口（§6.2）
- [ ] 支持 `immediate`、`once`、`cron` 三种调度模式（§6.2）
- [ ] 调度触发时发射 `edict.scheduled` 事件（§3.5）
- [ ] 调度状态持久化到 SQLite `scheduler_jobs` 表
- [ ] misfire 策略可配置（§6.2）
- [ ] 同一 `job_id` 不得并发重复执行（§6.2）
- [ ] 进程重启后能恢复未完成的调度任务
- [ ] 单元测试覆盖三种模式、misfire、持久化恢复

**复杂度**：高

---

### Step 1.4 — 内阁：Planner 模块

**目标**：实现 LLM 驱动的任务拆解，输出结构化 Plan。

**涉及文件**
```
src/tianshu/planner/
  __init__.py
  planner.py
  prompts.py
src/tianshu/models/plan.py
```

**依赖**：Step 1.2 + LLM Client（Phase 0 已有）

**验收条件**
- [ ] `Plan` 模型包含 `tasks`、`depends_on`、`tools_required`、`can_run_parallel`、`estimated_tokens`、`priority_order`（§5.2）
- [ ] Planner 订阅 `edict.scheduled` 事件，判断是否需要预规划（§2.3）
- [ ] 预规划完成后发射 `plan.completed` 事件
- [ ] 简单任务直接跳过 Planner，进入执行（§2.3）
- [ ] 过滤不必要上下文，减少执行污染 [DeepAgents-2] [DeepAgents-3]
- [ ] 只规划不执行，不调用业务工具（§5.2 边界）
- [ ] 单元测试覆盖规划输出结构、跳过逻辑

**复杂度**：中

---

### Step 1.5 — 兵部：Executor 事件化改造

**目标**：将 Phase 0 的 Agent Loop 改造为事件驱动模式，订阅事件触发执行，发射执行过程事件。

**涉及文件**
```
src/tianshu/executor/
  __init__.py
  agent.py（从 Phase 0 agent.py 迁移）
  executor.py
```

**依赖**：Step 1.2 + Step 1.4

**验收条件**
- [ ] Executor 订阅 `edict.scheduled` / `plan.completed` 事件触发执行
- [ ] 执行过程发射 `execution.started`、`tool.started`、`tool.completed`、`execution.completed` / `execution.failed` 事件（§3.5）
- [ ] 支持按 Plan 顺序执行子任务（如果有 Plan）
- [ ] 无 Plan 时直接进入 ReAct Loop（兼容 Phase 0 行为）
- [ ] Memorial 状态变迁：`SCHEDULED` → `RUNNING` → `COMPLETED` / `FAILED`
- [ ] 单元测试覆盖事件驱动执行流

**复杂度**：中

---

### Step 1.6 — 都察院：Auditor 模块

**目标**：实现两层审计机制（规则引擎 + LLM 复核），输出 pass/flag/block。

**涉及文件**
```
src/tianshu/auditor/
  __init__.py
  auditor.py
  rules.py
  reviewer.py
```

**依赖**：Step 1.2 + Step 1.5

**验收条件**
- [ ] Auditor 订阅 `execution.completed`、`tool.completed` 等事件（§5.4）
- [ ] 规则引擎（同步快速判断）：检查越权、预算、工具风险等硬规则（§5.4）
- [ ] LLM 复核（仅对可疑项）：结构化复核目标偏离、结果异常 [NanoBot-5]
- [ ] 输出 `pass` / `flag` / `block` 三种结论（§5.4）
- [ ] `flag` 将 Memorial 置为 `NEEDS_REVIEW`（§3.1）
- [ ] `block` 终止流转，Memorial 置为 `FAILED`
- [ ] 审计完成后发射 `audit.completed` 事件
- [ ] 单元测试覆盖三种结论路径

**复杂度**：高

---

### Step 1.7 — 通政司：Notifier 模块

**目标**：实现渲染管线和通知投递，支持 WebSocket 实时推送 + webhook。

**涉及文件**
```
src/tianshu/notifier/
  __init__.py
  notifier.py
  renderer.py
```

**依赖**：Step 1.2 + Step 1.6

**验收条件**
- [ ] Notifier 订阅 `audit.completed`、`execution.completed`、`execution.failed` 事件（§5.5）
- [ ] 渲染管线：从 Memorial 提取用户关心的信息，过滤工具调用细节 [CoPaw-7]
- [ ] WebSocket 实时推送：任务执行状态和结果实时推送到 Web 客户端
- [ ] webhook 支持：可配置的外部回调通知
- [ ] 优先级递送：urgent 立即推送，normal 可合并（§5.5）
- [ ] 待批奏折推送：`NEEDS_REVIEW` 的 Memorial 推送给用户催促批红（§5.5）
- [ ] 防抖：合并短时间内的连续状态更新 [CoPaw-8]
- [ ] 通知失败不回滚执行结果（§5.5 边界）
- [ ] 单元测试覆盖渲染、投递、防抖

**复杂度**：中

---

### Step 1.8 — 批红机制：人工复核与 Decree API

**目标**：实现 `NEEDS_REVIEW` 状态处理和 Decree REST API，完成批红决策闭环。

**涉及文件**
```
src/tianshu/gateway/api.py（扩展 Decree 端点）
src/tianshu/executor/approvals.py
```

**依赖**：Step 1.6 + Step 1.7

**验收条件**
- [ ] `GET /api/memorials?status=NEEDS_REVIEW` 列出所有待批 Memorial
- [ ] `POST /api/decrees` 提交批红
- [ ] 支持 5 种批红动作：`approve` / `reject` / `retry` / `amend` / `cancel`（§3.4）
- [ ] 各动作正确触发状态变迁（§3.4 批红决策链路表）
- [ ] `retry` 创建新 Memorial（`attempt+1`，`parent_memorial_id` 指向当前）
- [ ] `amend` 基于 `amended_goal` 创建新 Edict
- [ ] 执行中实时审批：T3 工具调用暂停等待批红，支持 `allow-once` / `allow-always` [OpenClaw-3]
- [ ] 审批字段从系统内部生成，不从用户输入直接解析（安全约束）（§3.4）
- [ ] 集成测试覆盖批红决策链路

**复杂度**：中

---

### Step 1.9 — 都察院/兵部：生命周期钩子体系

**目标**：实现 6 个核心生命周期钩子，让 Auditor/Memory/CostManager 通过钩子接入执行流。

**涉及文件**
```
src/tianshu/executor/agent.py（增强，注入钩子触发点）
src/tianshu/executor/executor.py（增强）
```

**依赖**：Step 1.5

**验收条件**
- [ ] 实现 Phase 1 引入的 6 个核心钩子（§5.4）：
  - `before_agent_start`：Agent 开始执行前
  - `before_tool_call`：工具调用前，支持拦截（`block: true`）和修改参数
  - `after_tool_call`：工具调用后，记录和告警
  - `llm_output`：收到 LLM 响应后，审计和 Token 统计
  - `agent_end`：Agent 执行结束，后处理和存储
- [ ] 同一钩子的多个处理器按优先级顺序执行
- [ ] `before_*` 钩子可返回拦截指令，`after_*` 钩子只能记录不能拦截
- [ ] 钩子注册/注销 API
- [ ] 处理器异常不影响主执行流
- [ ] 单元测试覆盖钩子触发、拦截、优先级排序

**复杂度**：高

---

### Step 1.10 — Skills 热重载

**目标**：实现 SKILL.md 文件监听和热重载，修改后自动刷新。

**涉及文件**
```
src/tianshu/skills/loader.py（增强）
```

**依赖**：独立（基于 Phase 0 Skills Loader）

**验收条件**
- [ ] 监听 Skills 目录的文件变更（创建、修改、删除）
- [ ] 变更后防抖（默认 1 秒），避免频繁重载
- [ ] 重载时重新执行资格检查
- [ ] 重载不影响正在执行的任务（下次任务使用新 Skills）
- [ ] 日志记录 Skills 重载事件
- [ ] 单元测试覆盖监听、防抖、重载

**复杂度**：低

---

### Step 1.11 — 御案台：WebSocket 实时推送 + 状态实时刷新

**目标**：用 WebSocket 替代 HTTP 轮询，任务状态变更实时推送到浏览器。

**涉及文件**
```
web/src/
  api/websocket.ts                # WebSocket 客户端（自动重连）
  stores/wsStore.ts               # 连接状态
  hooks/useWebSocket.ts           # React Hook
  components/realtime/
    ConnectionIndicator.tsx       # 页头连接状态指示
    LiveStatusBadge.tsx           # 实时状态徽章
  pages/EdictListPage.tsx         # 改造：接入实时更新
  pages/EdictDetailPage.tsx       # 改造：接入实时更新
```

**依赖**：Step 1.7（通政司 Notifier WebSocket 端点）

**验收条件**
- [ ] 页面加载时自动连接 WebSocket（`WS /api/ws`）
- [ ] 断线指数退避重连（1s → 2s → 4s，上限 30s）
- [ ] 页头连接状态指示器（已连接 / 断开 / 重连中）
- [ ] 任务列表页：Edict 状态变更时实时更新行数据，无需刷新
- [ ] 任务详情页：实时展示状态流转
- [ ] WebSocket 失败时自动降级为 HTTP 轮询

**复杂度**：中

---

### Step 1.12 — 御案台：批红台（Approval Queue + Decree 界面）

**目标**：提供专用审批界面，用户查看待批奏折（`NEEDS_REVIEW`）并提交批红。

**涉及文件**
```
web/src/
  api/decrees.ts                  # Decree API
  pages/ApprovalQueuePage.tsx     # 待批列表页
  components/decree/
    DecreeModal.tsx               # 批红操作弹窗
    ApprovalList.tsx              # 待批卡片列表
  components/layout/Sidebar.tsx   # 修改：加"批红台"导航 + 角标
  App.tsx                         # 修改：加 /approvals 路由
```

**依赖**：Step 1.8（Decree API）+ Step 1.11（WebSocket 推送审批通知）

**验收条件**
- [ ] 侧栏"批红台"导航项，角标显示待批数量
- [ ] 待批列表按优先级 + 等待时长排序
- [ ] 每张待批卡片展示：Edict 目标、摘要、审计问题、等待时长
- [ ] 点击卡片弹出批红弹窗，含 5 种操作按钮：approve / reject / retry / amend / cancel
- [ ] `amend` 显示文本框填写 `amended_goal`
- [ ] `cancel` 需二次确认
- [ ] 所有批红通过 `POST /api/decrees` 提交，含错误处理
- [ ] WebSocket 实时推送新审批请求到队列

**复杂度**：中

---

### Step 1.13 — 御案台：事件时间线（起居注）

**目标**：展示 Edict/Memorial 的完整事件时间线，可视化任务生命周期。

**涉及文件**
```
web/src/
  pages/EventTimelinePage.tsx
  components/memorial/MemorialTimeline.tsx
  pages/EdictDetailPage.tsx       # 修改：嵌入时间线
```

**依赖**：Step 1.2（EventBus 事件持久化）+ Step 1.5（Executor 发射执行事件）

**验收条件**
- [ ] Ant Design Timeline 组件渲染 `Memorial.timeline` 事件列表
- [ ] 每个事件项展示：时间戳、事件类型、生产者、载荷摘要
- [ ] 颜色编码：绿色成功、红色失败、黄色警告、蓝色信息
- [ ] 任务详情页内嵌时间线；`/edicts/{id}/timeline` 独立全屏页
- [ ] WebSocket 推送新事件时自动滚动到最新
- [ ] 载荷可展开查看 JSON 详情

**复杂度**：低

---

### Step 1.14 — 御案台：CLI 治理指令

**目标**：扩展 CLI，支持批红操作、实时监听、事件查询、调度管理。

**涉及文件**
```
src/tianshu/cli/commands/
  decree.py                       # tianshu decree submit/list + tianshu memorial review
  watch.py                        # tianshu watch <edict_id>（WebSocket 实时监听）
  event.py                        # tianshu event list
  schedule.py                     # tianshu schedule list/cancel
```

**依赖**：Step 0.12（CLI 基础）+ Step 1.7（WebSocket）+ Step 1.8（Decree API）+ Step 1.2（EventBus）+ Step 1.3（Scheduler）

**验收条件**
- [ ] `tianshu memorial review` 列出待批奏折（`NEEDS_REVIEW`），按优先级排序
- [ ] `tianshu decree submit --memorial-id <id> --action approve|reject|retry|amend|cancel [--comment "..."]`
- [ ] `--action amend --amended-goal "新目标"` 支持追加指令
- [ ] `tianshu watch <edict_id>` 通过 WebSocket 实时打印执行事件（状态变更、工具调用、LLM 摘要）
- [ ] watch 使用 rich Live Display 实时更新状态面板（状态、已用时间、已用 Token）
- [ ] 任务完成/失败时自动断开 WebSocket 并打印结果摘要；Ctrl+C 优雅断开
- [ ] `tianshu event list --edict-id <id> [--type TYPE]` 查询事件日志，按时间排序
- [ ] `tianshu schedule list` 列出调度任务；`tianshu schedule cancel <job_id>` 取消调度
- [ ] `tianshu edict submit --schedule-type cron --cron "0 2 * * *" --goal "..."` 提交周期任务
- [ ] 单元测试覆盖批红、watch 消息解析、事件查询

**复杂度**：中

---

## Step 依赖关系图

```
                    1.1 数据模型扩展
                     ├───────────────────────────────┐
                     v                               v
                  1.2 EventBus ──────────────────> 1.3 Scheduler
                     │
                     ├──> 1.4 Planner
                     │        │
                     │        v
                     └──> 1.5 Executor 事件化 ──────────> 1.9 生命周期钩子
                              │
                              v
                           1.6 Auditor
                              │
                              v
                           1.7 Notifier ──────────────> 1.11 WebSocket 实时
                              │                              │
                              v                              v
                           1.8 人工复核与批红 ──────────> 1.12 批红台

                    1.2 EventBus ──┐
                    1.5 Executor ──┼──> 1.13 事件时间线

                    0.12 CLI 基础 ──┐
                    1.7 WebSocket ──┤
                    1.8 Decree API ─┼──> 1.14 CLI 治理指令
                    1.2 EventBus ───┤
                    1.3 Scheduler ──┘

                    1.10 Skills 热重载（独立）
```

**可并行组**：
- 组 A：Step 1.3（Scheduler）— 依赖 1.1 + 1.2
- 组 B：Step 1.4（Planner）→ 1.5（Executor）→ 1.6 → 1.7 → 1.8（主执行链）
- 组 C：Step 1.9（钩子体系）— 依赖 1.5
- 组 D：Step 1.10（Skills 热重载）— 全程独立
- 组 E：Step 1.11（WebSocket 实时）→ 1.12（批红台）— 依赖 1.7 / 1.8
- 组 F：Step 1.13（事件时间线）— 依赖 1.2 + 1.5
- 组 G：Step 1.14（CLI 治理指令）— 依赖 0.12 + 1.2 + 1.3 + 1.7 + 1.8

Step 1.2 是核心汇聚点，后续大部分 Step 依赖它。Step 1.11/1.12/1.13 可在主执行链完成后并行开发。

## 测试策略

| 层次 | 覆盖范围 | 工具 |
|------|---------|------|
| 单元测试 | 每个模块独立验证 | pytest |
| 集成测试 | EventBus 驱动的完整链路（Scheduler → Planner → Executor → Auditor → Notifier） | pytest + mock |
| 端到端测试 | Web 服务 + API 提交任务 + API 批红 完整流程 | pytest + httpx TestClient |

## 风险

| 风险 | 缓解措施 |
|------|---------|
| APScheduler 4.x API 不稳定 | 封装 Scheduler 接口，隔离对 APScheduler 的直接依赖 |
| 事件驱动调试困难 | 事件日志 SQLite 持久化，可事后查询分析 |
| 生命周期钩子执行顺序导致死锁 | 钩子处理器设超时，before_* 钩子不允许调用其他钩子 |
| 批红机制的安全漏洞 | 审批字段严格从系统内部生成，单元测试覆盖注入场景 |
