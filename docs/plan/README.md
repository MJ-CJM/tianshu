# 天枢（Tianshu）分阶段实施计划

> **历史路线图：** 本目录把早期架构拆成 Phase 0–3，不是当前完成度或发布承诺。
> 当前实现和延期边界以 [当前实现与支持边界](../CURRENT-STATE.md) 与
> [能力事实矩阵](../launch/capability-matrix.md) 为准。特别是 PostgreSQL、K8s、
> Temporal 和多副本仍未进入当前支持范围。

本目录将早期 [`../design/architecture.md`](../design/architecture.md) 架构方案转化为
分阶段实施计划。具体特性当时如何拆解，见 [`../superpowers/INDEX.md`](../superpowers/INDEX.md)。

---

## Phase 概览

| Phase | 名称 | 目标 | Step 数 | 运行方式 |
|-------|------|------|---------|---------|
| [Phase 0](./phase-0-minimal-loop.md) | 跑通最小闭环 | 单 Agent + ReAct + 基础工具 + Skills + SQLite + Docker + **Web Dashboard 基础 CRUD** + **CLI 基础指令** | 12 | Web 服务进程（FastAPI + Uvicorn） |
| [Phase 1](./phase-1-governance.md) | 引入治理与异步调度 | EventBus + Scheduler + Planner + Auditor + Notifier + 人工复核 + **WebSocket 实时 + 批红台 + 事件时间线** + **CLI 治理指令** + **4 官员 Persona 注入** | 18 | Web 服务 + 事件驱动调度 |
| [Phase 2](./phase-2-platform.md) | 引入平台化能力 | Memory + CostManager + 多 Provider + 多通道通知 + PluginApi + **成本报表 + Provider/插件管理** + **CLI 平台指令** + **完整 R/R/R 记忆循环** | 11 | 平台化 Web 服务 |
| [Phase 3](./phase-3-multi-agent.md) | 多 Agent 与分布式扩展 | DAG + 多 Agent 并发 + PostgreSQL + K8s + **DAG 作战图** + **CLI 分布式指令** + **多官员并发 + 会商协议** | 12 | 容器集群 / K8s / Temporal |

## 阅读指南

1. **先读当前状态**：当前能力与边界以 `docs/CURRENT-STATE.md` 和能力事实矩阵为准
2. **Phase 是历史拆分**：完成某个 checklist 不能替代当前源码和回归证据
3. **Step 顺序即建议执行顺序**：无依赖的 Step 可并行开发
4. **验收条件即完成标准**：每个 Step 的 checklist 全部通过才算完成

## 制度映射总表

天枢以明朝"六部"体系组织治理职责（详见 `architecture.md` §1.3 命名原则、§1.4 六部治理职责映射）。下表展示部院与代码模块的完整映射，以及各部院首次引入的 Phase：

| 部院（外层） | 模块（内层） | 官员（Phase 1+） | 职责概述 | 首次引入 |
|------------|-----------|----------------|---------|---------|
| 御案台 | `Gateway` | — | 任务接入、输入校验、幂等检查 | Phase 0 |
| — | `Edict`（诏令） | — | 任务输入统一模型 | Phase 0 |
| — | `Memorial`（奏折） | — | 任务结果统一模型 | Phase 0 |
| 兵部 | `Executor` / `Agent` | 兵部尚书 `bingbu` | ReAct 执行引擎 | Phase 0 |
| 工部 | `Storage` / `ToolRegistry` / `ConfigManager` | — | 基础设施（存储、工具链、配置） | Phase 0 |
| 礼部 | `Skills` / System Prompt | — | Prompt 模板、能力声明 | Phase 0 |
| — | `EventBus` | — | 领域事件总线 | Phase 1 |
| — | `Scheduler` | — | 调度器 | Phase 1 |
| 内阁 | `Planner` | 内阁首辅 `neige` | 任务拆解与规划 | Phase 1 |
| 都察院 | `Auditor` | 都察院左都御史 `ducha` | 审计与风控 | Phase 1 |
| 通政司 | `Notifier` | 通政使 `tongzheng` | 渲染与通知 | Phase 1 |
| — | `Decree`（批红） | — | 用户反馈与复核 | Phase 1 |
| — | `AgentPersona` | — | 官员系统（Persona 注入 + 记忆） | Phase 1 |
| 文渊阁 | `Memory` | 文渊阁大学士 `wenyuan` | 记忆与检索 | Phase 2 |
| 户部 | `CostManager` | 户部尚书 `hubu` | Token/成本治理 | Phase 2 |
| 吏部 | `PluginApi` | — | 统一注册（Tool/Hook/Channel/Provider/Skill） | Phase 2 |
| 御案台（增强） | `Web Dashboard` | — | 用户 Web 交互界面 | Phase 0 |
| 御案台（增强） | `CLI` | — | 容器内命令行交互工具 | Phase 0 |

## 参考项目索引

架构设计附录 A/B/C/D 包含完整的参考采纳矩阵。下表汇总各参考点的采纳阶段：

| 编号 | 项目 | 设计点 | 采纳阶段 |
|------|------|--------|---------|
| NanoBot-1 | NanoBot | ReAct 循环状态机 | Phase 0 |
| NanoBot-3 | NanoBot | 工具裁剪与级联取消 | Phase 0（标记）/ Phase 3（实施） |
| NanoBot-5 | NanoBot | 两阶段结构化审计 | Phase 1 |
| DeepAgents-1 | DeepAgents | ReAct 中间件 | Phase 0 |
| DeepAgents-2/3 | DeepAgents | 结构化任务拆解 + 上下文裁剪 | Phase 1（Planner）/ Phase 3（多 Agent） |
| CoPaw | CoPaw | schedule+dispatch+runtime 任务模型 | Phase 0（Edict 模型设计参考） |
| CoPaw-7 | CoPaw | 消息渲染管线 | Phase 1 |
| CoPaw-8 | CoPaw | 防抖与通道治理 | Phase 1 |
| CoPaw-9 | CoPaw | Provider 路由 | Phase 2 |
| PicoClaw-1 | PicoClaw | 工作区隔离 | Phase 0 |
| PicoClaw-2 | PicoClaw | 通道限速与重试 | Phase 2 |
| ZeroClaw-1 | ZeroClaw | 安全策略违规不重试 | Phase 0 |
| ZeroClaw-7 | ZeroClaw | Provider 能力声明 | Phase 2 |
| OpenClaw-1 | OpenClaw | Compaction（分片压缩 + 标识符保留） | Phase 2 |
| OpenClaw-2 | OpenClaw | 多层 Tool Policy Pipeline | Phase 0（标记）/ Phase 1（实施） |
| OpenClaw-3 | OpenClaw | 执行中实时审批（allow-once/allow-always） | Phase 1 |
| OpenClaw-4 | OpenClaw | Lane-based 并发控制 | Phase 3 |
| OpenClaw-5 | OpenClaw | 生命周期钩子体系 | Phase 1 |
| OpenClaw-6 | OpenClaw | SKILL.md 格式兼容 | Phase 0 |
| OpenClaw-7 | OpenClaw | 统一 PluginApi 注册 | Phase 2 |
| ZeroClaw | ZeroClaw | 统一观察事件与指标语义 | Phase 0（设计）/ Phase 1（实施） |
| OpenClaw | OpenClaw | SOUL.md / AGENTS.md Bootstrap 文件体系 | Phase 1 |
| OpenClaw | OpenClaw | Memory R/R/R 循环 | Phase 2 |
| OpenClaw | OpenClaw | Markdown-as-SOT + SQLite FTS5 记忆检索 | Phase 2 |
| NanoBot | NanoBot | MEMORY.md + HISTORY.md 双层记忆 | Phase 1 |
| NanoBot | NanoBot | context.py 多层注入顺序 | Phase 1 |
| DeepAgents | DeepAgents | SubAgent context isolation 上下文隔离 | Phase 1 |
| ZeroClaw | ZeroClaw | Memory trait 记忆协议抽象 | Phase 2 |

## 约定

### Step 编号

`<Phase>.<Step>` — 如 `0.3` 表示 Phase 0 的第 3 个 Step。

### 复杂度标记

| 标记 | 含义 | 参考工时 |
|------|------|---------|
| 低 | 模式明确，代码量少 | 0.5-1 天 |
| 中 | 需要设计决策，涉及多文件 | 1-3 天 |
| 高 | 核心逻辑，需要集成测试 | 3-5 天 |

### 引用格式

- `§N.N` 引用 `docs/design/architecture.md` 章节号
- `[OpenClaw-N]` 引用架构设计附录中的参考项目编号

### 依赖关系图

每个 Phase 文件包含 ASCII 依赖关系图，箭头方向为"依赖于"：

```
A --> B    表示 B 依赖 A（先做 A，再做 B）
A --x B    表示 A 和 B 无依赖，可并行
```

## 源文件

| 文件 | 说明 |
|------|------|
| [`../design/architecture.md`](../design/architecture.md) | 架构设计（设计真相来源） |
| [`../design/project-analysis.md`](../design/project-analysis.md) | 项目背景与需求分析 |
| [`../reference/reference-projects.md`](../reference/reference-projects.md) | 参考项目分析 |
