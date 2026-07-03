# 可观测性 — trace、时间线与回放（operator / debugger 视角）

> 天枢的「黑盒」就是 `events` 表：每道 edict 的全生命周期都被 EventBus 落成一条有序事件流。本篇讲怎么按 `edict_id` 把一次 run 拼回来、读懂三类典型 trace、导出时间线。

**相关设计**：[../design/storage/events.md](../design/storage/events.md)（EventEnvelope / emit vs fire / 优先级与持久化协议）

## 1. 事件分类法

落库事件都来自 `EventBus.emit` / `fire`，由 `_persist` 在 `event.edict_id` 非空时写 `events` 表（`bus/event_bus.py`）。按生产者与语义分四大类：

| 类 | 前缀 | 关键事件（真实值） | 生产者 |
|---|---|---|---|
| **执行链** | `execution.*` | `execution.started` / `execution.completed` / `execution.failed` / `execution.cancelled` | executor（`executor/executor.py`） |
| **审计 / 外环** | `audit.*` `outer_loop.*` | `audit.completed`；`outer_loop.started` / `iteration.started` / `iteration.finished` / `escalated` / `paused` / `resumed` / `completed` / `exhausted` / `approval.requested` / `approval.received` | auditor、orchestrator（`emit_audit`，`executor/orchestrator/`） |
| **调度 / 计划** | `edict.*` `plan.*` `review.*` `decree.*` | `edict.submitted` / `edict.scheduled`；`plan.completed` / `plan.pending_review` / `plan.approved` / `plan.rejected`；`review.timeout`；`decree.approved` / `decree.rejected` | gateway、scheduler、planner、approvals |
| **治理 / 工具** | `policy.*` `tool.*` `cost.*` | `policy.decision` / `policy.session_rule_matched`；`tool.approval_required`；`cost.budget_exceeded` | PolicyHook、ApprovalManager、CostManager |

**不入 `events` 表的两类**（运维易踩坑）：

- `stream.delta` / `stream.tool_start` / `stream.tool_end` —— 这些是 **WebSocket 推送消息类型**（`notifier/notifier.py` 的 `WebSocketStreamCallback`），是 live 流，不持久化，run 结束即消失。
- 无 `edict_id` 的系统事件（如 global scope 预算事件）—— `_persist` 直接跳过。

> `emit_audit`（`executor/orchestrator/persistence.py`）会同时 `storage.append_event` + `bus.emit`，所以 `outer_loop.*` 既进 memorial 事件流也走总线广播。

## 2. 单次 run 的 trace 结构 + edict_id 作 correlation

一次 run 没有独立的 trace_id —— **`edict_id` 就是 correlation key**。`events` 表 schema（`storage/schema.py` 建表）：

| 列 | 含义 |
|---|---|
| `id` | ULID，事件主键（**单调递增，可当 tiebreaker 排序**） |
| `edict_id` | 关联诏令；`ON DELETE CASCADE`（删 edict 连带清事件） |
| `memorial_id` | 关联奏折（一次执行实例），可空 |
| `event_type` | 事件名（见 §1） |
| `payload_json` | 事件载荷 JSON 文本（`json.dumps(..., default=str)`） |
| `created_at` | UTC ISO8601 字符串 |

排序基准：`get_events` 用 `ORDER BY created_at ASC`（`storage/event_repo.py`）。同毫秒并发时 `created_at` 可能并列，必要时叠加 `id ASC` 做稳定 tiebreaker。索引 `idx_events_edict_id` 保证按 `edict_id` 查询走索引。

一次完整 run 的 trace 层级：

```
edict_id (correlation)
└─ memorial_id (一次执行实例；重试/重跑会产生多个)
   └─ events[]  按 created_at ASC：edict.* → plan.* → execution.* → outer_loop.* → audit.*
```

## 3. 追踪一道 edict 全生命周期（真实 SQL）

直接查 `events` 表（列名以 §2 为准；注意是 `created_at` / `payload_json`，不是 `timestamp` / `payload`）。

```sql
-- 3.1 一道 edict 的完整有序时间线
SELECT created_at, event_type, memorial_id, payload_json
FROM events
WHERE edict_id = ?
ORDER BY created_at ASC, id ASC;

-- 3.2 该 edict 各事件类型计数（快速判断卡在哪一阶段）
SELECT event_type, COUNT(*) AS n
FROM events
WHERE edict_id = ?
GROUP BY event_type
ORDER BY n DESC;

-- 3.3 只看外环升级 / 人工决策（排障升级路径）
SELECT created_at, event_type, payload_json
FROM events
WHERE edict_id = ?
  AND (event_type LIKE 'outer_loop.%' OR event_type LIKE 'decree.%')
ORDER BY created_at ASC;

-- 3.4 治理决策审计（policy/approval；对照 list_policy_events 路由口径）
SELECT created_at, event_type, payload_json
FROM events
WHERE edict_id = ?
  AND (event_type LIKE 'policy.%'
       OR event_type LIKE 'hook.%'
       OR event_type = 'tool.approval_required'
       OR event_type LIKE 'decree.%')
ORDER BY created_at ASC;

-- 3.5 跨 edict 找失败的 run（运营巡检）
SELECT edict_id, created_at, payload_json
FROM events
WHERE event_type = 'execution.failed'
ORDER BY created_at DESC
LIMIT 50;
```

DB 默认位于 `~/.tianshu/`；用 `sqlite3` 直连即可。`payload_json` 是文本，用 `json_extract(payload_json, '$.status')` 取字段，例如 `execution.completed` 的 `payload.status`、`execution.failed` 的 `payload.error`。

## 4. 三类 worked trace（事件序列）

按 `created_at ASC` 读到的典型序列。`execution.*` 的 producer 是 `executor`，`outer_loop.*` 是 `orchestrator`。

### 4.1 成功 run

```
edict.submitted        (gateway, fire → 202 立即返回)
edict.scheduled        (scheduler)
plan.completed         (planner；若免审批直接进执行)
execution.started      payload.memorial_id
outer_loop.started     payload.max_outer
outer_loop.iteration.started / .finished   (可能多轮)
outer_loop.completed
audit.completed
execution.completed    payload.status="completed"
```

### 4.2 失败 run

```
edict.submitted → edict.scheduled → plan.completed
execution.started
outer_loop.started → iteration.started / .finished ...
outer_loop.exhausted          (max_outer 耗尽，未达验收)
execution.failed              payload.status="failed", payload.error="..."
```

排障入口：先 `execution.failed` 的 `payload.error`；若 `error` 为 `orchestrator error: ...` 说明外环抛异常（`executor.py` finally 兜底翻 failed）。

### 4.3 升级 run（外环 escalate 到人工）

```
execution.started → outer_loop.started → iteration.started/.finished
outer_loop.escalated            payload.to="L2"/"L3", payload.iteration
outer_loop.approval.requested   (L3 触发人工，payload 带请求上下文)
   ── 等待人工 ──
decree.approved | decree.rejected
outer_loop.approval.received    payload.action
outer_loop.resumed | outer_loop.completed
execution.completed | execution.failed
```

L2 会插入一次 consultation（咨询），失败则补一条 `outer_loop.escalated` 直接升 L3（`orchestrator/loop.py`）。卡在 `approval.requested` 之后无后续，即「等人工未决」。

## 5. 导出 / 检查时间线

### 5.1 HTTP 路由（`edicts_api.py` / `system_api.py` / `audit_api.py`）

| 路由 | 用途 |
|---|---|
| `GET /api/edicts/{edict_id}/events` | 单道 edict 全量事件（直接落库口径，`get_events`） |
| `GET /api/edicts/{edict_id}/policy_events` | 仅 `policy.*` / `hook.*` / `tool.approval_required` / `decree.*`（治理审计视图） |
| `GET /api/event-bus/recent?limit=` | 跨 edict 最近事件（默认 50，上限 200，`get_recent_events` 倒序） |
| `GET /api/event-bus/stats` | 全库事件类型分布计数（`get_event_stats`） |
| `GET /api/event-bus/handlers` | 当前注册的 handler + priority（核对订阅是否就位） |
| `GET /api/policy/stats` | 当日 allow/deny/require_approval/approved/rejected 聚合 |

### 5.2 实时 timeline（WebSocket）

`GET /api/ws`（`websocket_endpoint`）→ `Notifier.register_ws`，推送 `stream.*`（增量 token / 工具起止）与 `audit.completed` 状态卡片。urgent 优先级 edict 跳过 0.5s debounce 立即广播（`notifier.py`）。

> live 流只用于「正在跑」的观察；run 结束后回放一律走 §3/§5.1 的 `events` 表，那才是单一可信源。

### 5.3 命令行导出

```bash
# 导出某 edict 的时间线为 CSV（DB 默认 ~/.tianshu/tianshu.db）
sqlite3 -header -csv ~/.tianshu/tianshu.db \
  "SELECT created_at, event_type, memorial_id, payload_json
   FROM events WHERE edict_id='<EDICT_ID>' ORDER BY created_at ASC, id ASC;" \
  > timeline.csv

# 全库事件类型分布
sqlite3 ~/.tianshu/tianshu.db \
  "SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY 2 DESC;"
```
