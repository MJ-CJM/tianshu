# Harness 执行壳

承接 DAG 调度的「执行壳」：把单个节点跑成一次受控的 Agent 执行，并在其外围套上并发治理（WorkerPool + 双层泳道）与失败恢复（级联取消 + 部分重试 + 节点级 checkpoint）。DAG 调度回答「下一个跑谁」，执行壳回答「怎么把它安全地跑完、跑挂了怎么办」。

**相关实现**：[../../impl/agent/harness-impl.md](../../impl/agent/harness-impl.md)
**相关设计**：[./dag.md](./dag.md) [./orchestrator.md](./orchestrator.md)

## 1. Worker：单节点执行的生命周期

`Worker.execute_node(edict, node, upstream_results, persona, ...)` 是执行壳最内层，把一个 `DAGNode` 跑成一次 `Agent.execute`。它围绕一条独立的 child `Memorial` 推进状态机：

| 阶段 | 动作 | Memorial 状态 |
|---|---|---|
| 建档 | 新建 Memorial（带 `dag_node_id`、`persona_id`），`save_memorial` | `SUBMITTED` |
| 起跑 | 记 `started_at`，`update_memorial` | `RUNNING` |
| 执行 | `Agent.execute(...)`，回填 summary/result/usage/error | （由 result.status 决定） |
| 落账 | `finally` 记 `completed_at`，`update_memorial` | `COMPLETED` / `FAILED` / `CANCELLED` |

设计要点：

- **上游 context 经 history 注入**：`upstream_results`（`{node_id: result_text}`）被拼成 `[Upstream node X result]: ...`，作为一条 `system` 消息塞进 `history` 传给 Agent——上游产出是「背景知识」而非用户指令，节点自身的 `node.description` 才作 `user_content`。这让每个节点的 ReAct 循环天然带上它依赖的产出，无需共享可变状态。
- **节点级 Memorial 是 fitness 与审计的锚点**：每个节点一份独立 Memorial，token 用量、事件流（`append_event`）、persona 归属都挂在它上面，root Memorial 只做最终聚合（见 [dag.md](./dag.md) §5）。
- **工具裁剪**：`node.tools_required` 非空时作为 `tool_filter` 下传，节点只暴露它声明需要的工具。
- **优雅取消**：捕获 `asyncio.CancelledError` 时，先把 Memorial 标 `CANCELLED` 落账，再 `raise` 让取消信号继续向上传播——不吞掉取消，但保证 DB 状态一致。普通 `Exception` 则标 `FAILED` 并把异常文本写进 `error`，**不** re-raise（由调度回调按 result.status 判失败）。`finally` 始终落 `completed_at`。

## 2. WorkerPool：信号量并发模型

`WorkerPool` 是全局执行槽，用一把 `asyncio.Semaphore(max_concurrency)` 限制同时在跑的协程数。装配时并发数取自配置 `max_global_concurrency`（默认 **8**；`WorkerPool` 构造器自身的默认值是 4，但生产装配以配置为准）。

- `submit(WorkItem, on_complete)`：`create_task` 包一层 `_run` 立即返回 `Task`，登记进 `_active_tasks`，`done_callback` 自动摘除。
- `_run` 在 `async with self._semaphore` 内执行 `item.coro_factory()`：拿到槽才真正跑，跑完/异常都在 `finally` 触发 `on_complete(node_id, error)` 回调（这是调度器重算就绪节点的钩子）。`CancelledError` 记 `error="Cancelled"` 并 re-raise；普通异常计入 `_failed_count`。
- `cancel(work_id)` 取已登记的 Task 调 `.cancel()`；`shutdown()` 取消全部并 `gather`。
- `status()` 暴露 `active_count / max_concurrency / completed_count / failed_count`，供运维观测。

## 3. LaneManager：双层泳道为何存在

WorkerPool 只管「全局有多少槽」，但两类需求它答不了：**单个 Edict 内部别一次铺太满**（否则一个大 DAG 占满全局槽会饿死其他 Edict），以及**系统级总闸**。`LaneManager` 用两层信号量解决：

| 泳道 | 粒度 | 默认 | 来源 |
|---|---|---|---|
| `GlobalLane` | 系统级背压总闸 | 8 | `max_global_concurrency` |
| `SessionLane` | 按 `edict_id` 隔离 | 1 | `edict.runtime.max_concurrency`（API 钳制 1–8） |

`SessionLane` 按 `edict_id` 懒创建并缓存（`get_session_lane`），保证「一个 Edict 的 DAG 内部并发」独立可调，互不影响。

**acquire 顺序与 finally 逆序释放**（在 `_schedule_ready` 的 `_run_node` 里）：

```text
acquire session_lane   # 先拿会话额度（细粒度，先排队）
  acquire global_lane  # 再拿全局背压
    Worker.execute_node(...)
  finally:
    release global_lane   # 逆序释放
    release session_lane
```

先 session 后 global、释放时反序，是标准的嵌套锁纪律：避免「拿了全局槽却卡在会话额度」造成全局槽被白白占用。`LaneManager.status()` 返回 `{global: {max,active,available}, sessions: {eid: {max,available}}}`，让背压状态可观测。

> 注意：当前 WorkerPool 的全局信号量与 GlobalLane 都用 `max_global_concurrency`，两者叠加构成全局背压；SessionLane 是真正按 Edict 隔离的那一层。

## 4. 失败恢复：级联取消 / 部分重试 / checkpoint

DAG 节点失败不是孤立事件——下游依赖它的节点不该再跑。三个组件分工承接「失败后怎么办」：

| 组件 | 触发 | 语义 |
|---|---|---|
| `CascadeCanceller` | 用户主动取消整个 DAG | RUNNING 节点 → 取消 worker 并标 CANCELLED，再 `propagate_failure` 把下游级联标 CANCELLED；PENDING/READY 直接标 CANCELLED；整个 execution 标 cancelled |
| 调度内联级联 | 某节点执行失败 | `on_node_complete` 里 `mark_failed` + `propagate_failure`，下游 PENDING/READY 标 CANCELLED（与取消同一套图操作，见 [dag.md](./dag.md) §3） |
| `PartialRetrier` | 用户对 failed/cancelled 的 DAG 发起重试 | 只把 FAILED 节点（或指定 `from_node_ids`）**及其下游 CANCELLED/FAILED 节点**重置回 PENDING，已 COMPLETED 的节点原样保留，execution 翻回 pending 后重新跑调度 |

**部分重试的核心价值**：DAG 跑到一半挂了，已完成的昂贵节点（已花掉的 token / 已产出的结果）不必重做——`prepare_retry` 用 BFS（`_collect_downstream`）只收集「失败节点 + 受其牵连的下游」，COMPLETED 节点的 `_node_results` 在重跑时仍可作上游 context 复用。

**与 checkpoint 的关系**：`Checkpoint`（per-node：`iteration / messages / usage`）经 `CheckpointManager` 存进节点的 `checkpoint_json` 列，是「**节点内部**断点续跑」的预留——区别于 PartialRetrier 的「**节点粒度**整节点重跑」。当前调度主路径按节点粒度重跑（重置即从头），checkpoint 为「恢复到节点执行中途某轮」预留，DAG 调度本身不强制读取它。另有 `OuterLoopCheckpoint`（per-edict，`KIND="outer_loop"`）服务 orchestrator 外循环，与 DAG 节点 checkpoint 区分。

## 5. 流程：3 节点 DAG 失败 → 级联取消 → 部分重试

设 DAG：`A → B → C`（B 依赖 A，C 依赖 B），全局/会话泳道有额度。

```text
初次执行
  A: SUBMITTED → RUNNING → COMPLETED   (_node_results[A] 落库)
  B: SUBMITTED → RUNNING → FAILED      (Agent 抛错 / result.status=FAILED)
       on_node_complete(B, error):
         dag.mark_failed(B)
         propagate_failure(B) -> [C]    # C 还是 PENDING，被级联取消
         C: PENDING → CANCELLED
  A 保留 COMPLETED；execution.has_failures() -> status=failed
  发 execution.failed

用户对该 DAG 发起 retry_dag（可不指定节点）
  PartialRetrier.prepare_retry:
    target = [B]                        # 所有 FAILED 节点
    _collect_downstream(B) -> {C}       # C 是 CANCELLED 下游，一并收
    reset = {B, C} -> PENDING (清 error/started_at/completed_at)
    A 不在 reset 集合，原样 COMPLETED
    execution -> pending
  重新 DAGScheduler.run:
    A 已 COMPLETED 不再调度，其结果仍在 _node_results 供 B 取作上游 context
    B: PENDING → RUNNING → COMPLETED
    C: PENDING → RUNNING → COMPLETED
  execution -> completed，root Memorial 聚合
```

要点：A 的算力不浪费，失败只波及 B 及其下游 C；级联取消（失败时）与部分重试（恢复时）共用 `DAG.propagate_failure` / `depends_on` 同一张图，语义对称。
