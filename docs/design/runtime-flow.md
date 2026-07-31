# 02 运行时装配与主流程

## 1. FastAPI 生命周期

`create_app()` 只负责创建 FastAPI、注册路由和挂载静态前端；真正的系统装配在 `lifespan()`。

重要装配顺序：

1. `Storage` 初始化数据库。
2. `EventBus()` 建立进程内派发；`EventHistoryConsumer` 投影时间线，outbox 负责关键事件持久化。
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
13. `PluginApi` 登记本地插件清单（不加载代码），随后启动
    `ProfileSynthesizer`、`DigestGenerator`、`SkillsWatcher` 等外围能力。

## 2. EventBus 与持久执行链

当前装配位于 `bootstrap/wiring_*.py`。HTTP 或调度入口先持久化 Memorial、执行 attempt
和 outbox，再唤醒本进程 reconciler/dispatcher；旧版事件入口继续由
`ManagedRunIngress.adopt_legacy` 接管，以便升级后的在途任务仍能进入同一持久执行链。

| event | handler | priority |
|---|---|---|
| `edict.submitted` | `scheduler.handle_submitted` | 100 |
| `edict.scheduled` / `plan.completed` / `edict.resume` | `managed_run_ingress.adopt_legacy` | 100 |
| `execution.completed` | `auditor.handle_execution_completed` | 100 |
| `execution.failed` | `auditor.handle_execution_failed` | 100 |
| `execution.completed` | `cost_manager.handle_execution_completed` | 150 |
| `execution.completed` | `memory_manager.handle_execution_completed` | 200 |
| `execution.failed` | `notifier.handle_execution_failed` | 100 |
| `execution.failed` | `cost_manager.handle_execution_failed` | 150 |
| `execution.cancelled` | `cost_manager.handle_execution_cancelled` | 150 |
| `audit.completed` | `notifier.handle_audit_completed` | 100 |
| `audit.completed` | `memory_manager.handle_audit_completed` | 200 |
| `cost.budget_exceeded` | `notifier.handle_execution_failed` | 100 |
| `outer_loop.*` | `notifier.handle_outer_loop_event` | 100 |

`EventBus.emit()`/`fire()` 仍承担广播与兼容入口，但不能把“事件已经出现”当成“执行已经
完成”。根执行由 `execution_attempts` 的租约、心跳和 fencing token 监督；进程丢失租约后，
旧 runner 无权提交终态。Scheduler fire 也以确定性 run 身份、游标 CAS 和 outbox 绑定，避免
重启或重放产生两次根执行。

## 3. 下旨到执行

### 3.1 Gateway 提交

`POST /api/edicts` 的关键动作：

1. 根据 `idempotency_key + submitter` 去重。
2. 校验 persona、网络 runtime 白名单、调度参数和长任务组合。
3. 保存 `Edict`。
4. 创建首条 `Memorial(status=submitted)`。
5. 持久化事件/执行入口，再唤醒后台 reconciliation。
6. HTTP 首次接受返回 `202`；相同幂等请求重放返回 `200`。

`once` 必须有未来时间，`cron` 必须有合法表达式，`interval_seconds` 必须大于 0；非法
配置返回 `422`，不会降级成“立即执行”。幂等重放已成功接受的 once 请求时，不会因为时间
后来已过去而误拒绝。

### 3.2 Scheduler

Scheduler 根据 `Edict.schedule` 决策：

| schedule | 行为 |
|---|---|
| `immediate` | 直接发 `edict.scheduled` |
| `once` | 写入 `scheduler_jobs`，到点触发 |
| `cron` / `interval` | 写入持久游标，每个槽位创建独立 run |

调度 job 的用户可见状态是 `active / paused / completed / failed`；取消后保留
`cancelled` 记录但不再出现在正常列表。支持暂停、恢复、修改时间、立即运行和查看 run
历史。立即运行要求 `Idempotency-Key`，不改变原时间表。一次任务正常触发后标记
`completed`，不再误写为 cancelled。

周期 misfire 只合并补最近一次（`coalesce`）；并发策略默认 `skip`。长任务只允许
`immediate/once + skip`，周期长任务从 API、工具和恢复路径统一 fail closed。

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

持久 attempt 获得租约后，managed runner 完成规划并选择执行路径：

```text
if edict.acceptance is not None:
    run orchestrator outer loop
elif plan.tasks > 1:
    run DAGScheduler
else:
    run Agent directly
```

follow-up 的 `runtime_override` 和 `acceptance_override` 只在当前 Memorial 生效，不回写
Edict 行。其创建、幂等身份、attempt 和 outbox 在一个事务中提交，不再从 HTTP handler
直接调用 Executor。

### 3.5 Executor 路由决策树

当前执行主体是 dispatcher 赢得租约后的 managed runner。旧
`executor.handle_plan_completed`/`execute_edict` 仍是兼容执行实现，但新建根任务和
follow-up 都先进入 durable attempt，再应用 Memorial override 后分派：

```text
managed attempt runner
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
        └─► execute_edict  →  Agent.execute
```

分派判据优先级：**acceptance（长任务）> 多任务 plan（DAG）> 单 Agent**。Memorial
override 在分派前生效，因此一条 follow-up 可以把原本的单 Agent 任务升级成长任务 outer
loop（补了 `acceptance_override`），或只调整 runtime。follow-up 不重走 Scheduler，但会
进入与普通根执行相同的受监督 planning/execution attempt。

### 3.6 长任务恢复、暂停与 steer

- 新建深度任务统一落为 `checkpointed`，并保证至少一次租约恢复机会。
- 恢复时优先从持久 attempt/continuation 状态重建，旧
  `outer_loop_checkpoints` 只是兼容 fallback。
- pause 请求在当前轮边界生效；进程仍在时原地等待 resume，checkpointed/background 会先
  保存 checkpoint。进程重启后由 durable attempt 恢复，而不是依赖内存 task。
- steer 先进入 `pending_steers`，下一轮注入 actor。只有 checkpoint 成功后才确认删除，
  避免“读取后崩溃”丢指令。
- actor 已返回明确失败/取消时直接保持该终态，不再让 critic 改写；checkpoint 只在最终
  Memorial 终态持久化和监督报告收口后清理。

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
| `AGENT_END` | `MemoryManager`, `SkillReviewHandler`, `ProfileTrigger` | 写记忆、画像合成触发；SkillReviewHandler 默认关闭且在 LLM 前 fail fast，不直接写 live Skill |
| `SESSION_END` | 预留/插件 | 会话结束 |

### 4.1 审批层级 — 统一持久化 Decision

人工裁决以 `DecisionRequestV1` 为权威，状态为
`pending/resolved/expired/cancelled`，resolve 使用 expected version 做 CAS。当前 kind
包括 `tool`、`outer_loop`、`plan_review` 和 `governed_apply`，互不复用授权语义：

| kind | 触发点 | 结果影响 |
|---|---|---|
| `tool` | `PolicyHook` 收到 `require_approval` | 仅决定当前工具提案；默认等待上限 300s |
| `outer_loop` | 长任务验收穷尽并配置 escalate | continue / accept_as_is / abort / modify_acceptance |
| `plan_review` | 规划需要人工复核 | 恢复或终止同一受管运行 |
| `governed_apply` | 隔离工作区变更申请合入 | 决定是否执行受治理 apply |

请求和 continuation 在事务内持久化；`decision.resolved/expired` 通过 outbox 派发，
`ContinuationRecoveryService`、plan review lifecycle 或 workspace service 幂等收敛。
旧 `Decree` 只作为兼容投影，不再是恢复权威。

**预授权抹掉审批（auto_approve_max_tier）**：工具级审批可以被「事前预授权」整段省掉——`PolicyProfile.auto_approve_max_tier`（见 [tools/policy.md](./tools/policy.md) §5）在 `DefaultTierRule` 里直接放行 `tool_tier ≤ auto_approve_max_tier` 的工具，根本不进 require_approval；session 规则缓存（policy.md §8）则在 `PolicyHook` 把已审过的 `require_approval` 改判 `allow`。两者只作用于 `tool`，不影响长任务、规划复核或受治理合入。

## 5. 运行中 follow-up

`POST /api/edicts/{edict_id}/follow-up` 会：

1. 拒绝已关闭 Edict。
2. 要求 `Idempotency-Key`，同 key 同 envelope 可安全重放，冲突 envelope 拒绝。
3. 在事务内拒绝存在 active 根 Memorial 的并发 follow-up。
4. 从历史 Memorial 构建多轮上下文。
5. 创建新 Memorial，携带本轮 override，并同时创建 attempt/outbox。
6. 唤醒 reconciler/dispatcher，进入同一受监督的 planning/execution attempt；不重新走
   Scheduler。

thinking-mode 模型要求历史 assistant 消息带 `reasoning_content`，老 Memorial 缺失时会跳过 assistant 消息，避免上游 400。
