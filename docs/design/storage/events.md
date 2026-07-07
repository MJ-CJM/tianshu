# storage · 事件总线与协议

> EventBus 是模块间的稳定协议，不是日志。新增能力优先订阅事件，减少直接互调。

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

## 2. emit vs fire

两种发射模式，按是否需要保序与是否阻塞调用方区分：

| 模式 | 行为 | 用于 |
|---|---|---|
| `emit(event)` | 先持久化，再**顺序 await** 每个 handler（按 priority） | 事件链内保序（`scheduled→plan.completed→执行`），handler 间有时序依赖 |
| `fire(event)` | 先持久化，再 `asyncio.create_task` **后台调度** handler | HTTP 请求不想阻塞（API 提交任务用 `fire`，立即返回 202） |

`emit` 的 handler **不设 per-handler 超时** —— 避免在嵌套 emit 链里取消长跑 LLM 调用。`fire` 用 `_background_tasks` set 持有 task 引用防 GC。

## 3. 优先级与异常隔离

| 机制 | 实现 |
|---|---|
| 优先级 | `on(event_type, handler, priority=100)`，**数字小者先执行**，同级按注册顺序 |
| 异常隔离 | 每个 handler 包 `try/except` + `logger.exception`，**失败不连累兄弟 handler** |

典型 priority 分层：policy/approval（5/10）< planner（50）< 默认（100，scheduler/executor/auditor/notifier）< cost（150，业务完成后记账）< memory/skill（200，最后学习持久化）。

设计立场：**优先级是声明式时序契约**。同一事件多消费者时，数字决定「谁先记账、谁后学习」，无需消费者互相知道对方存在。

## 4. 事件持久化

`_persist(event)` 仅在 `event.edict_id` 非空时写 `events` 表（`storage.append_event`）。无 edict 关联的系统事件（如 global scope 的预算事件）不入库。落库的事件构成任务的可审计时间线。

## 5. 主链路事件清单

> 以 `app.py` 订阅注册与各生产者源码为准。完整订阅表见 [../runtime-flow.md](../runtime-flow.md) §2。

| event_type | 生产者 | 主要消费者 |
|---|---|---|
| `edict.submitted` | gateway `api.py` / 飞书 bridge（`fire`） | Scheduler |
| `edict.scheduled` | `scheduler.py` | Planner |
| `review.timeout` | `scheduler.py`（needs_review 超时巡检） | Notifier |
| `plan.pending_review` | `planner.py`（`plan_review=true`） | 前端/人工审批 |
| `plan.completed` | `planner.py` / `api.py` 审批通过路径 | Executor |
| `plan.approved` | `api.py`（审批落库记录） | — |
| `execution.started` | executor | 事件时间线 / UI |
| `execution.completed` | executor | Auditor / CostManager / MemoryManager |
| `execution.failed` | executor | Notifier / CostManager |
| `audit.completed` | auditor | Notifier / MemoryManager |
| `cost.budget_exceeded` | `cost/manager.py` | Notifier |
| `outer_loop.*` | orchestrator | Notifier / WebSocket / 审计时间线 |
| `decree.approved` / `decree.rejected` | approvals | Executor（resume/abandon） |

主流程串联：

```
POST /edicts → edict.submitted → Scheduler
  → (immediate) edict.scheduled → Planner
      → plan.completed → Executor
      └→ plan.pending_review（plan_review）→ 审批 → plan.completed
  → execution.completed → Auditor(audit.completed) / CostManager(150) / MemoryManager(200)
  → audit.completed → Notifier / MemoryManager
```

**相关实现**：[../../impl/storage/](../../impl/storage/)
