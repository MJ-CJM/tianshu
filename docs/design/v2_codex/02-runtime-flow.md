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

## 5. 运行中 follow-up

`POST /api/edicts/{edict_id}/follow-up` 会：

1. 拒绝已关闭 Edict。
2. 拒绝存在 active Memorial 的并发 follow-up。
3. 从历史 Memorial 构建多轮上下文。
4. 创建新 Memorial，携带本轮 override。
5. 直接调 `executor.execute_edict()`，不重新走 Scheduler/Planner。

thinking-mode 模型要求历史 assistant 消息带 `reasoning_content`，老 Memorial 缺失时会跳过 assistant 消息，避免上游 400。
