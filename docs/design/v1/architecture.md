# 天枢（Tianshu）全局架构设计

> **天枢：一座会与你共同成长的宫殿。内有你的分身，外有辅佐你的六部。**

**What** 一个可常驻、会成长的个人 agent 系统
**Why** 把 agent 从"一次性工具"升级为"长期共生体" —— 不只完成当下任务，更沉淀对你的理解
**How** "宫殿"隐喻组织记忆与角色：emperor wing（你的分身）+ 六部 wing（专业官员）+ court wing（共享记忆），各 wing 通过 Memory Palace + Skills 飞轮持续演进

> 异步 AI 执行平台。白天下旨，夜间办差，早上递折子。
>
> 本文是 **WHY + WHAT**（架构意图与稳定契约）。**HOW + WHERE**（当前代码真相）见 `docs/impl/`。

---

## 一、系统目标与设计原则

### 1.1 产品定位

天枢不是聊天 UI，也不是固定流程编排器，而是一个面向复杂任务的异步 AI 执行中枢：

- 用户以自然语言下发任务
- 系统将任务标准化为可治理的 `Edict`
- 执行链路支持规划、执行、审计、通知、复核
- 结果沉淀为可追踪、可复盘、可重跑的 `Memorial`

一句话闭环：

```
下旨 → 排期 → 办差 → 稽核 → 递折 → 批红
```

### 1.2 设计原则

1. **先闭环，后扩展** — 从单 Agent + 工具调用起步，按需拆模块，抵制过早抽象
2. **先契约，后模块** — 先定义数据模型、状态机、事件、权限边界，再设计"部院"
3. **默认可审计** — 每次提交 / 执行 / 审计 / 通知 / 批红都有结构化记录
4. **显式治理优先于隐式智能** — 预算、审批、权限、重试、人工复核不交给 LLM 猜测
5. **外层古风，内层现代** — 命名服务叙事，不干扰工程实现

### 1.3 命名原则

| 外层（用户可见） | 内层（代码实现） |
|---|---|
| 诏令 | `Edict` |
| 奏折 | `Memorial` |
| 批红 | `Decree` |
| 御案台 | `Gateway` |
| 内阁 | `Planner` |
| 兵部 | `Executor` |
| 都察院 | `Auditor` |
| 通政司 | `Notifier` |
| 文渊阁 | `Memory` |
| 户部 | `CostManager` |

### 1.4 六部治理矩阵

六部是**职责分类框架**，不是模块切分方案：每项治理职责挂靠到最自然的 owner 上，而不是为每个部硬造一个包。

| 六部 | 治理职责 | 落点 |
|---|---|---|
| **吏部** | Agent/Skill 注册、权限身份、能力声明、配额 | `ToolRegistry` + `SkillsLoader` + `PluginApi` + `PolicyEngine` |
| **户部** | Token 成本、API 配额、预算熔断、资源账本 | `CostManager`（独立模块，`cost/`） |
| **礼部** | Prompt 模板、输出规范、通道内容适配 | `PromptBuilder`（8 层）+ `Notifier.renderer` + Skills body |
| **兵部** | 执行引擎、Agent Loop、工具调用、并发调度 | `Executor` + `Agent` + `WorkerPool` + `DAGScheduler` |
| **刑部** | 失败处理、异常升级、风险封禁、安全策略执行 | `Auditor.rules` + `Guard` + `PolicyHook` + `ExitReason` |
| **工部** | 工具链、工作区、环境隔离、存储、基础设施 | `ToolRegistry` + `Storage` + `ConfigManager` + `ProviderManager` |

### 1.5 官员映射（Persona）

每个部院配置专属人格（Agent Persona），以人格化方式承接治理职责。详见 `agent-persona.md` 与 `docs/impl/persona.md`。

| 部院 | 官员 id | 隐喻 |
|---|---|---|
| 内阁 | `neige` | 战略规划，Planner 默认人格 |
| 兵部 | `bingbu` | `DEFAULT_EXECUTOR_ID`，默认执行者 |
| 都察院 | `ducha` | 审计、Code Review |
| 通政司 | `tongzheng` | 渲染、通知、Consultation 主持 |
| 文渊阁 | `wenyuan` | 文档、知识、memory 任务 |
| 户部 | `hubu` | 成本审查、budget/token |
| 朝廷 | `court` | 共享上下文（非独立 persona） |

## 二、顶层模块图

```
┌────────────────────────────────────────────────────────────────┐
│                          Gateway (FastAPI)                      │
│   POST /edicts · /approvals · /memory · /consultation · WS /ws  │
└──────────────────────┬─────────────────────────────────────────┘
                       │ Edict
                       ▼
              ┌─────────────────┐       ┌────────────────────┐
              │   Scheduler     │──────▶│   EventBus         │
              │ immediate/cron  │       │ (priority + async) │
              └─────────────────┘       └──────────┬─────────┘
                                                   │
                       ┌──────────────┬────────────┼────────────┬──────────────┐
                       ▼              ▼            ▼            ▼              ▼
               ┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐
               │   Planner    │ │ Executor │ │ Auditor  │ │ Notifier │ │ CostManager│
               │ LLM / direct │ │          │ │ rules    │ │ 飞书/邮件 │ │ ledger/    │
               └──────┬───────┘ └────┬─────┘ └──────────┘ │ ws/dingd │ │ budget     │
                      │              │                    └──────────┘ └────────────┘
                      │              ▼
                      │       ┌──────────────┐    ┌──────────────┐
                      │       │ DAGScheduler │───▶│  WorkerPool  │
                      └──────▶│ 拓扑调度      │    │  + LaneMgr   │
                              └──────────────┘    └──────┬───────┘
                                                         │
                                                         ▼
                                                 ┌──────────────┐
                                                 │    Agent     │
                                                 │  ReAct loop  │
                                                 └──┬────┬──────┘
                                                    │    │
                   ┌────────────────────────────────┴────┴────────────────────┐
                   ▼                                                          ▼
           ┌──────────────┐                                          ┌─────────────────┐
           │   LLMClient  │                                          │   ToolRegistry  │
           │ + providers  │                                          │  policy + guard │
           └──────────────┘                                          └─────────────────┘

           ┌───────────────────────────────────────────────────────────┐
           │               横切能力（通过 Hook 系统注入）                   │
           ├───────────────────────────────────────────────────────────┤
           │  PromptBuilder(8层) · Memory Palace(L0-L3) · Skills(渐进)    │
           │  ApprovalManager · PolicyHook · StreamCallback · Compaction │
           └───────────────────────────────────────────────────────────┘

                                   ┌──────────┐
                                   │ Storage  │  SQLite（18 表 + FTS5）
                                   │  WAL     │  + ~/.tianshu/memory/drawers.sqlite3
                                   └──────────┘
```

## 三、核心流程

### 3.1 主链路

```
Consultation（可选，多人格会诊，产出结构化目标）
  ↓
POST /edicts  →  EventBus.emit(edict.submitted)
  ↓
Scheduler.handle_submitted
  ├→ immediate: EventBus.emit(edict.scheduled)
  └→ cron/at:   写 scheduler_jobs，到期触发
  ↓
Planner.handle_scheduled
  ├→ 直接指派：passthrough plan（assigned_persona_id 非空）
  └→ 内阁决策：LLM + planner persona 产出 JSON plan
  ↓
EventBus.emit(plan.completed / plan.pending_review)
  ↓
Executor.handle_plan_completed
  ├→ 单任务：Worker.execute → Agent loop
  └→ 多任务：DAGScheduler 拓扑排序 → WorkerPool.submit(子 memorial)
  ↓
Agent.execute (ReAct 循环)
  [BEFORE_AGENT_START] MemoryManager L2 recall 注入
  while not done:
    [BEFORE_ITERATION] CostManager budget check
    LLMClient.chat(messages, tools)
    [LLM_OUTPUT] CostManager 记账
    for tool_call:
      [BEFORE_TOOL_CALL] PolicyHook(p=5) + ApprovalManager(p=10)
      tool.execute → [AFTER_TOOL_CALL]
    state = state.next_turn(messages)   # LoopState 不可变更新
  [AGENT_END] MemoryManager 写记忆 + SkillReviewHandler 学习
  ↓
EventBus.emit(execution.completed)
  ↓
Auditor → audit.completed → MemoryManager 写 ducha insight
Notifier → 渲染 + 多通道推送 + WS 实时流
```

### 3.2 审批与批红

任何 tool call 命中 `PolicyEngine` 的审批规则（例如 T3 写操作）：
1. `PolicyHook` → `ApprovalManager.request_approval` → memorial 挂起
2. 用户 `POST /approvals/decide` → `Decree` 持久化 + `EventBus.emit(decree.approved/rejected)`
3. `ApprovalManager` 唤醒等待的 coroutine → Agent 继续 / 放弃

### 3.3 反馈回路（记忆沉淀）

execution.completed → MemoryManager.on_agent_end
  - Markdown daily log 追加
  - DrawerStore chunk 存入 Memory Palace（`retain_drawers`）
  - 下次同人格任务 → L1 关键事实 + L2 recall 自动注入 prompt

## 四、横切能力

| 能力 | 位置 | 介入点 | 详见 |
|---|---|---|---|
| **PromptBuilder 8 层** | `persona/prompt_builder.py` | Agent 启动构 system prompt | `impl/persona.md` §5 |
| **Memory Palace L0–L3** | `memory/` | BEFORE_AGENT_START hook / prompt Layer 5.1 | `design/memory-palace.md`, `impl/memory.md` |
| **Skills 渐进加载** | `skills/loader.py` | prompt Layer 7（索引 + always-on） | `impl/skills.md` |
| **Guard 安全扫描** | `skills/guard.py` | `skill_install` / `skill_propose` | `impl/skills.md` §3 |
| **Hook 系统（10 钩点）** | `executor/hooks.py` | agent 生命周期 | `impl/executor.md` §4 |
| **PolicyHook + Approval** | `executor/policy_hook.py`, `approvals.py` | BEFORE_TOOL_CALL | `impl/executor.md` §5 |
| **3 层 Compaction** | `executor/compaction/` | 上下文溢出 / 阈值 / 每轮末尾 | `impl/executor.md` §3 |
| **CostManager** | `cost/` | BEFORE_ITERATION / LLM_OUTPUT | `impl/llm-and-cost.md` §4 |
| **Streaming** | `executor/streaming.py` | LLM chunk → WebSocket | `impl/executor.md` §6 |
| **Anthropic Prompt Cache** | `llm.py` `_apply_prompt_caching` | chat / chat_stream | `impl/llm-and-cost.md` §1 |

## 五、演进简史

### feat_phase3：六部 persona + bug 修复

- 引入 6 个部门 persona（bingbu/ducha/hubu/neige/tongzheng/wenyuan）+ court 共享
- PromptBuilder 从单通用 prompt 升级到多层注入
- 修复 Memory / DAG / Planner 的若干 bug（persona 上下文漏传、DAG 级联失败、Planner JSON 解析）

### feat_phase4：Claude Code 借鉴

参考 `docs/superpowers/plans/2026-04-02-phase1-agent-loop-redesign.md`：
- **ExitReason 枚举**（9 种退出原因，替代 bool success）
- **LoopState frozen dataclass** 每轮返回新对象，消除隐式状态
- **3 层 Compaction**：`reactive`（溢出时救急）+ `micro`（每轮末尾预防）+ `auto`（阈值触发 LLM 摘要）
- **Skills 渐进加载**：`load_index`（索引，LLM 按需用 `skill_view`）+ `load_always`（常驻）
- **Anthropic prompt_caching**：system + 最后 3 非 system 消息插 `cache_control: ephemeral` 断点，~75% 输入 token 节省
- **Streaming**：`StreamCallback` protocol + `WebSocketStreamCallback` 桥到前端 OpsMonitorPage

### feat_phase4：Hermes agent 借鉴

参考 `docs/superpowers/plans/2026-04-09-hermes-inspired-enhancements.md`：
- **Guard 安全扫描**：13 类威胁 × 50+ regex + 无形 Unicode 检测 + TrustLevel 策略矩阵
- **FuzzyMatch 8 策略链**：exact → line_trimmed → whitespace_normalized → indentation_flexible → escape_normalized → trimmed_boundary → unicode_normalized → block_anchor
- **Skills 3 层缓存**：L1 LRU（get_skill 结果）+ L2 stat 快照 + L3 磁盘扫描
- **ToolResult 截断**：超 `ToolDefinition.max_result_chars` 自动 truncate

### feat_phase5：Memory Palace + Persona 运行时分离

- **Memory Palace Phase 1**：Drawer / DrawerStore / MemoryStack L0–L3 / FTS5 BM25 + MemoryConfig 消融开关（详见 `memory-palace.md`）
- **Persona runtime 分离**：`personas/{id}/` 为 git 模板，`~/.tianshu/personas/{id}/` 为运行时副本；UI 修改只落运行时，模板永不动（详见 `agent-persona.md` §运行时覆盖）
- `/memory/search` + `/memory/l1` API
- PromptBuilder Layer 5.1 接入 L1 关键事实

## 宫殿共生成长（核心叙事）

两条成长轴并行：

- **emperor 轴（你的分身）** 跨会话持续沉淀的个人画像 —— 由 `~/.tianshu/memory/emperor/` 的 Drawer + 将来的 UserProfile 合成（Phase 下一期）负担
- **六部官员轴** 每个官员形成自己的 `PROFILE.md` 成长档案（擅长 / 近期任务 / 健康度 / 退化迹象），由 `ProfileSynthesizer` 周期合成

两轴共享 `court` wing 作为跨人格共识层。详见：

- `docs/design/memory-palace.md` §7 Court 共享 + §7.5 Emperor 分身
- `docs/design/agent-persona.md` §8.5–§8.6
- `docs/impl/persona.md` `ProfileSynthesizer`

## 六、扩展点与演进路线

**当前已落地**：
- Tool policy pipeline（`docs/superpowers/plans/2026-04-14-tool-policy-pipeline.md`）
- Agent loop 重设计（feat_phase4）
- Hermes 安全强化（feat_phase4）
- Memory Palace Phase 1（feat_phase5）

**规划中**：
- Memory Palace Phase 2+：Closet / Tunnel / 向量后端
- Consultation session：多人格并行会诊模式（当前仅单人格）
- 插件 marketplace：基于 `PluginApi` 的第三方能力分发
- 跨实例 federation：多进程 / 多机部署时的记忆 + 事件同步

## 七、文档索引

### design/（WHY + WHAT）
- `architecture.md` — 本文，全局架构
- `agent-persona.md` — 人格五维模型 + 运行时分离
- `memory-palace.md` — Memory Palace 隐喻与 L0–L3 语义
- `project-analysis.md` — 早期分析（历史参考）
- `reference-projects.md` — Claude Code / Hermes / NanoBot / DeepAgents 等借鉴源

### impl/（HOW + WHERE，按模块索引当前代码）
- `overview.md` — 启动序列、18 张表、模块树、前端↔后端路由
- `executor.md` — 17 文件：Agent / Hook / Policy / DAG / Compaction / Worker
- `skills.md` — Loader / Guard / FuzzyMatch / Metrics / Reviewer / Validator
- `memory.md` — 14 文件：Drawer / Stack / Markdown / Chunker / Compactor / Reflect
- `persona.md` — 模板/运行时 / Loader / Selector / PromptBuilder 8 层
- `llm-and-cost.md` — LLMClient / ConfigManager / ProviderManager / CostManager
- `storage-and-events.md` — 18 表 / EventBus / 事件流

### 进行中计划（`docs/superpowers/plans/`）
- 2026-04-02 Phase 1 Agent loop redesign（CC 借鉴，已落地）
- 2026-04-09 Hermes inspired enhancements（Guard / FuzzyMatch，已落地）
- 2026-04-14 Tool policy pipeline（已落地）
- 2026-04-16 Memory Palace（Phase 1 已落地，Phase 2+ 待启动）
