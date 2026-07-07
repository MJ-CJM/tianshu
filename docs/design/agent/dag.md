# DAG 执行

## 1. 设计意图

当 planner 产出多任务 plan，任务之间往往有依赖（A 的产出喂给 B）。天枢把多任务 plan 转成 DAG，按拓扑顺序并发调度，每个节点可独立指派 persona、独立建 Memorial、独立 checkpoint。设计目标：**最大化无依赖任务的并发，同时让失败沿依赖边可控传播**，并通过双层泳道防止单个会话或单个官员独占系统资源。

## 2. 图模型

| 对象 | 关键字段 | 说明 |
|---|---|---|
| `DAGExecution` | `id`、`edict_id`、`status`、`root_memorial_id`、`max_concurrency`、`nodes` | 一次 DAG 执行 |
| `DAGNode` | `node_id`、`depends_on`、`status`、`assigned_official`、`memorial_id`、`checkpoint_json` | 单个节点 |
| `DAGNodeStatus` | pending / ready / running / completed / failed / cancelled | 节点生命周期 |

`depends_on` 是节点的前驱依赖列表；`assigned_official` 指定执行 persona（缺失时回退 `bingbu`）。

## 3. 调度契约

`DAG`（内存图）提供纯图操作：

- **拓扑排序**：Kahn 算法，检测到环抛 `ValueError`，整个 DAG 直接 failed。
- **就绪判定**：节点自身为 PENDING 且所有 `depends_on` 均 COMPLETED → ready。
- **失败传播**：`propagate_failure(node_id)` 把所有下游 PENDING/READY 节点级联标 CANCELLED，返回被取消列表。

调度循环：建图→拓扑校验→发 `dag.started`→初次调度就绪节点→每个节点完成回调里重算就绪节点→全部终态后聚合。

## 4. 节点级隔离

每个就绪节点调度时：
1. 按 `assigned_official` 取 persona，取不到回退 `DEFAULT_EXECUTOR_ID="bingbu"`（warning）。
2. 收集上游已完成节点的产出，作为本节点输入上下文。
3. 经泳道获取并发额度后，由 `Worker.execute_node` 跑单次 Agent，建独立 child Memorial。

## 5. 结果聚合

DAG 完成后回写 root Memorial：
- **usage**：所有节点 token 用量求和。
- **result**：每个节点 `## node_id: description` + 产出拼成全量记录。
- **final_output**：抽取**叶子节点**（不被任何节点依赖）的产出作为「用户关心的最终交付物」，供外发渠道（飞书/邮件）单独呈现；单叶子直接取，多叶子拼接。

终态发 `execution.completed` 或 `execution.failed`（有任一 FAILED 节点即 failed）。

## 6. 并发治理：WorkerPool + LaneManager

两层并发控制职责不同：

| 组件 | 粒度 | 机制 |
|---|---|---|
| `WorkerPool` | 全局执行槽 | `asyncio.Semaphore(max_concurrency)`，`submit(WorkItem)` 返回 Task |
| `LaneManager` | 双层泳道 | `GlobalLane`（系统级背压，默认 8）+ `SessionLane`（按 `edict_id` 隔离，默认 1） |

`SessionLane` 按 **edict_id** 建泳道（一个 Edict 的 DAG 内部并发受限），`GlobalLane` 是系统级背压总闸。节点运行时先 acquire session lane 再 acquire global lane，`finally` 中反序 release——保证一个长 DAG 不会饿死其他 Edict。

## 7. 节点级 Checkpointing

DAG 把「续作单位」下沉到**节点**：每个 `DAGNode` 自带一列 `checkpoint_json`，可独立存一份执行进度快照，重试时只恢复该节点、不重跑全图。`executor/checkpoint.py` 的 `Checkpoint` 是这份快照的最小载体。

**存什么**——`Checkpoint` 只快照三样东西，刻意与单任务 ReAct 的可恢复状态（见 [./react-loop.md](./react-loop.md) 的 `LoopState`）对齐：

| 字段 | 语义 | 与 ReAct 的对应 |
|---|---|---|
| `iteration` | 已完成的轮次 | `LoopState.iteration`，恢复后从此轮续跑 |
| `messages` | 当前消息快照（`list[dict]`） | `LoopState.messages`，重建对话上下文 |
| `usage` | 累计 token（`UsageSummary`） | 跨轮累计用量，恢复后继续累加 |

为何只存这三样：它们恰好是「重建一个 `LoopState` 并续写」所需的全部输入。不存 persona、不存上游产出——persona 由 `assigned_official` 在调度时重新解析、上游产出由 `_node_results` 在内存中现取（见 §4），快照因此小且与可变环境解耦。

**怎么存 / 怎么取**——`CheckpointManager` 是 storage 的薄封装，三个动作都落在 `dag_nodes.checkpoint_json` 这一列上：

| 方法 | 行为 |
|---|---|
| `save(dag_id, node_id, ckpt)` | `ckpt.to_json()` 写入该节点的 `checkpoint_json`（`update_dag_node_checkpoint`） |
| `load(dag_id, node_id)` | 扫该 DAG 的节点，命中且 `checkpoint_json` 非空则 `Checkpoint.from_json` 还原，否则 `None` |
| `clear(dag_id, node_id)` | 置 `checkpoint_json = NULL`（重试成功后清理，避免脏快照误导下次恢复） |

**重试时如何 restore**：`load` 拿回 `Checkpoint` 后，调用方用 `iteration` / `messages` / `usage` 重建 `LoopState`（参见 react-loop 的「调用方拿到的旧 `LoopState` 永远有效」契约），让 Agent 从断点的下一轮继续，而非从第 0 轮重跑——避免重复消耗已付出的 token 与工具副作用。

**当前边界**：checkpoint 的**数据通路已就绪**（列、序列化、`CheckpointManager` CRUD 都在），但 `dag_scheduler.py` 的调度循环目前直接 `Worker.execute_node` 整段跑节点、**未在轮间主动 `save`**。即这是一项**为「节点中途崩溃后断点续跑」预留的机制**，DAG 调度本身不强制使用；区别于 per-edict 的 `OuterLoopCheckpoint`（同文件，`KIND="outer_loop"`，快照外层循环状态，与节点级 `Checkpoint` 不是一回事）。

## 8. 失败恢复与选择性重试

失败的处理分两段、关注点正交：**运行时**沿依赖边把不可能再成功的下游就地取消（`propagate_failure`），**重试时**把失败子图重置回 PENDING 重跑、保留已完成工作（`PartialRetrier`）。

### 8.1 propagate_failure 精确语义

`DAG.propagate_failure(node_id)`（`dag/graph.py`）从失败节点的**后继**出发做 BFS，只动该有的状态：

- **方向**：仅沿 `_reverse_edges`（后继方向）下行，永不回溯上游——上游已完成的产出不受影响。
- **取消条件**：只把状态为 **PENDING / READY** 的下游节点改成 `CANCELLED` 并计入返回列表；**RUNNING 不动**（已在执行的节点不被打断，调度器不主动 kill 在跑的 Task）、COMPLETED / FAILED 也不动。
- **深度**：BFS 走整个**传递闭包**——被取消的节点其后继继续入队（`queue.extend(self._reverse_edges.get(nid, []))`），但只有「真的被本次取消」的节点才继续向下扩散；`visited` 集合去重，菱形依赖不会重复处理。
- **副作用边界**：`propagate_failure` 是**纯内存图操作**，只改 `DAGNode.status`，**不写库、不发事件、不碰 Memorial**。返回被取消的 `node_id` 列表交由调用方落库。

调度侧 `dag_scheduler.py` 的 `on_node_complete` 串起这条链：节点失败 → `dag.mark_failed(node_id, error)` → `cancelled = dag.propagate_failure(node_id)` → 把每个 `cancelled` 节点 `update_dag_node_status(..., CANCELLED)` 落库、把失败节点本身写 `FAILED + error`、发一条 `dag.node.failed` 事件。Memorial 不在此处回写（节点级 Memorial 由 `Worker.execute_node` 负责，root Memorial 在全图终态时聚合，见 §5）。最终只要图中存在任一 `FAILED` 节点，`has_failures()` 为真，整个 execution 收 `failed`。

### 8.2 与 PartialRetrier 协作

`propagate_failure` 让失败「就地止血」，`executor/retry.py` 的 `PartialRetrier.prepare_retry` 负责「带着已完成的工作重跑失败子图」：

- **重试范围**：默认收集所有 `FAILED` 节点为起点；也可传 `from_node_ids` 指定从哪些节点重跑。
- **下游收集**：`_collect_downstream` 递归把起点的下游中状态为 **CANCELLED / FAILED** 的节点一并纳入重置集——正好对应 §8.1 里被级联取消的那批；**COMPLETED 的下游不重置**，已完成的工作得以保留。
- **重置动作**：重置集内每个节点状态回 `PENDING`，清空 `error` / `started_at` / `completed_at` 并落库；execution 状态改回 `pending`、`completed_at` 清空，使其可被重新 `run`。

两者构成「失败 → 级联取消 → 选择性重置 → 重跑」的闭环：取消阶段标记的 `CANCELLED` 恰是重置阶段要复活的对象，已 `COMPLETED` 的节点两个阶段都不碰。配合 §7 的节点级 checkpoint，重跑可进一步细化到「从节点断点续作」而非整节点重来。

## 9. 边界

- 取消与失败重试由 Executor 侧承接：运行时级联取消（`propagate_failure`，§8.1）+ 重试时选择性重置（`PartialRetrier`，§8.2）。
- 节点 checkpoint（`checkpoint_json`）为断点续跑预留（§7），DAG 调度循环本身不强制写快照。

**相关设计**：[./harness.md](./harness.md)（把单节点跑成受控 Agent 执行、并发治理与失败恢复的执行壳）
**相关实现**：[../../impl/agent/](../../impl/agent/)
