# agent 执行引擎 · 实现现状

**相关设计**：[../../design/agent/](../../design/agent/)

覆盖 `src/tianshu/executor/`（含 `compaction/`、`orchestrator/`）与 `src/tianshu/dag/`。

## 1. 关键类 / 文件路径

| 主题 | 文件 | 关键符号 |
|---|---|---|
| ReAct 主循环 | `executor/agent.py` | `Agent.execute()`、`AgentResult`、`ASSISTANT_ONLY_TOOLS` |
| 单轮状态 | `executor/loop_state.py` | `LoopState`（frozen，`next_turn`/`with_recovery`/`with_compacted`/`accumulate_usage`） |
| 退出原因 | `kernel/exit_reason.py` | `ExitReason(StrEnum)` 10 值 |
| 流式 | `executor/streaming.py` | `StreamCallback`(Protocol)、`CancellationToken` |
| 钩点 | `kernel/hooks.py` | `HookRegistry`、`HookType`、`HookResult`、`HOOK_TIMEOUTS` |
| policy 钩 | `executor/policy_hook.py` | `PolicyHook`（priority 5） |
| 审批 | `executor/approvals.py` | `ApprovalManager`（priority 10） |
| 取消/恢复 | `executor/cancel.py`、`executor/checkpoint.py`、`executor/retry.py` | cancel 信号、checkpoint、重试 |
| 压缩 | `executor/compaction/{micro,auto,reactive,token_estimator}.py` | `micro_compact`、`auto_compact`/`should_auto_compact`、`reactive_compact`、`estimate_tokens` |
| 并发 | `executor/worker_pool.py`、`executor/lanes.py`、`executor/worker.py` | `WorkerPool`、`LaneManager`/`SessionLane`/`GlobalLane`、`Worker` |
| DAG 调度 | `executor/dag_scheduler.py` | `DAGScheduler.run()`、`_schedule_ready()` |
| DAG 图 | `dag/graph.py`、`models/dag.py` | `DAG`、`DAGExecution`、`DAGNode`、`DAGNodeStatus` |
| 分派 | `executor/executor.py` | `Executor.handle_plan_completed()`、`execute_edict()`、`_run_outer_loop()` |
| outer loop | `executor/orchestrator/` | 见 §4 |

## 2. 核心流程

### 2.1 分派（executor.py）

`handle_plan_completed(event)` 按优先级选路径：

```text
if edict.acceptance is not None and orchestrator_ctx: -> outer loop
elif plan and len(plan.tasks) > 1 and dag_scheduler:  -> DAG (plan.to_dag())
else:                                                  -> 单次 Agent
```

`execute_edict()` 用于 follow-up：先 `_apply_overrides` 合并 memorial 的 `runtime_override`/`acceptance_override` 到 edict 副本（不回写原行），合并后有 acceptance 也会切到 outer loop。

### 2.2 ReAct（agent.py）

`Agent.execute(edict, memorial, persona, history, user_content, stream_callback, cancellation_token, …) -> AgentResult`：读 `config_manager.state` → 解析 persona LLM override → 经 `provider_manager.get_client` 或直建 `LLMClient` → PromptBuilder 构建 system → 循环（micro/auto compact → BEFORE_ITERATION → chat → LLM_OUTPUT → 工具或结束）→ AGENT_END。`AgentResult` 含 `exit_reason`、`iteration_count`、`compact_count`、`reasoning_content`。

### 2.3 DAG（dag_scheduler.py）

`DAGScheduler.run(edict, execution)`：`DAG.from_execution` → 拓扑校验（环→failed）→ `dag.started` → `_schedule_ready` 循环。每节点：取 persona（回退 bingbu）→ 收集上游产出 → acquire session/global lane → `Worker.execute_node` → 完成回调 `mark_completed`/`mark_failed`+`propagate_failure` → 重算就绪。终态聚合 usage/result/final_output 回 root Memorial，发 `execution.{status}`。

## 3. 压缩常量

| 常量 | 值 | 位置 |
|---|---|---|
| `COMPACT_THRESHOLD_RATIO` | 0.75 | auto.py |
| `PRESERVE_TAIL` | 6 | auto.py |
| `_TRUNCATE_MIN_CHARS` | 200 | micro.py |
| reactive 激进 micro | `keep_recent=2` | reactive.py |
| token 估算 | `len // 3`（高估） | token_estimator.py |

## 4. orchestrator 模块结构

| 文件 | 职责 |
|---|---|
| `loop.py` | 主编排 `run()`、`OrchestratorContext`、`derive_actor_override`、`ActorOverride`、软着陆/lifecycle/L2 会诊/L3 人工 |
| `state.py` | `OuterLoopState`、`IterationRecord`、`CriticResult`、`ChecksResult`、`CheckOutcome`（全 frozen） |
| `checks.py` | `run_checks`（bash/lint/rubric 并发）、`ChecksConfigError` |
| `critic.py` | `review`、`ISSUE_CLASSES`、strictness guidance、`CriticUnavailable` |
| `audit.py` | `run_completion_audit`、`AuditGap`/`AuditResult`、`format_gaps_for_continuation` |
| `escalation.py` | `decide_escalation`（纯函数 FSM，返回 L0/L1/L2/L3/EXHAUSTED） |
| `budget.py` | `compute_usage_ratio`、`dominant_dimension`、`SOFT_LANDING_THRESHOLD=0.9`、`HARD_LIMIT=1.0` |
| `lifecycle.py` | `can_transition`、`apply_transition`、`PhaseTransition` |
| `human_decision.py` | `HumanDecision`（continue/accept_as_is/abort/modify_acceptance） |
| `supervision.py` | `generate_supervision_report`（4 章节，按 memorial_id+persona_id 存） |
| `persistence.py` | `persist_iteration`（写 outer_loop_iterations）、`emit_audit` |
| `templates.py` | `render_template`、`TemplateName`（critic / audit prompt 模板） |
| `archive.py` | iteration 归档辅助 |

## 5. 扩展点

- **新治理逻辑**：实现 `HookHandler` 协程，`hook_registry.register(HookType.X, handler, priority)` 注入；安全关键放 `before_tool_call`（fail-secure）。
- **新 check 类型**：扩展 `CheckSpec.kind` + `checks.py` 的 `_dispatch` 分支。
- **新升级动作**：`escalation.decide_escalation` 加 level 分支 + `loop.py` 接对应动作。
- **新流式后端**：实现 `StreamCallback` 三方法（参考 notifier 的 `WebSocketStreamCallback`）。
- **新压缩策略**：在 `compaction/` 加模块，主循环按触发条件调用并返回新 `LoopState`。

## 6. 已知现状

- Agent 侧 context limit 默认写死，未从 provider metadata 派生。
- auto compact 连续失败熔断（`MAX_CONSECUTIVE_FAILURES`）为 TODO。
- L1 的 thinking_budget/model_upgrade 字段已传入 `ActorOverride`，actor 端实际消费部分为预留。
