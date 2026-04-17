# 执行层（Executor）

覆盖 `src/tianshu/executor/` 全部 17 个文件 + `src/tianshu/planner/` + `src/tianshu/scheduler/` + `src/tianshu/tools/` 与 DAG。

---

## 1. Agent 主循环（`executor/agent.py`）

`Agent.execute(edict, persona=None, parent_memorial_id=None, …) -> AgentResult` 是 ReAct 主循环：

```text
loop while iteration < max_iterations:
  LoopState（不可变快照） → hooks[BEFORE_ITERATION]
  → LLMClient.chat(messages, tools)
  → hooks[LLM_OUTPUT]
  → 若无 tool_call → ExitReason.COMPLETED
  → 对每个 tool_call:
      hooks[BEFORE_TOOL_CALL]（policy + approval 可 block）
      tool.execute(args)
      hooks[AFTER_TOOL_CALL]
      append tool result 到 messages
  → state = state.next_turn(messages)
end
hooks[AGENT_END] → 返回 AgentResult
```

关键特性：
- `max_iterations`、`timeout_seconds` 来自 `AgentConfigState`
- 工具结果超 `ToolDefinition.max_result_chars` 自动截断（Hermes 风格）
- LLM 响应为空但有 `reasoning_content` → 使用推理内容（DeepSeek-R1 兼容）
- 上下文溢出（`_is_context_overflow`）触发 reactive compaction

## 2. LoopState + ExitReason + TokenEstimator

`loop_state.py` — `@dataclass(frozen=True) LoopState`，每轮通过 `next_turn` / `with_recovery` / `with_compacted` / `accumulate_usage` **返回新对象**，永不修改原对象。

字段：`messages`（tuple）、`iteration`、`transition_reason`、每轮 guard（`compact_attempted`、`output_recovery_count`）、会话累计（`total_compact_count`、`total_prompt_tokens`、`total_completion_tokens`）。

`exit_reason.py` — `ExitReason(StrEnum)`：

| 值 | 含义 |
|---|---|
| `completed` | 模型无工具调用、自然结束 |
| `max_iterations` | 达到 iteration 上限 |
| `context_overflow` | 上下文溢出且 reactive compact 也失败 |
| `timeout` | 执行超时 |
| `cancelled` | 外部 cancel（见 `cancel.py`） |
| `hook_blocked` | Hook 返回 `block=True` |
| `budget_exhausted` | CostManager 熔断 |
| `llm_error` | LLM 调用异常 |
| `output_truncated` | 输出被 provider 截断 |

`compaction/token_estimator.py` — 估算 messages 的 token 数，为 auto compact 阈值判断。

## 3. 三层 Compaction（`executor/compaction/`）

| 层 | 文件 | 触发 | 策略 |
|---|---|---|---|
| Reactive | `reactive.py` — `reactive_compact()` | 上下文溢出异常 | 截断中间 tool_call / tool_result 对，保留 system + 首尾消息 |
| Micro | `micro.py` — `micro_compact(state, keep_recent=4)` | 每轮末尾（预防性） | 对非最近 `keep_recent` 轮的 tool_result 做 `_truncate` |
| Auto | `auto.py` — `should_auto_compact()` + `auto_compact()` | token 估算超阈值 | `_pre_compress_tool_results` → LLM 摘要中段 → 保留首尾 |

`auto.py` 的关键函数：`_extract_existing_summary`（避免重复 summary）、`_format_for_summary`。Auto compact 失败后回退到 reactive。

## 4. Hook 系统（`executor/hooks.py`）

`HookType(str, Enum)` 共 10 种：

| 值 | 用途 |
|---|---|
| `before_agent_start` | 入口；MemoryManager L2 recall |
| `before_iteration` | 每轮开头；CostManager 预算检查 |
| `before_tool_call` | 工具调用前；**PolicyHook (priority=5) + ApprovalManager (priority=10)** |
| `after_tool_call` | 工具调用后 |
| `llm_input` | LLM 请求前 |
| `llm_output` | LLM 响应后；CostManager 记账 |
| `agent_end` | Agent 返回；MemoryManager 写记忆、SkillReviewHandler 学习 |
| `before_iteration` | （同上） |
| `before_compaction` | 压缩前 |
| `session_start` / `session_end` | 会话级 |

`HookRegistry.register(type, handler, priority=100)` — priority 小者先执行；`HookResult(block, reason, modified_args)` 可阻断或改写参数；`HOOK_TIMEOUT = 5.0` 秒硬超时。事件写入器 `set_event_writer(storage)` 用于审计。

## 5. PolicyHook（`executor/policy_hook.py` + `tools/policy*`）

Tier 化的工具权限：`PolicyEngine(rules=build_default_rules())` 读取 `tools/policy_rules/`，结合 `SessionRuleStore`（会话级 allow/deny）和 `ApprovalManager`（人工审批）。

- 注册于 `BEFORE_TOOL_CALL`，priority=5（先于 approval）
- 命中禁止规则 → `HookResult(block=True)` → ExitReason.HOOK_BLOCKED
- 命中需要审批 → 转 ApprovalManager（异步等待 decree）
- 允许的写入工具会做 `workspace_root` 路径校验（见 `tools/path_utils.py`）

## 6. Streaming（`executor/streaming.py`）

- `StreamCallback` protocol：`on_text_delta / on_tool_call_delta / on_finish`
- `WebSocketStreamCallback` 桥接到 `Notifier.broadcast_ws`
- Agent 若 `stream_callback` 非空，调用 `LLMClient.chat_stream` 并按 delta 回调
- 前端 `OpsMonitorPage` 通过 WS 订阅实时流

## 7. Worker / WorkerPool / Lane

- `worker.py` — `Worker` 封装单次 Agent 执行、memorial 持久化、事件发射
- `worker_pool.py` — 全局并发上限 (`max_global_concurrency`)；`submit(coro)` → `asyncio.Task`；`shutdown()` 优雅退出
- `lanes.py` — `LaneManager` 按 lane_key 做独立并发配额（默认 lane=persona_id），防止单官员独占 pool

## 8. DAG 调度（`executor/dag_scheduler.py` + `dag/`）

`dag/models.py` — `DAGExecution`、`DAGNode`、`DAGNodeStatus`。`dag/graph.py` — 拓扑排序、就绪节点选取、循环检测。

`DAGScheduler.handle_plan_completed(event)`：
1. 多任务 plan → 建 `dag_executions` + `dag_nodes` 记录
2. 拓扑排序 → 找就绪节点（依赖已 COMPLETED）
3. 为每个就绪节点：`persona = persona_loader.get(node.assigned_official)` → 建 child memorial → `worker_pool.submit(agent.execute(...))`
4. 子任务完成 → 更新 `dag_nodes.status` → 再算一轮就绪节点
5. 失败传播：某节点失败 → 下游依赖节点标为 BLOCKED；可配置 fail-fast 或 best-effort

## 9. Approval / Cancel / Checkpoint / Retry

| 文件 | 能力 |
|---|---|
| `approvals.py` | `ApprovalManager` 维护 `pending_tool_calls` map；等待 `/approvals/decide` 的 Decree |
| `cancel.py` | 外部 cancel 信号注入 LoopState；`ExitReason.CANCELLED` |
| `checkpoint.py` | Agent 状态 checkpoint，用于重试恢复 |
| `retry.py` | 失败重试策略（指数退避），memorial.attempt 递增 |

## 10. Planner（`planner/planner.py`）

两条路径：

- **直接指派**：`edict.assigned_persona_id` 非空 → `_passthrough_plan()` 返回单任务 plan（assigned_official = 指定人格）
- **内阁决策**：走 LLM。使用 `edict.planner_persona_id` 对应的 `llm_config_name`（若无则 global）。PromptBuilder 构建 planner persona context + officials roster + tools list，LLM 产出 JSON plan。`_extract_json` 三重兜底解析（直接 / 代码块 / 大括号匹配）。失败一律回退到 passthrough。

产出 Plan 后 emit `plan.pending_review`（`edict.plan_review=1`）或 `plan.completed`。

## 11. Scheduler（`scheduler/scheduler.py`）

订阅 `edict.submitted`：
- `schedule_type = "immediate"` → 立刻 emit `edict.scheduled`
- `schedule_type = "cron" / "at"` → 写 `scheduler_jobs`，循环任务到期触发

## 代码路径索引

- `src/tianshu/executor/agent.py`
- `src/tianshu/executor/executor.py`
- `src/tianshu/executor/loop_state.py`
- `src/tianshu/executor/exit_reason.py`
- `src/tianshu/executor/hooks.py`
- `src/tianshu/executor/policy_hook.py`
- `src/tianshu/executor/streaming.py`
- `src/tianshu/executor/worker.py`
- `src/tianshu/executor/worker_pool.py`
- `src/tianshu/executor/lanes.py`
- `src/tianshu/executor/dag_scheduler.py`
- `src/tianshu/executor/approvals.py`
- `src/tianshu/executor/cancel.py`
- `src/tianshu/executor/checkpoint.py`
- `src/tianshu/executor/retry.py`
- `src/tianshu/executor/compaction/auto.py`
- `src/tianshu/executor/compaction/reactive.py`
- `src/tianshu/executor/compaction/micro.py`
- `src/tianshu/executor/compaction/token_estimator.py`
- `src/tianshu/planner/planner.py`
- `src/tianshu/planner/prompts.py`
- `src/tianshu/scheduler/scheduler.py`
- `src/tianshu/tools/policy.py`
- `src/tianshu/tools/policy_store.py`
- `src/tianshu/tools/policy_profile.py`
- `src/tianshu/tools/policy_rules/`
- `src/tianshu/tools/registry.py`
- `src/tianshu/tools/builtins.py`
- `src/tianshu/dag/models.py`
- `src/tianshu/dag/graph.py`
