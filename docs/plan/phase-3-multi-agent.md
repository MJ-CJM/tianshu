# Phase 3：多 Agent 与分布式扩展

> 对应架构设计 §1.5 Phase 3、§8.4

---

## 目标

引入 DAG 执行引擎和多 Agent 并发能力，将存储层迁移到 PostgreSQL，支持 K8s 生产级部署和水平扩缩容，可选集成 Temporal 实现 Durable Workflow。

## 本阶段制度映射

| 部院 | 模块 | 本阶段实现内容 |
|------|------|--------------|
| 兵部（增强） | `Executor` | DAG 引擎、多 Agent 并发、Lane-based 控制 |
| 工部（增强） | `Storage` | PostgreSQL 迁移、K8s 部署 |
| 内阁（增强） | `Planner` | DAG 拓扑输出、多 Agent 资源协调 |
| 御案台（增强） | `Web Dashboard` | DAG 作战图可视化监控 |
| 御案台（增强） | `CLI` | DAG 拓扑展示 + Worker 管理 |

## 本阶段参考来源

| 参考 | 设计点 | 落点 Step |
|------|--------|----------|
| [OpenClaw-4] | Lane-based 并发控制（session lane + global lane） | Step 3.3 |
| [NanoBot-3] | 工具裁剪与级联取消（多 Agent 场景） | Step 3.2 + Step 3.4 |
| [DeepAgents-2][DeepAgents-3] | 多子代理并行 + 上下文裁剪分派 | Step 3.2 |

## 运行方式

容器集群 / K8s / Temporal。系统可水平扩展，支持多实例一致性。基础 Docker 容器化已在 Phase 0 完成。

## 前置条件

- Phase 2 全部 Step 通过验收
- PostgreSQL 实例可用（Step 3.5 起）

## Phase 验收标准（§8.4）

- [ ] 支持 DAG 执行和多 Agent 并发
- [ ] 支持更强的取消、重试和恢复能力
- [ ] 容器化部署下仍保持统一任务契约和事件语义
- [ ] Web 界面提供 DAG 作战图（ReactFlow 可视化）
- [ ] CLI 支持 `dag show`（ASCII DAG 拓扑）、`worker list/status`（Worker 管理）

---

## Step 拆分

### Step 3.1 — 兵部：DAG 执行引擎

**目标**：实现基于 DAG（有向无环图）的任务编排引擎，支持子任务依赖关系和拓扑排序执行。

**涉及文件**
```
src/tianshu/executor/
  dag.py
  executor.py（增强）
```

**依赖**：无

**验收条件**
- [ ] DAG 数据结构：节点（子任务）+ 边（依赖关系）
- [ ] 拓扑排序：自动计算可执行的子任务集
- [ ] 循环依赖检测：构建时校验，有循环则拒绝执行
- [ ] Planner 输出的 `Plan.depends_on` 可直接转换为 DAG
- [ ] DAG 执行状态追踪：每个节点的 pending/running/completed/failed 状态
- [ ] 节点失败时的策略：停止依赖该节点的所有下游节点
- [ ] 单元测试覆盖拓扑排序、循环检测、失败传播

**复杂度**：高

---

### Step 3.2 — 兵部：多 Agent 并发执行

**目标**：支持 DAG 中无依赖的子任务由不同 Agent 实例并发执行。

**涉及文件**
```
src/tianshu/executor/
  worker.py
  executor.py（增强）
```

**依赖**：Step 3.1

**验收条件**
- [ ] Worker Pool：管理多个 Agent 实例的生命周期
- [ ] DAG 调度器：当节点的所有前置依赖完成时，分配给空闲 Worker
- [ ] 子任务工具裁剪：子 Agent 不能调用元工具 [NanoBot-3]（§4.4）
- [ ] 子任务上下文裁剪：过滤不必要上下文 [DeepAgents-2]（§5.2）
- [ ] 并发执行结果聚合回 DAG 状态
- [ ] Worker 异常不影响其他 Worker
- [ ] 集成测试覆盖并发执行、结果聚合

**复杂度**：中

---

### Step 3.3 — 兵部：Lane-based 并发控制

**目标**：实现 Lane-based 双层并发控制，保证任务内串行和全局背压。

**涉及文件**
```
src/tianshu/executor/
  lanes.py
  executor.py（增强）
```

**依赖**：Step 3.2

**验收条件**
- [ ] Session Lane：每个 Edict 一条独立 lane，保证同一任务内的子任务不会跨任务串扰 [OpenClaw-4]（§5.3）
- [ ] Global Lane：全局背压控制，限制系统整体并发度
- [ ] 双层队列：Session Lane 内串行，多个 Session Lane 之间通过 Global Lane 控制并发上限
- [ ] 避免全局锁的粗粒度控制
- [ ] 防止无限并发导致资源耗尽
- [ ] 可配置：per-edict 并发度（`runtime.max_concurrency`）和全局并发度
- [ ] 单元测试覆盖串行保证、背压控制、配置生效

**复杂度**：高

---

### Step 3.4 — 增强取消、重试和恢复

**目标**：增强多 Agent 场景下的取消传播、重试策略和故障恢复能力。

**涉及文件**
```
src/tianshu/executor/
  executor.py（增强）
  worker.py（增强）
  dag.py（增强）
```

**依赖**：Step 3.1

**验收条件**
- [ ] 取消传播：取消 Edict 时，级联取消所有正在执行的子任务 Worker [NanoBot-3]（§3.6）
- [ ] 尽快停止未开始子任务，对进行中的做最佳努力终止（§3.6）
- [ ] 取消生成 `execution.cancelled` 事件并更新所有关联 Memorial（§3.6）
- [ ] 部分失败恢复：DAG 中部分节点失败时，可从失败点重试而非全部重跑
- [ ] 重试时保留已完成节点的结果
- [ ] 断点续跑：进程崩溃后，可从持久化的 DAG 状态恢复执行
- [ ] 集成测试覆盖级联取消、部分重试、断点续跑

**复杂度**：高

---

### Step 3.5 — 工部：存储层迁移到 PostgreSQL

**目标**：将 SQLite 迁移到 PostgreSQL，支持多实例一致性和高级查询。

**涉及文件**
```
src/tianshu/storage/
  postgres_repo.py
  migrations/
```

**依赖**：独立

**验收条件**
- [ ] PostgreSQL 表结构与 SQLite 兼容（Edict、Memorial、EventEnvelope、SchedulerJob）
- [ ] Repository 接口保持不变（上层代码无需修改）
- [ ] 支持多实例并发读写（事务隔离）
- [ ] 数据迁移脚本：SQLite → PostgreSQL
- [ ] 连接池管理
- [ ] 查询性能优化：索引、分区（按时间或状态）
- [ ] 单元测试覆盖 CRUD、并发、迁移

**复杂度**：中

---

### Step 3.6 — 工部：K8s 生产级部署

**目标**：在 Phase 0 Docker 基础上，支持 K8s 生产级部署和水平扩缩容。

**涉及文件**
```
Dockerfile（增强，适配 PostgreSQL）
docker-compose.yml（增强，加入 PostgreSQL）
k8s/
  deployment.yaml
  service.yaml
  configmap.yaml
```

**依赖**：Step 3.5

**验收条件**
- [ ] docker-compose.yml 增强：天枢 + PostgreSQL + 可选 Redis 一键启动
- [ ] K8s 部署配置：Deployment + Service + ConfigMap
- [ ] 健康检查端点优化：`/health`（存活）、`/ready`（就绪，含 PostgreSQL 连接检查）
- [ ] 配置通过环境变量和 ConfigMap 注入（§6.7）
- [ ] 日志输出为结构化 JSON，便于 K8s 日志采集
- [ ] 容器化部署下事件语义与本地部署一致
- [ ] 支持水平扩缩容（多实例 + PostgreSQL 共享存储）
- [ ] 集成测试：docker-compose 启动后完成端到端执行

**复杂度**：中

---

### Step 3.7 — Temporal 集成（可选）

**目标**：可选集成 Temporal，将 DAG 执行引擎映射为 Temporal Workflow，获得 Durable Workflow 能力。

**涉及文件**
```
src/tianshu/executor/
  temporal_executor.py
  temporal_activities.py
```

**依赖**：Step 3.6

**验收条件**
- [ ] DAG 节点映射为 Temporal Activity
- [ ] DAG 整体映射为 Temporal Workflow
- [ ] Temporal 提供自动重试、超时、补偿能力
- [ ] 与非 Temporal 模式的接口保持兼容（可配置切换）
- [ ] Temporal Server 连接配置化
- [ ] 断点续跑由 Temporal 原生支持（替代 Step 3.4 的自实现恢复）
- [ ] 集成测试：Temporal 模式下完成端到端执行

**复杂度**：高

---

### Step 3.8 — 兵部：DAG 作战图

**目标**：DAG 执行可视化监控，实时展示任务依赖图、节点状态和多 Agent 并发情况。

**涉及文件**
```
web/src/
  api/dag.ts
  pages/DagMonitorPage.tsx
  components/dag/
    DagCanvas.tsx                 # ReactFlow DAG 画布
    NodeStatusPanel.tsx           # 节点详情面板
    AgentWorkerList.tsx           # Worker 列表
    LaneIndicator.tsx             # Lane 使用率指示
  pages/EdictDetailPage.tsx       # 修改：多步 Edict 嵌入 DAG 视图
  components/layout/Sidebar.tsx   # 修改：加"兵部作战图"导航
  App.tsx                         # 修改：加 /dag 路由
```

**依赖**：Step 3.1（DAG 引擎）+ Step 3.2（多 Agent 并发）+ Step 3.3（Lane-based 控制）+ Step 1.11（WebSocket）

**验收条件**
- [ ] ReactFlow 画布：节点为子任务，边为依赖关系
- [ ] 节点颜色实时反映状态：灰色待定、蓝色执行中、绿色完成、红色失败、黄色等待依赖
- [ ] 点击节点弹出详情面板：子任务描述、分配 Agent、时间、输出预览、错误信息
- [ ] Worker 面板：展示所有活跃 Agent Worker 及当前任务分配
- [ ] Lane 使用率指示器：Session Lane 用量 + Global Lane 背压
- [ ] 取消传播可视化：取消时显示级联动画
- [ ] dagre 自动布局 + 支持手动拖拽
- [ ] ReactFlow 通过 `React.lazy()` 懒加载，不影响 Phase 0-2 包体积
- [ ] 非 DAG 类型 Edict（Phase 0-2）回退展示标准时间线

**复杂度**：高

---

### Step 3.9 — 御案台：CLI 分布式指令

**目标**：扩展 CLI，支持 DAG 拓扑展示和 Worker 管理。

**涉及文件**
```
src/tianshu/cli/commands/
  dag.py                          # tianshu dag show <edict_id>
  worker.py                       # tianshu worker list/status
```

**依赖**：Step 0.12（CLI 基础）+ Step 3.1（DAG 引擎）+ Step 3.2（多 Agent Worker）

**验收条件**
- [ ] `tianshu dag show <edict_id>` 调用 `GET /api/edicts/{id}/dag`
- [ ] DAG 以 ASCII 树形图渲染：节点名称、状态着色、依赖边
- [ ] 每个节点显示：子任务描述（截断）、状态、耗时
- [ ] `tianshu worker list` 展示 Worker 列表（ID、状态、当前任务、资源使用率）
- [ ] `tianshu worker status <worker_id>` 展示 Worker 详情
- [ ] 所有命令支持 `--format json` 输出

**复杂度**：中

---

### Step 3.10 — 多官员并发执行

**目标**：DAG 并行子任务使用各自官员 Persona，每个 Worker 加载独立的 Persona 上下文。

**涉及文件**
```
src/tianshu/executor/
  worker.py（增强）
  executor.py（增强）
```

**依赖**：Step 3.2 + Step 1.16

**验收条件**
- [ ] Worker 启动时根据子任务的 `assigned_official` 加载对应 Persona
- [ ] 每个 Worker 的系统提示包含其官员的 SOUL.md + ROLE.md + MEMORY.md
- [ ] 不同 Worker 的 Persona 上下文互相隔离
- [ ] 无 `assigned_official` 的子任务使用默认 bingbu Persona
- [ ] Persona 加载失败时回退到通用提示，不中断执行
- [ ] 集成测试覆盖多 Persona 并发执行

**复杂度**：中

---

### Step 3.11 — 会商协议（多官员协商）

**目标**：实现内阁召集多官员并行提供视角、综合决策的会商机制。

**涉及文件**
```
src/tianshu/persona/
  consultation.py
src/tianshu/planner/planner.py（增强）
```

**依赖**：Step 3.10

**验收条件**
- [ ] `ConsultationProtocol` 定义会商流程：发起 → 并行分析 → 综合决策
- [ ] 只有内阁（neige）可以发起会商
- [ ] 参与官员并行提供各自视角：兵部（可行性）、都察院（风险）、户部（成本）、文渊阁（历史经验）
- [ ] 内阁综合各方意见输出最终决策
- [ ] 会商记录写入朝堂共享记忆
- [ ] Planner 在复杂任务规划前可选触发会商
- [ ] 会商超时机制：单个官员超时不阻塞其他官员
- [ ] 集成测试覆盖会商流程、超时、记录

**复杂度**：高

---

### Step 3.12 — 官员绩效评估

**目标**：追踪各官员成功率、效率、成本等指标，为官员选择提供数据支撑。

**涉及文件**
```
src/tianshu/persona/
  evaluation.py
  selector.py（增强）
```

**依赖**：Step 3.10 + Step 2.10

**验收条件**
- [ ] `OfficialEvaluator` 追踪各官员指标：任务成功率、平均耗时、Token 消耗、审计通过率
- [ ] 指标按时间窗口（日/周/月）聚合
- [ ] `OfficialSelector` 增强：结合绩效指标优化官员分配
- [ ] 绩效数据写入 SQLite，支持历史查询
- [ ] 低绩效官员可触发告警（日志级别）
- [ ] 单元测试覆盖指标计算、选择器增强

**复杂度**：中

---

## Step 依赖关系图

```
3.1 DAG 引擎 ──> 3.2 多 Agent 并发 ──> 3.3 Lane-based 并发控制
     │                │
     │                └──────────────────────────────────┐
     └──> 3.4 增强取消/重试/恢复                         │
                                                         │
3.5 PostgreSQL 迁移 ──> 3.6 K8s 生产级部署 ──> 3.7 Temporal 集成（可选）

3.1 DAG 引擎 ──┐
3.2 多 Agent ──┼──> 3.8 DAG 作战图
3.3 Lane ──────┤
1.11 WebSocket ┘

0.12 CLI 基础 ──┐
3.1 DAG 引擎 ───┼──> 3.9 CLI 分布式指令
3.2 多 Agent ───┘
```

**Persona 相关依赖**：

```
3.2 多 Agent ──┐
               ├──> 3.10 多官员并发执行 ──> 3.11 会商协议
1.16 Loader ───┘                    │
                                    └──> 3.12 官员绩效评估（+ 依赖 2.10）
```

**可并行组**：
- 组 A：Step 3.1 → 3.2 → 3.3（执行引擎演进线）
- 组 B：Step 3.5 → 3.6 → 3.7（基础设施演进线）
- 组 C：Step 3.4（增强恢复）— 只依赖 3.1
- 组 D：Step 3.8（DAG 作战图）— 依赖 3.1 + 3.2 + 3.3 + 1.11
- 组 E：Step 3.9（CLI 分布式指令）— 依赖 0.12 + 3.1 + 3.2
- 组 F：Step 3.10（多官员并发）— 依赖 3.2 + 1.16
- 组 G：Step 3.11（会商协议）— 依赖 3.10
- 组 H：Step 3.12（官员绩效评估）— 依赖 3.10 + 2.10

组 A 和组 B 完全独立，可并行推进。Step 3.8 需等待组 A 完成，Step 3.9 可在 3.2 完成后开始。Step 3.10-3.12（Persona 相关）依赖 3.2 完成，可在执行引擎演进线后期并行推进。

## 测试策略

| 层次 | 覆盖范围 | 工具 |
|------|---------|------|
| 单元测试 | DAG 引擎、Lane 控制、Worker Pool | pytest |
| 集成测试 | 多 Agent 并发执行、级联取消 | pytest + asyncio |
| 容器化测试 | Docker Compose 端到端（含 PostgreSQL） | docker-compose + pytest |
| 负载测试 | 并发 Edict 提交、Worker 扩缩容 | locust / k6 |

## 风险

| 风险 | 缓解措施 |
|------|---------|
| DAG 执行引擎复杂度高 | 先支持简单线性 DAG，再逐步支持复杂拓扑 |
| 多 Agent 并发的资源争用 | Lane-based 控制 + 全局背压 + 可配置并发上限 |
| PostgreSQL 迁移数据一致性 | 迁移脚本做全量校验 + 回滚方案 |
| Temporal 学习曲线 | 标记为可选，非 Temporal 模式仍完整可用 |
