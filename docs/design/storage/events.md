# storage · 事件广播与持久执行协议

> EventBus 是观察和模块广播协议，不是根执行的唯一权威。新增持久业务动作优先使用
> UnitOfWork + outbox/RunState/attempt，再用事件做投影和通知；不能只发一个 `fire()` 就宣称
> 动作可跨重启恢复。

## 1. EventEnvelope 契约

事件统一用 `EventEnvelope` 承载（`models/events.py`）：

| 字段 | 作用 |
|---|---|
| `event_id` | ULID，自动生成 |
| `event_type` | 事件名（如 `edict.scheduled`） |
| `edict_id` / `memorial_id` | 关联任务与奏折（可空） |
| `attempt` | 重试轮次，默认 1 |
| `timestamp` | UTC 时间，自动填 |
| `producer` | 生产者标识，默认 `system` |
| `payload` | 任意 dict 数据 |

工厂 `make_event(event_type, edict_id, memorial_id, producer, payload, attempt)` 自动补 `event_id` + `timestamp`。

## 2. dispatch、emit 与 fire

EventBus 自身不持久化。三种派发模式按是否由 outbox 恢复、是否阻塞调用方区分：

| 模式 | 行为 | 用于 |
|---|---|---|
| `dispatch(event)` | 顺序调用 named consumer，返回每个消费者的结果 | outbox 可恢复派发 |
| `emit(event)` | **顺序 await** named + local handler（按 priority） | 进程内需要保序的兼容/广播 |
| `fire(event)` | `asyncio.create_task` **后台调度** named + local handler | 不阻塞调用方的尽力通知 |

`emit` / `fire` 不提供重投保证；`fire` 用 `_background_tasks` set 持有 task 引用防
GC。关键业务动作必须先在领域事务中写 outbox。

## 3. 优先级与异常隔离

| 机制 | 实现 |
|---|---|
| 优先级 | `on(event_type, handler, consumer_name=..., priority=100)`，**数字小者先执行**，同级按注册顺序 |
| 异常隔离 | 每个 handler 包 `try/except` + `logger.exception`，**失败不连累兄弟 handler** |

典型 priority 分层：policy/approval hooks（5/10）< 默认业务消费者（100）< cost
（150）< memory/skill（200）。managed planning/execution 的先后关系由 dispatcher-owned
runner 和持久 attempt 决定，不依赖 EventBus priority。

设计立场：**优先级是声明式时序契约**。同一事件多消费者时，数字决定「谁先记账、谁后学习」，无需消费者互相知道对方存在。

## 4. 事件持久化与消费进度

领域应用服务在同一 UnitOfWork 中提交业务行和 `outbox_events`。`OutboxDispatcher`
claim 后调用 `dispatch`，逐个记录 named consumer 成功结果；失败会退避重试，并跳过
已经成功的消费者。通配 `EventHistoryConsumer`（priority=0）再把事件投影到 `events`
任务时间线。无 Edict 归属的系统审计走各自的系统审计表，不依赖 EventBus 自动落库。

## 5. 当前主链路

> 以 `app.py` 订阅注册与各生产者源码为准。完整订阅表见 [../runtime-flow.md](../runtime-flow.md) §2。

| event_type | 生产者 | 主要消费者 |
|---|---|---|
| `edict.submitted` | `EdictApplicationService` 事务 outbox | Scheduler |
| `edict.scheduled` | Scheduler 兼容入口 | `ManagedRunIngress.adopt_legacy` |
| `review.timeout` | `scheduler.py`（needs_review 超时巡检） | Notifier |
| `plan.pending_review` | `planner.py`（`plan_review=true`） | 前端/人工审批 |
| `plan.completed` | Planner 兼容入口 | `ManagedRunIngress.adopt_legacy` |
| `plan.approved` | `api.py`（审批落库记录） | — |
| `execution.started` | executor | 事件时间线 / UI |
| `execution.completed` | fenced terminal outbox | Auditor / CostManager / MemoryManager |
| `execution.failed` | fenced completion | Auditor / Notifier / CostManager |
| `execution.cancelled` | fenced cancellation | CostManager |
| `audit.completed` | auditor | Notifier / MemoryManager |
| `cost.budget_exceeded` | `cost/manager.py` | Notifier |
| `outer_loop.*` | orchestrator | Notifier / WebSocket / 审计时间线 |
| `decision.resolved` / `decision.expired` | `DecisionService` 事务 outbox | continuation / plan review / workspace 投影 |
| `decree.approved` / `decree.rejected` | ApprovalManager 兼容投影 | 旧接口消费者 |

新建根执行的权威串联：

```
API/Scheduler/Follow-up
  → UnitOfWork: Memorial + outbox + attempt
  → commit
  → Reconciler → RunDispatcher lease/heartbeat/fencing
  → managed Planner → Executor
  → fenced terminal
  → execution.* broadcast → audit/evidence/cost/memory/notification
```

旧 `edict.scheduled/plan.completed/edict.resume` 事件仍可 adoption，保证升级期兼容，但不应成为
新功能绕过 durable ingress 的捷径。

**相关实现**：[../../impl/storage/](../../impl/storage/)
