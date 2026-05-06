# 03 执行引擎设计

## 1. Agent ReAct 循环

`executor/agent.py` 是单任务执行核心。执行流程：

```text
构建 system prompt
  -> 合并 history / user_content
  -> LoopState 初始化
  -> while iteration < max_iterations:
       micro_compact
       auto_compact if needed
       BEFORE_ITERATION hook
       LLM chat/chat_stream
       LLM_OUTPUT hook
       if tool_calls:
           BEFORE_TOOL_CALL hook
           ToolRegistry.execute
           AFTER_TOOL_CALL hook
           append tool result
           next_turn
       else:
           handle length continuation or finish
  -> AgentResult
```

设计要点：

- `LoopState` 是不可变状态对象，每轮返回新状态，降低隐式状态风险。
- T0 只读工具在 Agent 层和 Registry 层都有 fast path，减少治理噪声。
- tool result 会按 `ToolDefinition.max_result_chars` 截断。
- `finish_reason=length` 会最多 3 次自动续写。
- 上下文溢出会先尝试 reactive compaction。
- 配置了 fallback LLM 时，主模型失败可切换备用配置。

## 2. 上下文压缩

Agent 内置三层压缩：

| 类型 | 触发 | 目的 |
|---|---|---|
| micro compact | 每轮开头 | 清理工具结果等低成本收缩 |
| auto compact | 接近 context limit | LLM 摘要，预防溢出 |
| reactive compact | 捕获 context overflow | 出错后救急恢复 |

当前 context limit 默认写死为 `128000`，后续可从 provider metadata 派生。

## 3. DAG 执行

多任务 plan 会转成 `DAGExecution`：

1. Executor 创建 root Memorial。
2. `plan.to_dag()` 生成 DAG。
3. `Storage.save_dag_execution()` 持久化执行和节点。
4. `LaneManager` 提供全局和 session 级并发限制。
5. `DAGScheduler.run()` 按依赖拓扑调度 Worker。
6. 每个节点可有自己的 Memorial、persona 和 checkpoint。

DAG 支持取消和失败节点重试：

- `Executor.cancel_dag()` 使用 `CascadeCanceller` 级联取消。
- `Executor.retry_dag()` 使用 `PartialRetrier` 重置失败/取消节点后重跑。

## 4. 长任务 Outer Loop

当 `Edict.acceptance` 非空，Executor 进入 `executor/orchestrator/loop.py`。它不是单次 Agent 输出，而是“actor -> checks -> critic -> audit -> upgrade”的外循环。

### 4.1 单轮结构

```text
检查 pause / budget / lifecycle
  -> actor Agent.execute
  -> run_checks
  -> critic review
  -> persist outer_loop_iterations
  -> completion audit
  -> pass / continue / escalate / exhaust
```

### 4.2 Checks

`AcceptanceCriteria.checks` 支持：

- `bash`
- `lint`
- `rubric`

checks 不通过时，critic 结果会转成 fail，问题类为 `checks_failed`。

### 4.3 Critic 与 completion audit

checks 通过后，critic 负责判断 actor output 是否满足验收。critic pass 之后还会跑 completion audit 门：

- critic 可用时，由 critic LLM 审核是否真的覆盖目标；
- critic 被 skip 时，退化成 actor self audit；
- audit 不通过会把 gaps 渲染成 continuation prompt，进入下一轮。

这避免“critic 通过但目标漏项”的终态漏洞。

### 4.4 升级路径

Outer loop 的升级级别：

| Level | 含义 |
|---|---|
| L1 | 注入上一轮 critic feedback，可配置 thinking budget / model upgrade 的预留字段 |
| L2 | 触发 ConsultationSession，多 persona 会诊给 actor 建议 |
| L3 | 请求人工决策，支持 continue、accept_as_is、abort、modify_acceptance |

如果超过 `max_outer_iterations`，按 `on_exhaustion` 决定 best effort、fail 或 L3 escalate。

### 4.5 预算与 lifecycle

Outer loop 每轮前计算 token、成本、时间的 usage ratio：

- 达到 soft landing 阈值：进入 `winding_down`，把收尾 prompt 注入下一轮。
- 达到 hard limit：若已 winding_down 则强制终止，否则先进入 winding_down。
- `winding_down` 阶段 ToolRegistry 会拦截 `side_effect=True` 的工具。
- pause 状态下，checkpointed/background 会保存 checkpoint 并返回 `needs_review`。

### 4.6 Checkpoint 和监督报告

`execution_profile in ("checkpointed", "background")` 时，outer loop 会保存/恢复 `outer_loop_checkpoints`。

终态时如果配置了 critic persona，会生成 `supervision_reports`。多监督官按 `(memorial_id, persona_id)` 存储，避免 follow-up 间互相覆盖。

## 5. 失败恢复策略

| 失败点 | 策略 |
|---|---|
| Planner LLM 失败/JSON 解析失败 | fallback 到 passthrough plan |
| Agent context overflow | reactive compact 后重试 |
| Agent LLM 失败 | fallback LLM 或返回 `LLM_ERROR` |
| 工具异常 | 转成 tool error 返回给 LLM |
| Executor timeout | Memorial failed |
| hook block | Memorial failed 或 AgentResult failed |
| outer loop checks 配置错误 | 终止 failed |
| critic 不可用 | 按 `on_critic_unavailable` skip 或 fail/escalate |
| retry_limit 未耗尽 | 创建下一 attempt Memorial 自动重试 |
