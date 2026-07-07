# 天枢架构总览

> 本目录按“总分”结构梳理当前项目的架构和核心设计：本文件是总览，其余按功能子系统拆分到同名目录（与 [`../impl/`](../impl/) 的实现文档一一对应）。内容以当前代码为准，主要参考 `src/tianshu/app.py` 的装配链路、`models/` 的领域契约、`executor/` 的执行路径和 `gateway/` 的接口面。

## 1. 一句话定位

天枢是一个异步 AI 执行平台：用户通过 API、Web、飞书或 CLI 下达 `Edict`，系统把目标转成可调度、可审计、可审批、可复盘的执行链路，最终沉淀为 `Memorial`、事件、成本记录、记忆和监督报告。

核心闭环是：

```text
下旨 Edict
  -> 排期 Scheduler
  -> 规划 Planner
  -> 执行 Executor / Agent / DAG / Outer Loop
  -> 审计 Auditor / Supervision
  -> 通知 Notifier / Gateway
  -> 记忆 Memory / Profile / Skill learning
```

## 2. 当前代码的顶层边界

| 层 | 主要模块 | 责任 |
|---|---|---|
| 入口与接口 | `app.py`, `gateway/`, `web/`, `cli/` | FastAPI 生命周期装配、HTTP/WS、飞书、静态 Web、命令行 |
| 领域契约 | `models/` | `Edict`, `Memorial`, `Decree`, `Plan`, `AcceptanceCriteria`, 事件、成本、DAG 模型 |
| 流程编排 | `scheduler/`, `planner/`, `executor/`, `dag/` | 事件驱动主链路、LLM 规划、单任务 Agent、DAG 多任务、长任务 outer loop |
| Agent 核心 | `executor/agent.py`, `llm.py`, `providers/`, `config_manager.py` | ReAct 循环、工具调用、上下文压缩、模型配置与 provider fallback |
| 治理与安全 | `tools/`, `executor/policy_hook.py`, `executor/approvals.py`, `auditor/` | 工具注册、tier、PolicyEngine、审批、审计、winding_down 副作用拦截 |
| 成长系统 | `persona/`, `memory/`, `skills/`, `consultation/` | 六部 persona、PromptBuilder、多层记忆、Skills 渐进加载、会诊、画像合成 |
| 可观测与持久化 | `storage/`, `bus/event_bus.py`, `cost/`, `notifier/` | SQLite 真相源、事件总线、成本账本、通知和 WebSocket |
| 扩展能力 | `plugins/`, `tools/hongluisi/`, `gateway/feishu/` | 插件注册、外部网络工具、飞书助手模式 |

## 3. 启动时的依赖装配

`src/tianshu/app.py` 的 `lifespan()` 是当前架构的权威装配图。关键顺序如下：

1. `Storage` 初始化 SQLite、WAL、迁移、FTS。
2. `EventBus` 接入持久化事件。
3. `HookRegistry` 建立 Agent 生命周期钩点。
4. `ToolRegistry` 注册内建工具、记忆工具、skill 工具、敕令工具，并加载工具启停状态。
5. `SkillsLoader`、`PersonaLoader`、`DrawerStore`、`MemoryConfig`、`PromptBuilder` 启动成长上下文。
6. `ConfigManager`、`ProviderManager`、`Agent`、`WorkerPool`、`LaneManager` 建立执行基础设施。
7. `Auditor`、`Notifier`、`ApprovalManager`、`PolicyEngine`、`PolicyHook` 注入治理能力。
8. `MemoryManager`、`CostManager`、`ConsultationSession`、`OrchestratorContext` 接入横切能力。
9. `Planner`、`Scheduler`、`PluginApi`、`ProfileSynthesizer`、`DigestGenerator`、`SkillsWatcher` 启动外围能力。
10. 注册 EventBus 订阅链，启动 scheduler。

这意味着项目不是“路由调用服务”的同步架构，而是“API 写入领域对象 + 事件驱动后台链路”的异步架构。

## 4. 主链路架构图

```text
Gateway / Feishu / CLI / Web
        |
        v
   Storage.save_edict + save_memorial
        |
        v
 EventBus.fire("edict.submitted")
        |
        v
 Scheduler
   immediate -> edict.scheduled
   once/cron -> scheduler_jobs -> edict.scheduled
        |
        v
 Planner
   direct assigned_persona_id -> passthrough plan
   cabinet LLM planning -> Plan(tasks)
   plan_review -> plan.pending_review
        |
        v
 Executor
   acceptance != None -> Orchestrator outer loop
   multi task plan -> DAGScheduler + WorkerPool
   single task -> Agent ReAct loop
        |
        v
 Agent
   PromptBuilder -> LLM -> tool calls -> hooks/policy/approval -> result
        |
        v
 execution.completed / execution.failed
        |
        +--> Auditor
        +--> CostManager
        +--> MemoryManager
        +--> Notifier / WebSocket / channels
```

## 5. 核心设计判断

### 5.1 事件是模块间主协议

模块之间主要通过 `EventBus` 解耦。`emit()` 顺序等待 handler，适合主链路；`fire()` 后台执行，适合 API 快速返回。所有带 `edict_id` 的事件会落到 `events` 表，形成可追踪时间线。

### 5.2 SQLite 是控制面真相源

`Storage` 管理 `edicts`、`memorials`、`events`、`decrees`、DAG、session rules、provider、成本、persona、plugins、飞书状态、outer loop 等控制面数据。Memory Palace 的 drawer 另有独立 SQLite；Markdown 记忆是人格记忆源。

### 5.3 执行引擎分三档

| 路径 | 触发条件 | 设计意图 |
|---|---|---|
| 单 Agent | 单任务或 passthrough plan | 最短闭环，默认路径 |
| DAG | `Plan.tasks > 1` 且有 `DAGScheduler` | 多任务依赖、并发、节点级 memorial |
| Outer Loop | `Edict.acceptance != None` | 长任务自检、critic、升级、人工 L3、监督报告 |

### 5.4 治理优先于隐式智能

工具调用经过 tier、PolicyRule、session rule、审批、winding_down 副作用拦截。长任务预算会触发 soft landing / hard limit；外部网络工具受 profile、host whitelist、SSRF、防凭证泄露和 rate limit 约束。

### 5.5 Persona、Memory、Skills 是成长飞轮

PromptBuilder 把 court、persona、role、memory、drawer L1、近期日志、部门记忆、同僚画像、skills、任务上下文组装成系统提示。Agent 执行后 MemoryManager、SkillReviewHandler、ProfileTrigger 继续写回记忆、skill 指标和画像合成信号。

## 6. 分篇索引

顶层总览：

| 文档 | 内容 |
|---|---|
| [architecture.md](architecture.md) | 顶层架构、六部隐喻、设计原则、阶段规划 |
| [project-analysis.md](project-analysis.md) | 项目背景、痛点与需求分析 |
| [domain-model.md](domain-model.md) | 领域对象、状态机、SQLite 表、事件契约 |
| [runtime-flow.md](runtime-flow.md) | 启动装配、事件链、Scheduler/Planner/Executor 主流程 |

按功能子系统（每个目录含设计意图与当前设计，与 [`../impl/<同名>`](../impl/) 实现文档一一对应）：

| 子系统 | 内容 |
|---|---|
| [agent/](agent/) | ReAct 循环、三层压缩、DAG、长任务 outer loop、Hook |
| [persona/](persona/) | 六部官员、PromptBuilder 8 层注入、画像合成 |
| [memory/](memory/) | 记忆宫殿、Drawer/Markdown/FTS 后端、全文回忆 |
| [skills/](skills/) | 渐进加载、技能学习与修撰、安全 Guard |
| [tools/](tools/) | 工具注册与 tier、Policy 管线、鸿胪寺网络、MCP |
| [consultation/](consultation/) | 多人格并行会诊、LLM 汇聚成 L2 升级智囊 |
| [auditor/](auditor/) | 两层审计（快规则 + LLM 复审）、三态裁决、人审分档 |
| [secrets/](secrets/) | 藏兵阁凭证加密托管、按 host 静默注入、LLM 不可见 |
| [llm/](llm/) | LLMClient/Provider/fallback/缓存、成本与预算 |
| [storage/](storage/) | SQLite 真相源、EventBus 事件链 |
| [bus/](bus/) | EventBus 进程内事件总线、emit/fire/priority 解耦协议 |
| [scheduling/](scheduling/) | Scheduler 三模式、Planner 规划 |
| [universe/](universe/) | 位面演化、代码变体晋升 |
| [plugins/](plugins/) | manifest 声明、PluginApi 把扩展注册进内核各注册表 |
| [interfaces/](interfaces/) | HTTP/WS Gateway、飞书/Telegram 渠道、Web、CLI |
| [growth/](growth/) | 自改进四环统一视图（design-only 元文档，无对应 impl 子系统） |

## 7. 读代码时的优先入口

| 问题 | 优先看 |
|---|---|
| 系统怎么启动 | `src/tianshu/app.py` |
| 下旨后怎么流转 | `gateway/edicts_api.py`, `bus/event_bus.py`, `scheduler/scheduler.py`, `planner/planner.py`, `executor/executor.py` |
| Agent 怎么调用模型和工具 | `executor/agent.py`, `llm.py`, `tools/registry.py` |
| 长任务如何验收 | `models/acceptance.py`, `executor/orchestrator/loop.py` |
| 权限审批如何工作 | `tools/policy.py`, `executor/policy_hook.py`, `executor/approvals.py`, `tools/policy_rules/` |
| 人格和记忆怎么进 prompt | `persona/prompt_builder.py`, `memory/manager.py`, `persona/loader.py` |
| 数据落在哪里 | `storage/`, `memory/drawer_store.py`, `memory/markdown_backend.py` |
