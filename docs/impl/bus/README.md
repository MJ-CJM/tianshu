# EventBus 实现现状

**相关设计**：[../../design/bus/README.md](../../design/bus/README.md)

> 代码位于 `src/tianshu/bus/`，仅两个文件。本篇讲「代码在哪 / 怎么跑 / 怎么扩展」，emit/fire/priority/隔离的设计意图见 design 篇。

## 1. 模块清单（`src/tianshu/bus/`）

| 文件 | 关键符号 | 职责 |
|---|---|---|
| `event_bus.py` | `EventBus` | 总线本体：`emit` / `fire` / `on` / `off` / `_persist` / `_run_handlers` |
| `event_bus.py` | `_HandlerEntry`（`__slots__`：`handler` / `priority`） | 一条订阅记录，承载 handler + priority |
| `event_bus.py` | `EventHandler`（类型别名） | `Callable[[EventEnvelope], Coroutine[Any, Any, None]]`，即「吃一个事件的 async 函数」 |
| `__init__.py` | 导出 `EventBus` | 包入口 |

事件契约 `EventEnvelope` / 工厂 `make_event` 不在本包，在 `models/events.py`（见 [../../design/storage/events.md](../../design/storage/events.md)）。

## 2. 内部状态

`EventBus.__init__(storage=None)` 持有三样东西：

| 字段 | 类型 | 作用 |
|---|---|---|
| `_handlers` | `defaultdict(list)`：`event_type → list[_HandlerEntry]` | 订阅表，每类事件一条有序 handler 列表 |
| `_storage` | `Storage \| None` | 持久化后端，注入则 `_persist` 写 events 表 |
| `_background_tasks` | `set[asyncio.Task]` | 持有 `fire` 起的后台任务引用，防 GC（详见 design §5） |

## 3. 公开 API

| 方法 | 签名 | 语义 |
|---|---|---|
| `emit` | `async (event) -> None` | 持久化 → 顺序 await 各 handler（保序，调用方等） |
| `fire` | `(event) -> None` | 持久化 → `create_task` 后台扇出（不阻塞调用方） |
| `on` | `(event_type, handler, priority=100) -> None` | 注册，append 后 `sort(key=priority)`，数字小者先跑 |
| `off` | `(event_type, handler) -> None` | 注销，按 `handler is not handler` 过滤重建列表 |

`emit` 和 `_run_handlers`（fire 的后台体）共用同一套 handler 循环：取 `_handlers[event_type]`，逐个 `try/except Exception + logger.exception`，失败不中断兄弟 handler。

## 4. 装配（`app.py` lifespan）

总线在 lifespan 早期构造，注入 storage 以打开持久化：

```text
event_bus = EventBus(storage=storage)        # app.py
... 各子系统构造完成后，集中注册订阅 ...
event_bus.on("edict.submitted",     scheduler.handle_submitted)            # 默认 100
event_bus.on("edict.scheduled",     planner.handle_scheduled,    priority=50)
event_bus.on("plan.completed",      executor.handle_plan_completed)
event_bus.on("execution.completed", auditor.handle_execution_completed)    # 100
event_bus.on("execution.completed", cost_manager.handle_...,     priority=150)
event_bus.on("execution.completed", memory_manager.handle_...,   priority=200)
event_bus.on("execution.completed", _update_universe_fitness,    priority=250)
event_bus.on("audit.completed",     ...)  /  on("execution.failed", ...)  / ...
```

订阅集中在 `app.py` 一处注册（约 `app.py:533` 起），便于一眼看全「谁消费谁、谁先谁后」。`outer_loop.*` 一类事件用循环批量注册 notifier handler。

`event_bus` 随后被各生产者持有：gateway / scheduler / planner / executor / auditor / notifier / cost / memory / universe 通过构造注入或 `app.state` 拿到引用，调 `emit`/`fire` 发事件。

## 5. 生产者怎么发事件

发事件统一用 `make_event(event_type, edict_id=..., memorial_id=..., payload=...)` 造 envelope，再 `emit`/`fire`：

- **emit（保序、需 await）**：scheduler/planner/executor 主链路在 handler 里 `await bus.emit(make_event("plan.completed", ...))`，让下游接力按序跑完。
- **fire（非阻塞）**：典型站点 —— `gateway/edicts_api.py`（提交任务后立即 202）、`tools/submit_edict.py`、`executor/policy_hook.py`（审批事件）、`universe/evolver.py` 与 `manager.py`、`skills/reviewer.py`、`skills/curator.py`、`persona/profile_synthesizer.py`、`gateway/core/edict_bridge.py`。

## 6. 持久化路径

`_persist(event)` 在扇出前调用，仅当 `_storage` 存在且 `event.edict_id` 非空时写库：调 `storage.append_event(edict_id, memorial_id, event_type, payload)` → INSERT 进 `events` 表（列：`id`(ULID)/`edict_id`/`memorial_id`/`event_type`/`payload_json`/`created_at`）。回读经 `storage.get_events(edict_id)`，按 `created_at ASC` 还原时间线。建表与 storage 细节见 [../storage/](../storage/)。

## 7. 扩展点

| 想做 | 怎么扩 |
|---|---|
| 给某事件加一个新消费者 | 在 `app.py` 加一行 `event_bus.on(event_type, handler, priority=N)`，无需改生产者 |
| 控制新消费者的执行时机 | 选 priority：事前拦截用小数字（5/10），记账用 150，学习用 200，纯旁路聚合用 250+ |
| 发一类新事件 | 生产者侧 `make_event(...)` + `emit`/`fire`；要保序选 emit，要非阻塞选 fire |
| 让事件可审计入库 | 确保 envelope 带 `edict_id`（否则 `_persist` 跳过，不落 events 表） |
| 临时摘除消费者 | `event_bus.off(event_type, handler)`（按对象身份匹配，需传入同一函数引用） |
| 单测总线行为 | `EventBus(storage=None)` 构造无持久化实例，`on` 注册 fake handler，`await emit(...)` 断言扇出顺序与隔离 |
