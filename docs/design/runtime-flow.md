# 02 运行时装配与主流程

## 1. FastAPI 生命周期

`create_app()` 只负责创建 FastAPI、注册路由和挂载静态前端；真正的系统装配在 `lifespan()`。

重要装配顺序：

1. `Storage` 初始化数据库。
2. `EventBus(storage)` 让事件自动持久化。
3. `HookRegistry` 提供 Agent 生命周期扩展点。
4. `ToolRegistry` 注册 builtins、memory、skills、edict 工具，并应用 DB 中的禁用列表。
5. `SkillsLoader` 加载 builtin、workspace、user 三层 skill。
6. `PersonaLoader` 加载 git 模板和运行时人格，并同步到 DB。
7. `DrawerStore`、`MemoryConfig`、`PromptBuilder` 初始化记忆与 prompt 构建。
8. `ConfigManager`、`ProviderManager`、`Agent` 初始化模型和 ReAct 引擎。
9. `WorkerPool`、`LaneManager`、`DAGScheduler` 提供并发与 DAG 能力。
10. `Auditor`、`Notifier`、`ApprovalManager`、`PolicyEngine`、`PolicyHook` 接入治理链路。
11. `MemoryManager`、`CostManager`、`ConsultationSession`、`OrchestratorContext` 接入横切能力。
12. `Planner`、`Scheduler` 完成主链路。
13. `PluginApi`、`ProfileSynthesizer`、`DigestGenerator`、`SkillsWatcher` 启动外围扩展。

## 2. EventBus 订阅链

当前 `app.py` 注册的主链路订阅：

| event | handler | priority |
|---|---|---|
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
| `outer_loop.*` | `notifier.handle_outer_loop_event` | 100 |

`EventBus.emit()` 会先持久化再按 priority 顺序执行 handler；`fire()` 会先持久化再后台调度 handler。API 提交任务使用 `fire()`，避免 HTTP 请求等待完整执行。

## 3. 下旨到执行

### 3.1 Gateway 提交

`POST /api/edicts` 的关键动作：

1. 根据 `idempotency_key + submitter` 去重。
2. 校验 persona、网络 runtime 白名单等输入。
3. 保存 `Edict`。
4. 创建首条 `Memorial(status=submitted)`。
5. `EventBus.fire("edict.submitted")`。
6. 返回 202。

### 3.2 Scheduler

Scheduler 根据 `Edict.schedule` 决策：

| schedule | 行为 |
|---|---|
| `immediate` | 直接发 `edict.scheduled` |
| `once` | 写入 `scheduler_jobs`，到点触发 |
| `cron` | 写入 cron job，循环触发 |

### 3.3 Planner

Planner 有两种路径：

| 条件 | 路径 |
|---|---|
| `assigned_persona_id` 非空 | 直接生成单任务 passthrough plan |
| 未直接指派 | 用全局 LLM 或 `planner_persona_id` 的 LLM 配置进行 JSON 规划 |

Planner prompt 会融合：

- 规划官 persona 上下文；
- 可用官员名册；
- 可用工具列表；
- 任务目标、上下文、约束、输出格式。

如果 `plan_review=true`，Planner 发 `plan.pending_review` 并把 Memorial 标为 `needs_review`；审批通过后 API 再发 `plan.completed`。

### 3.4 Executor

Executor 处理 `plan.completed` 后选择路径：

```text
if edict.acceptance is not None:
    run orchestrator outer loop
elif plan.tasks > 1:
    run DAGScheduler
else:
    run Agent directly
```

follow-up 的 `runtime_override` 和 `acceptance_override` 只在当前 Memorial 生效，不回写 Edict 行。

### 3.5 Executor 路由决策树

`executor.handle_plan_completed`（`plan.completed` 的 handler）是主入口；`execute_edict` 是单 Agent 快路径，也被 follow-up **直连**调用（不走 EventBus）。两条入口都先 `_apply_memorial_override` 合并 follow-up 的 runtime/acceptance override 到 edict 副本，再分派：

```text
handle_plan_completed(event)          ← plan.completed 事件入口
  ├─ get_edict / get_memorial
  ├─ edict = _apply_memorial_override(edict, memorial)   # follow-up override 浅合并，不回写
  │
  ├─ edict.acceptance ≠ None 且 orchestrator_ctx 已注入
  │     └─► _execute_outer_loop  →  orchestrator.run（actor/critic 长任务 outer loop）
  │
  ├─ plan.tasks > 1 且 dag_scheduler 存在
  │     └─► _execute_dag  →  DAGScheduler.run（多任务并发，复用根 memorial）
  │
  └─ 否则（单任务）
        └─► execute_edict  →  Agent.execute（Phase 2 兼容快路径）

execute_edict(edict, ...)             ← follow-up 直连入口（POST /follow-up）
  ├─ edict = _apply_memorial_override(edict, memorial)
  ├─ 合并后 edict.acceptance ≠ None 且 ctx 已注入   # follow-up 升级为长任务
  │     └─► _execute_outer_loop  →  orchestrator.run
  └─ 否则 → Agent.execute（单 Agent）
```

分派判据优先级：**acceptance（长任务）> 多任务 plan（DAG）> 单 Agent**。memorial override 在分派前生效，因此一条 follow-up 可以把原本的单 Agent 任务升级成长任务 outer loop（补了 `acceptance_override`），或反向只调 runtime 参数。follow-up 不重走 Scheduler/Planner，故直接进 `execute_edict`，由它内部再判一次 acceptance。

## 4. Hook 生命周期

Agent/Executor 周围有统一 HookRegistry。当前重要钩点：

| HookType | 主要注册者 | 作用 |
|---|---|---|
| `SESSION_START` | 预留/插件 | 会话开始 |
| `BEFORE_AGENT_START` | `MemoryManager` | 注入记忆 history |
| `BEFORE_ITERATION` | `CostManager` | 每轮预算检查 |
| `LLM_INPUT` | 预留/插件 | 修改 LLM 输入 |
| `LLM_OUTPUT` | `CostManager` | 记账 |
| `BEFORE_TOOL_CALL` | `PolicyHook`, `ApprovalManager` | 工具治理和审批 |
| `AFTER_TOOL_CALL` | 预留/插件 | 工具结果处理 |
| `AGENT_END` | `MemoryManager`, `SkillReviewHandler`, `ProfileTrigger` | 写记忆、skill 学习、画像合成触发 |
| `SESSION_END` | 预留/插件 | 会话结束 |

### 4.1 审批层级 — 两条平行队列

系统有**两个互不覆盖**的人工审批队列，对应不同粒度，都由 `ApprovalManager`（`executor/approvals.py`）维护独立的 `asyncio.Event` 字典：

| 层级 | 触发点 | 队列 | 等待/提交接口 | 超时 |
|---|---|---|---|---|
| **工具级（per-tool-call）** | `PolicyHook` 在 `BEFORE_TOOL_CALL` 收到 `require_approval` | `_pending` / `_results`（按 `memorial_id`） | `wait_for_approval` / `submit_tool_decision` | `APPROVAL_TIMEOUT=300s` |
| **L3（per-edict 长任务）** | orchestrator outer loop 在 `on_exhaustion="escalate"` 时 `_escalate_to_human` | `_outer_loop_pending` / `_outer_loop_results`（按 `edict_id`） | `wait_for_outer_loop_decision` / `submit_outer_loop_decision` | `deadline_seconds`，默认 86400s（24h） |

两条队列**键不同**（memorial vs edict）、**结果类型不同**（工具级返 `Decree` approve/reject；L3 返 `HumanDecision` continue/accept_as_is/abort/modify_acceptance）、**语义不同**（工具级只 unblock 当前 tool-call，不改任务状态；L3 决定整条长任务的去留），因此**互不覆盖**：批准一次工具调用不影响 L3，反之亦然。前端御书房分别用 `list_pending_tool_calls` 和 `list_pending_outer_loop` 渲染两类卡片。

**预授权抹掉审批（auto_approve_max_tier）**：工具级审批可以被「事前预授权」整段省掉——`PolicyProfile.auto_approve_max_tier`（见 [tools/policy.md](./tools/policy.md) §5）在 `DefaultTierRule` 里直接放行 `tool_tier ≤ auto_approve_max_tier` 的工具，根本不进 require_approval；session 规则缓存（policy.md §8）则在 `PolicyHook` 把已审过的 `require_approval` 改判 `allow`。两者都只作用于**工具级**队列，**不影响 L3**——L3 是长任务穷尽后的人在回路决策，不走 tier 预授权。

## 5. 运行中 follow-up

`POST /api/edicts/{edict_id}/follow-up` 会：

1. 拒绝已关闭 Edict。
2. 拒绝存在 active Memorial 的并发 follow-up。
3. 从历史 Memorial 构建多轮上下文。
4. 创建新 Memorial，携带本轮 override。
5. 直接调 `executor.execute_edict()`，不重新走 Scheduler/Planner。

thinking-mode 模型要求历史 assistant 消息带 `reasoning_content`，老 Memorial 缺失时会跳过 assistant 消息，避免上游 400。
