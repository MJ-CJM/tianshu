# 实现总览（HEAD @ feat_phase8）

本目录每份文档描述 **当前代码** 的一个横切面。设计意图与稳定契约见 `../design/`。

## 启动序列（FastAPI lifespan → bootstrap/ 装配）

装配逻辑已从单体 `lifespan()` 拆分为 `src/tianshu/bootstrap/` 包的 wiring 函数序列
（`wiring_storage / wiring_tools / wiring_skills / wiring_memory / wiring_persona /
wiring_llm / wiring_executor / wiring_channels / wiring_scheduler / wiring_universe`
等，`app.py` 的 `lifespan()` 只保留 ~40 行顺序编排）。下列实例化顺序不变，
每步对应的 wiring 文件见 `bootstrap/` 内同名模块：

1. `Storage` → `storage.init_db()`（SQLite，WAL 模式，线程锁）
2. `EventBus`（持久化事件到 `events` 表）
3. `HookRegistry`（生命周期钩点注册中心）
4. `ToolRegistry` ← `register_builtins` + `register_memory_tools` + `register_skill_tools`
5. `SkillsLoader`（builtin / workspace / user 三层）+ `SkillMetricsStore`
6. `PersonaLoader`（git 模板 + runtime 目录）
7. `DrawerStore` + `MemoryConfig` → Memory Palace 初始化
8. `PromptBuilder`（8 层注入）
9. `ConfigManager`（LLM + Agent 配置状态）+ `ProviderManager`
10. `Agent`（主 ReAct 循环）
11. `WorkerPool` + `LaneManager`（并发与 lane 隔离）
12. `Auditor`、`ChannelRegistry`、`Notifier`、`CompositeSessionRuleStore`
13. `Executor` → 注入 agent / persona_loader / dag_scheduler / lane_manager
14. `DAGScheduler`（拓扑调度）
15. `ApprovalManager`、`PolicyEngine` + `PolicyHook`（注册到 `BEFORE_TOOL_CALL`，priority=5）
16. `MemoryManager` → `ensure_memory_dirs()`，订阅 `BEFORE_AGENT_START` / `AGENT_END`
17. `CostManager`（`BEFORE_ITERATION` / `LLM_OUTPUT`）、`ConsultationSession`、`PerformanceEvaluator`
18. `OfficialSelector`、`Planner`、`Scheduler`
19. **EventBus 订阅链**（见下）
20. `PluginApi` + `PluginLoader.discover()`
21. `SkillReviewHandler`（`AGENT_END` priority=200）
22. `DigestGenerator` + 24h 循环任务
23. `SkillsWatcher`（watchdog 可选）
24. `scheduler.start()`

## EventBus 订阅链

来自 `src/tianshu/app.py` 第 336–346 行：

| event | handler | priority |
|---|---|---|
| `edict.submitted` | `scheduler.handle_submitted` | 默认 100 |
| `edict.scheduled` | `planner.handle_scheduled` | 50 |
| `plan.completed` | `executor.handle_plan_completed` | 100 |
| `execution.completed` | `auditor.handle_execution_completed` | 默认 |
| `execution.completed` | `cost_manager.handle_execution_completed` | 150 |
| `execution.completed` | `memory_manager.handle_execution_completed` | 200 |
| `execution.failed` | `notifier.handle_execution_failed` | 默认 |
| `execution.failed` | `cost_manager.handle_execution_failed` | 150 |
| `audit.completed` | `notifier.handle_audit_completed` | 默认 |
| `audit.completed` | `memory_manager.handle_audit_completed` | 200 |
| `cost.budget_exceeded` | `notifier.handle_execution_failed` | 默认 |

另有 `plan.pending_review`（审批模式）由 Planner 发出、Executor 按需处理。

## 模块树

`src/tianshu/` 一级：

| 目录 / 文件 | 定位 | 详见 |
|---|---|---|
| `app.py` | FastAPI lifespan 入口 | 本文 |
| `gateway/`, `gateway.py` | HTTP/WS 路由 | — |
| `storage/` | SQLite 单一真相源（`_base` + 15 领域 Mixin + facade） | `storage-and-events.md` |
| `bus/` | EventBus 发布-订阅 | `bus/README.md` |
| `models/` | 数据契约（Edict/Memorial/Decree/Event/Plan/…） | — |
| `executor/` | Agent 循环 + DAG + Hook + Policy | `executor.md` |
| `planner/` | LLM 规划 / passthrough | `executor.md` |
| `scheduler/` | Cron / 一次性调度 | `executor.md` |
| `persona/` | 人格加载、选择、Prompt 构建 | `persona.md` |
| `skills/` | Skills 渐进加载 + guard + fuzzy | `skills.md` |
| `memory/` | Memory Palace + Markdown 后端 | `memory.md` |
| `llm.py` | LLMClient（chat / chat_stream / cache_control / fallback） | `llm-and-cost.md` |
| `config_manager.py` | LLM + Agent 配置状态 | `llm-and-cost.md` |
| `providers/` | LiteLLM 适配 + 配额 | `llm-and-cost.md` |
| `cost/` | 成本账本、预算、熔断 | `llm-and-cost.md` |
| `tools/` | 内建工具 + policy + skill/memory 工具 | `executor.md` / `skills.md` |
| `secrets/` | 凭证加密托管 + 按 host 注入 | `secrets/README.md` |
| `auditor/` | 规则引擎 + 审计复核 | `auditor/README.md` |
| `notifier/` | 渲染 + 多通道（飞书/钉钉/邮件/WS） | — |
| `consultation/` | 多人格咨询会话 | `consultation/README.md` |
| `plugins/` | PluginApi + manifest loader | `plugins/README.md` |
| `dag/` | 图模型与算法 | `executor.md` |
| `web/`, `web.py` | 静态前端挂载 | — |
| `cli/` | 管理 CLI | — |

## SQLite 表（共 38+ 张业务表，含 FTS）

由 `Storage.init_db()` 创建：

**业务核心**
- `edicts`（goal/context/status/priority/schedule_json/assigned_persona_id/planner_persona_id/plan_review 等）
- `memorials`（edict_id FK，status/result/usage_json/audit_json/timeline_json/dag_node_id/persona_id/attempt）
- `events`（edict_id, memorial_id, event_type, payload_json）
- `decrees`（memorial_id, action, comment, amended_goal, actor）

**DAG 执行**
- `dag_executions`（edict_id, plan_json, status, root_memorial_id, max_concurrency）
- `dag_nodes`（dag_execution_id + node_id 联合主键；depends_on_json、checkpoint_json、memorial_id）

**Memory Palace**
- `memory_entries`（persona_id, edict_id, category, content, confidence, access_level, expires_at）
- `memory_fts` + `memory_fts_config` / `_data` / `_docsize` / `_idx`（FTS5 虚表及其辅表）

**Drawer 独立库**：`~/.tianshu/memory/drawers.sqlite3`（`drawers` + `drawers_fts`，见 `memory.md`）

**成本**
- `cost_ledger`（edict_id, memorial_id, provider_name, model, prompt/completion/total_tokens, cost_cny）
- `cost_budgets`（scope, budget_cny, spent_cny, period, reset_at）

**配置 / 元数据**
- `llm_configs`（name 主键；ConfigManager 多配置）
- `providers`（name 主键；model/api_base/capabilities/rpm_limit/tpm_limit/priority）
- `scheduler_jobs`（job_id, edict_id, schedule_type, cron_expr, next_run）
- `session_rules`（Policy 会话级规则存储）
- `departments`、`personas`（部门与人格元数据，由 PersonaLoader 同步）
- `plugins`（name, version, manifest_json, sha256）
- `skill_metrics`（skill_name, used_count, …）

## 人格目录（7 部门）

`personas/` 为 git 跟踪的模板，`~/.tianshu/personas/{id}/` 为运行时覆盖。每个部门含 `SOUL.md` + `ROLE.md` + `MEMORY.md`；`court/` 独有 `COURT.md`（共享上下文）。

| id | 隐喻 | 职能（代码中的角色） |
|---|---|---|
| `neige` | 内阁 | 战略规划 Planner 人格、跨部门协调 |
| `bingbu` | 兵部 | Default executor（`DEFAULT_EXECUTOR_ID = "bingbu"`，见 `persona/model.py:10`） |
| `ducha` | 都察院 | 审计、Code Reviewer |
| `tongzheng` | 通政司 | 渲染、通知、咨询 session 主持 |
| `wenyuan` | 文渊阁 | 文档与知识管理 |
| `hubu` | 户部 | 成本审查、配额裁决 |
| `court` | 朝廷共享 | 所有 persona 共享的 `COURT.md` 上下文（Layer 2） |

详见 `persona.md`。

## 前端页面 ↔ 后端路由

| 页面 | 主要后端路由（`/api` 前缀） |
|---|---|
| `EdictCreatePage` / `EdictDetailPage` | `POST /edicts`, `GET /edicts/{id}`, `GET /edicts/{id}/events`, `PATCH /edicts/{id}`, `DELETE /edicts/{id}` |
| `RoyalStudyPage`（御书房，合并页双 Tab；`EdictListPage`/`ApprovalQueuePage` 已退役） | `GET /edicts`（status=open 或分页查询）, `POST /edicts/latest-memorials`（批量最新奏折）, `GET /approvals/pending_tool_calls`, `POST /approvals/tool_decision`, `POST /decrees`, `DELETE /edicts/{id}` |
| `MemoryDashboardPage` | `GET /memory/{persona_id}`, `POST /memory/recall`, `POST /memory-palace/search`, `POST /memory-palace/l1` |
| `PersonaDashboardPage` / `PersonaDetailPage` | `GET /personas`（详情页由列表本地过滤,无单条 GET 路由）, `POST /personas`, `PUT/DELETE /personas/{id}`, `GET /personas/{id}/metrics`, `GET /personas/{id}/profile` |
| `AuditDashboardPage` | `GET /audit/stats`, `GET /audit/recent` |
| `CostDashboardPage` | `GET /costs/summary`, `GET /costs/budgets` |
| `SchedulerPage` | `GET /scheduler/jobs`, `DELETE /scheduler/jobs/{id}` |
| `AuditDashboardPage`（都察院运维 Tab：EventBus/Workers/Hooks，原 `OpsMonitorPage` 死页壳已删） | `GET /event-bus/stats`, `GET /event-bus/recent`, `GET /hooks/registry`, `GET /workers/status`, `GET /notifications/channels` |
| `ConsultationPage` | `POST /consultation`, `GET /consultation/{id}` |
| `SessionRulesPage` | `GET/POST /session_rules` |
| `SystemManagementPage` | `GET/POST /config`, `GET/POST /llm_configs`, `/providers/*`, `/plugins/*` |

## 配置与数据位置

| 路径 | 用途 |
|---|---|
| `~/.tianshu/tianshu.db` | 主 SQLite（38+ 表） |
| `~/.tianshu/memory/drawers.sqlite3` | Drawer 独立库 |
| `~/.tianshu/memory/{persona_id}/MEMORY.md` + `logs/` | 持久记忆 |
| `~/.tianshu/personas/{id}/` | 运行时人格覆盖（`SOUL.md`, `ROLE.md`） |
| `~/.tianshu/skills/` | 用户级 skills |
| `~/.tianshu/logs/` | 运行日志 |
| `personas/` | git 跟踪的人格模板 |
| `src/tianshu/skills/builtin/` | 内建 skills（`file-ops`、`shell`） |
| `plugins/` | 顶层插件 manifest 目录 |
