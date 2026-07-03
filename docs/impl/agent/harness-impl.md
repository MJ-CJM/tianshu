# Harness 执行壳 · 实现现状

**相关设计**：[../../design/agent/harness.md](../../design/agent/harness.md)

> 代码位于 `src/tianshu/executor/`。本篇讲「代码在哪 / 怎么装配 / 怎么扩展」，设计意图与失败恢复语义见 design 篇。

## 1. 文件 / 类映射

| 主题 | 文件 | 关键符号 |
|---|---|---|
| 单节点执行 | `executor/worker.py` | `Worker.execute_node()` |
| 全局执行槽 | `executor/worker_pool.py` | `WorkerPool`（`submit`/`_run`/`cancel`/`shutdown`/`status`）、`WorkItem`、`WorkerStatus` |
| 双层泳道 | `executor/lanes.py` | `LaneManager`（`get_session_lane`/`global_lane`/`status`）、`SessionLane`、`GlobalLane` |
| 失败级联取消 | `executor/cancel.py` | `CascadeCanceller.cancel()` |
| 部分重试 | `executor/retry.py` | `PartialRetrier.prepare_retry()`、`_collect_downstream()` |
| 节点 checkpoint | `executor/checkpoint.py` | `Checkpoint`、`CheckpointManager`（`save`/`load`/`clear`）、`OuterLoopCheckpoint` |
| ambient 上下文 | `kernel/ambient.py` | `bind_edict`/`bind_persona`、`get_current_edict`/`get_current_persona` |
| 调度编排 | `executor/dag_scheduler.py` | `DAGScheduler.run()`、`_schedule_ready()` |
| 取消/重试入口 | `executor/executor.py` | `Executor.cancel_dag()`、`retry_dag()`、`set_dag_scheduler()`、`set_lane_manager()` |
| 图操作 | `dag/graph.py` | `DAG.propagate_failure`、`mark_failed`/`mark_completed`、`get_ready_nodes`、`is_complete` |

## 2. 装配（`bootstrap/wiring_executor.py`：`wire_worker_lane` + `wire_executor`）

lifespan 装配已从 `app.py` 内联拆到 `bootstrap/wiring_executor.py` 的两个 `wire_xxx()` 函数，对象构造顺序不变：

```text
WorkerPool(max_concurrency=settings.max_global_concurrency)        # 默认 8
LaneManager(max_global_concurrency=settings.max_global_concurrency)
Executor(...) → executor.set_agent / set_persona_loader
DAGScheduler(worker_pool, agent, storage, event_bus,
             persona_loader, prompt_builder)
executor.set_dag_scheduler(dag_scheduler)
executor.set_lane_manager(lane_manager)
```

`DAGScheduler.__init__` 内部 `Worker(agent, storage, prompt_builder)` 自持一个 worker。`session_lane`/`global_lane` 不在构造时绑死，而是 `Executor._run_dag` 每次按 `edict.id` 取 SessionLane（额度 `edict.runtime.max_concurrency`）后写进 `dag_scheduler._session_lane/_global_lane`。

配置默认值（`config.py`）：`max_global_concurrency = 8`。Edict 侧 `edict.runtime.max_concurrency` 默认 1（`models/edict.py`），API 入参钳制 `1–8`（`models/api.py`）。

## 3. 数据流：泳道 acquire 在哪

`DAGScheduler._schedule_ready` 为每个就绪节点构造闭包 `_run_node`（默认参数绑当前 node/upstream/persona/lane，避免循环变量逃逸），泳道 acquire/release 就在这里：

```text
_run_node:
  session_lane.acquire(); global_lane.acquire()
  try:
    Worker.execute_node(edict, node, upstream, persona)
    _node_results[node_id] = result.result
    _node_usage[node_id]   = result.usage
    if result.status == FAILED: raise RuntimeError(...)   # 触发 on_complete 的 error 分支
  finally:
    global_lane.release(); session_lane.release()         # 逆序
```

`WorkItem(id=f"{execution.id}:{node.node_id}", coro_factory=_run_node)` 提交给 `WorkerPool.submit(item, on_complete=on_node_complete)`。`on_node_complete` 在 worker `finally` 触发：成功 `mark_completed`、失败 `mark_failed`+`propagate_failure`，然后重算就绪或置 `completion_event`。

> work_id 约定 `"{execution.id}:{node.node_id}"`：`CascadeCanceller.cancel` 用同样格式调 `worker_pool.cancel(work_id)` 命中正在跑的 Task。

## 4. 取消 / 重试入口（`executor.py`）

- `cancel_dag(dag_id)`：校验 status ∈ {pending, running} → 取 `_dag_scheduler._pool` → `CascadeCanceller(storage, pool).cancel(execution)` → 发 `dag.cancelled`。
- `retry_dag(dag_id, from_node_ids=None)`：校验 status ∈ {failed, cancelled} → `PartialRetrier(storage).prepare_retry(...)` 重置节点 → 重新 `get_dag_execution` 取最新状态 → `asyncio.create_task(dag_scheduler.run(edict, execution))` 重跑。

两者都对应 HTTP 路由（gateway 侧），按 `dag_id` 操作。

## 5. checkpoint 持久化

`CheckpointManager.save/load/clear` 直接读写 `DAGNode.checkpoint_json` 列（`storage.update_dag_node_checkpoint` / `get_dag_nodes`）。`Checkpoint.to_json/from_json` 序列化 `{iteration, messages, usage}`。当前 `Worker.execute_node` 的 `checkpoint_manager` 形参已预留但主路径未写入——节点重跑走 PartialRetrier 的整节点粒度，checkpoint 为「节点内续跑」预留。

## 6. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 调全局并发上限 | 改配置 `max_global_concurrency`（同时作用于 WorkerPool 与 GlobalLane） |
| 调单 Edict 内并发 | 走 `edict.runtime.max_concurrency`（API 1–8），`get_session_lane` 据此建 SessionLane |
| 节点内断点续跑 | 在 `Worker.execute_node` 接入 `checkpoint_manager`：执行前 `load` 恢复 messages/iteration，执行中按轮 `save`；重试时改为从 checkpoint 续而非从头 |
| 自定义重试范围 | `retry_dag(dag_id, from_node_ids=[...])` 指定起点；`PartialRetrier._collect_downstream` 自动带上下游 |
| 观测背压 | `WorkerPool.status()` + `LaneManager.status()`，可挂到运维面板 |
| 上游 context 注入格式 | 改 `Worker.execute_node` 里 `[Upstream node X result]` 拼装逻辑（当前为 system 消息） |

## 7. 已知现状

- `worker.py` 在 `except asyncio.CancelledError` 引用 `asyncio`，而 `import asyncio` 写在 `finally` 内（`as _asyncio`）——依赖该名字在模块他处已可见的隐式约定，建议提到模块顶部显式 import。
- `SessionLane.available` / `GlobalLane.available` 读 `Semaphore._value`（私有属性），仅用于 `status()` 观测，非控制路径。
- checkpoint 主路径未启用（见 §5）；`OuterLoopCheckpoint` 服务 orchestrator 外循环，与 DAG 节点 checkpoint 分属两套。
