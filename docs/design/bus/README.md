# bus · EventBus 进程内事件总线

EventBus 是天枢模块间的**稳定解耦协议**：生产者只管发事件，消费者只管订阅，谁也不直接 import 谁。它不是日志通道，而是工作流编排的总线 —— 调度、规划、执行、审计、记账、学习、通知全靠它串起来。

**相关实现**：[../../impl/bus/README.md](../../impl/bus/README.md)
**事件清单与 EventEnvelope 契约**：[../storage/events.md](../storage/events.md)

## 1. 为什么是「总线」而不是直接调用

`EventBus`（`bus/event_bus.py`）只有一张表：`event_type → 有序 handler 列表`。生产者调 `emit`/`fire`，总线按类型扇出。这样设计的收益：

- **新增能力优先订阅，不改生产者**。例如「位面适应度闭环」只是给 `execution.completed` 多挂一个 priority=250 的 handler，executor 一行都不动。
- **生产者无需知道下游存在**。`execution.completed` 同时被 auditor、cost、memory、universe 消费，executor 只发一次事件。
- **时序契约声明化**。多消费者的先后顺序由 priority 数字裁定，消费者之间互不感知。

总线是**纯 asyncio、单进程内**的：没有跨进程消息队列，handler 都是 `async def`，在同一事件循环里跑。这是刻意的简化 —— 天枢是单实例后端，不需要分布式 MQ 的复杂度。

## 2. emit vs fire：两种发射语义

两种模式的区别只有一个问题：**调用方要不要等 handler 跑完**。

| 模式 | 签名 | 行为 | 用在哪 |
|---|---|---|---|
| `emit(event)` | `async`，调用方 `await` | 先持久化，再**顺序 await** 每个 handler（按 priority） | 事件链内需要保序：`edict.scheduled → plan.completed → execution.*`，handler 间有时序依赖 |
| `fire(event)` | 同步，立即返回 | 先持久化，再 `asyncio.create_task` 把扇出丢到**后台** | 调用方不想阻塞：HTTP 提交任务、handler 内部触发下游事件 |

### 为什么 emit 不给 handler 设超时

`emit` 的 handler 循环里**没有 per-handler timeout**。原因是 emit 链会嵌套 —— 一个 handler 里可能再 `await emit(...)`，而 planner/executor 这类 handler 会跑长时间 LLM 调用。如果在每层套 `wait_for` 超时，外层超时会**取消正在进行的 LLM 请求**，把好端端的长任务掐断。所以 emit 的纪律是：**保序但不限时**，靠任务级预算（cost/budget）而非总线层超时来约束时长。

### 为什么需要 fire

两类场景必须用 fire：

1. **HTTP 不能阻塞**。`POST /edicts`（`gateway/edicts_api.py`）`fire(edict.submitted)` 后立即返回 202，整条 scheduler→planner→executor 链路在后台跑，请求不会挂在那里等任务完成。
2. **handler 内触发下游、不想自我阻塞**。如 `policy_hook.py`、`universe/evolver.py`、`skills/reviewer.py` 在自己的 handler 里 `fire(...)` 发后续事件 —— 用 emit 会让当前 handler 等下游全跑完才返回，fire 则把下游解耦到独立后台任务。

## 3. 优先级排序

订阅时声明优先级：`on(event_type, handler, priority=100)`，**数字小者先执行**，同级按注册顺序（`entries.sort` 是稳定排序）。

典型分层（以 `app.py` 注册为准）：

| priority | 消费者 | 语义 |
|---|---|---|
| 5 / 10 | policy / approval | 事前拦截，必须最先 |
| 50 | planner | 规划在执行前 |
| 100（默认） | scheduler / executor / auditor / notifier | 主链路 |
| 150 | cost manager | 业务完成后**记账** |
| 200 | memory / skill | 最后做**学习持久化** |
| 250 | universe fitness | 适应度闭环，挂在最末尾聚合 |

设计立场：**优先级是声明式的时序契约**。同一事件多消费者时，数字直接表达「谁先记账、谁后学习」，无需任何消费者知道其它消费者的存在，也无需在生产者里硬编码调用顺序。

## 4. 单个订阅者异常隔离

emit 和后台扇出（`_run_handlers`）都把每个 handler 调用包在 `try/except Exception` 里，失败时 `logger.exception` 记栈，然后**继续下一个 handler**：

```text
for entry in entries(按 priority 排好):
    try:
        await entry.handler(event)
    except Exception:
        logger.exception("Handler %s failed for event %s", ...)
        # 不 re-raise —— 兄弟 handler 照常执行
```

收益：一个消费者抛异常（比如 notifier 飞书 API 挂了）**不会连累**同一事件的其它消费者（auditor/cost/memory 照跑）。代价：异常被吞进日志，调用方拿不到失败信号 —— 这是总线刻意的取舍，事件消费本就该是「尽力而为」的旁路，关键路径的失败应由 handler 自己上报事件而非靠异常冒泡。

## 5. 后台任务生命周期

`fire` 用 `asyncio.create_task` 起后台任务，必须解决一个 asyncio 陷阱：**create_task 返回的 Task 只被弱引用持有，可能在跑完前被 GC**。EventBus 的做法：

```text
task = asyncio.create_task(self._run_handlers(event))
self._background_tasks.add(task)              # 强引用，防 GC
task.add_done_callback(self._background_tasks.discard)  # 跑完自动摘除
```

`_background_tasks` 是一个 `set`：fire 时 add，完成时回调 discard。这样后台任务在执行期间始终被强引用、不会被回收，结束后又自动从 set 移除、不泄漏。这是一个无生命周期管理负担的「自清理」集合 —— 总线本身不需要 start/stop。

## 6. 事件持久化到 events 表的时机

emit 和 fire 都在扇出**之前**先调 `_persist(event)`，所以**事件先落库、再通知消费者**。落库条件是 `_persist` 里的两个判断：

```text
if self._storage and event.edict_id:
    storage.append_event(edict_id, memorial_id, event_type, payload)
```

- **必须有 storage**（构造时注入，`EventBus(storage=storage)`）。
- **必须有 `edict_id`** —— 无 edict 关联的系统事件（如 global scope 预算事件）**不入库**，因为 events 表是按任务（edict）组织的可审计时间线，没有归属的事件无处安放。

落库走 `storage.append_event`，向 `events` 表 INSERT 一行（`id`(ULID) / `edict_id` / `memorial_id` / `event_type` / `payload_json` / `created_at`）。回读用 `get_events(edict_id)` 按 `created_at ASC` 还原任务的事件时间线，供审计与 UI 展示。

设计含义：**持久化是同步的、发生在扇出前**。即使所有 handler 都失败，事件本身也已经在库里 —— 时间线不会因为消费侧出错而丢记录。
