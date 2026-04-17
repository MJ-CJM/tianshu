# Storage 与 EventBus

覆盖 `src/tianshu/storage.py`、`src/tianshu/bus/event_bus.py`、`src/tianshu/models/events.py`。

---

## 1. Storage（`storage.py`）— 单一真相源

### 初始化

`Storage(db_path).init_db()`：
- `sqlite3.connect(db_path, check_same_thread=False)` + `threading.Lock`（所有写入 `with self._lock`）
- `PRAGMA journal_mode=WAL` — 读写不互斥
- `PRAGMA foreign_keys=ON` — 启用级联删除
- `_create_tables()` → 创建 17 张主表
- `_migrate()` → 处理 schema 演进（增列）
- `_init_fts()` → 创建 `memory_fts` + 3 触发器（insert/delete/update，见 `memory/fts.py`）

`row_factory = sqlite3.Row` 让所有查询返回 `dict`-like 行，字段名访问。

`_db_path` 默认为 `~/.tianshu/tianshu.db`（`app.py` 注入）。Drawer 独立库是 `~/.tianshu/memory/drawers.sqlite3`（见 `memory.md`）。

### 18 张表一览

主 SQLite 包含 17 张业务表 + 1 张 FTS 虚表（`memory_fts` 及其内部 config/data/docsize/idx 辅表共 4 张）。

**业务核心**

| 表 | 主键 | 外键 / 索引 |
|---|---|---|
| `edicts` | id (TEXT) | — |
| `memorials` | id (TEXT) | FK `edict_id → edicts.id` ON DELETE CASCADE |
| `events` | id (AUTOINCREMENT) | `edict_id`, `memorial_id`（无 FK，保留历史） |
| `decrees` | id (TEXT) | FK `memorial_id → memorials.id` |

**DAG 执行**

| 表 | 主键 | 备注 |
|---|---|---|
| `dag_executions` | id (TEXT) | `edict_id`, `plan_json`, `status`, `root_memorial_id` |
| `dag_nodes` | (dag_execution_id, node_id) | `depends_on_json`, `checkpoint_json`, `memorial_id`, `assigned_official` |

**Memory Palace**

| 表 | 主键 | 备注 |
|---|---|---|
| `memory_entries` | id (TEXT) | `persona_id`, `edict_id`, `category`, `content`, `confidence`, `access_level`, `expires_at` |
| `memory_fts` | (FTS5 虚表) | rowid 关联 `memory_entries`，由触发器自动同步 |

**成本**

| 表 | 主键 | 字段 |
|---|---|---|
| `cost_ledger` | id (AUTOINCREMENT) | `edict_id`, `memorial_id`, `provider_name`, `model`, `prompt/completion/total_tokens`, `cost_cny` |
| `cost_budgets` | (scope, period) | `budget_cny`, `spent_cny`, `reset_at` |

**配置 / 元数据**

| 表 | 主键 | 备注 |
|---|---|---|
| `llm_configs` | name | ConfigManager 多配置存储 |
| `providers` | name | model, api_base, capabilities(JSON), status, priority, rpm/tpm_limit |
| `scheduler_jobs` | id | `edict_id`, `schedule_type`, `cron_expr`, `next_run` |
| `session_rules` | id | Policy 会话级规则（Tier / allow / deny） |
| `departments` | id | 部门元数据 |
| `personas` | id | 人格元数据（PersonaLoader 同步）|
| `plugins` | name | manifest_json, sha256, enabled |
| `skill_metrics` | skill_name | used_count, last_used, avg_latency_ms, success_rate |

### 典型 API

Storage 提供面向领域的方法，调用方无需写 SQL：
- `save_edict(edict)`, `get_edict(id)`, `list_edicts(status, limit)`, `update_edict_status`
- `save_memorial(memorial)`, `get_memorial(id)`, `update_memorial_result`, `update_memorial_timeline`
- `append_event(edict_id, memorial_id, event_type, payload)`, `list_events(edict_id)`
- `save_decree(decree)`, `list_decrees(memorial_id)`
- `save_dag_execution`, `save_dag_node`, `list_dag_nodes(dag_execution_id)`, `update_dag_node_status`
- `save_cost_record`, `update_budget_spent(scope, amount)`, `get_cost_summary`
- `save_persona`, `list_personas`, `delete_persona`
- `search_memory(persona_id, query, category, limit)` — 配合 FTS5

线程安全：所有写操作在 `with self._lock` 内，读操作依赖 WAL 不加锁。

### 级联删除

`edicts` → `memorials`（FK CASCADE）→ 但 `events` / `cost_ledger` / `dag_*` **没有 FK**（历史留存，清理需手动 DELETE，见 session 中的 SQL 清理脚本）。

## 2. EventBus（`bus/event_bus.py`）

纯 asyncio 事件总线，优先级排序 + 异常隔离。

### API

```python
EventBus(storage=None)

async def emit(event: EventEnvelope) -> None    # 持久化 + 顺序执行 handler
def fire(event: EventEnvelope) -> None          # 持久化 + 后台 create_task
def on(event_type, handler, priority=100) -> None
def off(event_type, handler) -> None
```

`emit` vs `fire`：
- `emit` 顺序 await 每个 handler，用于事件链内保序（例如 `edict.submitted → scheduled → plan.completed`）
- `fire` 后台任务非阻塞，handler 需要触发下游事件但不想阻塞自己时用；`_background_tasks` set 持有引用防止 gc

### 优先级

`_HandlerEntry(handler, priority)` 按 priority 升序排列（`entries.sort(key=...)`），**小者先执行**。同 priority 按注册顺序。

典型分层（见 `overview.md` §EventBus 订阅链）：
- 5 / 10：policy hook / approval
- 50：planner
- 100：默认（scheduler / executor / auditor / notifier）
- 150：cost manager（在核心业务完成后记账）
- 200：memory manager / skill reviewer（最后学习与持久化）

### 异常隔离

每个 handler 用 `try / except` 包裹，`logger.exception` 记录，**失败不影响兄弟 handler**。典型场景：memory 写入失败不会阻断 notifier 发送告警。

### 事件持久化

`_persist(event)` 仅在 `event.edict_id` 非空时写 `events` 表（`storage.append_event`）。无 edict 关联的系统事件（如 `cost.budget_exceeded` 的 global scope）不入库。

## 3. EventEnvelope（`models/events.py`）

```python
class EventEnvelope(BaseModel):
    event_id: str                           # ULID
    event_type: str
    edict_id: str | None
    memorial_id: str | None
    attempt: int = 1
    timestamp: datetime
    producer: str = "system"
    payload: dict[str, Any]
```

`make_event(event_type, edict_id, memorial_id, producer, payload, attempt)` 工厂，生产者传入自动填 `event_id` + `timestamp`。

## 4. 事件类型清单（grep 源码得）

| event_type | 产生者 | 典型消费者 |
|---|---|---|
| `edict.submitted` | `gateway/api.py` (POST /edicts) | Scheduler |
| `edict.scheduled` | `scheduler.py` | Planner |
| `review.timeout` | `scheduler.py` | Notifier |
| `plan.pending_review` | `planner.py` | Executor（审批模式） |
| `plan.completed` | `planner.py`, `gateway/api.py`（直发路径） | Executor → DAGScheduler |
| `execution.started` | `executor.py` | Notifier（UI 状态）|
| `execution.completed` | `executor.py` | Auditor / CostManager / MemoryManager |
| `execution.failed` | `executor.py` | Notifier / CostManager |
| `execution.cancelled` | `executor.py` | — |
| `dag.cancelled` | `executor.py` | Notifier |
| `audit.completed` | `auditor.py` | Notifier / MemoryManager |
| `cost.budget_exceeded` | `cost/manager.py` | Notifier |
| `decree.approved` / `decree.rejected` | `approvals.py` | Executor (resume) |

## 5. 主事件流

```text
POST /edicts
  → edict.submitted
    → Scheduler.handle_submitted
      → (immediate) edict.scheduled
      → (cron/at)   入 scheduler_jobs 表，到期再触发
  → edict.scheduled
    → Planner.handle_scheduled
      → plan.completed  (直发 / 内阁决策)
      ├→ plan.pending_review  (edict.plan_review=1)
  → plan.completed
    → Executor.handle_plan_completed
      → (单任务) Worker.execute → Agent loop
      → (多任务) DAGScheduler
        → 拓扑调度 → 就绪节点 → worker_pool.submit
    → execution.started
    → execution.completed / failed
  → execution.completed
    → Auditor.handle_execution_completed
      → audit.completed
    → CostManager.handle_execution_completed  (priority=150)
    → MemoryManager.handle_execution_completed  (priority=200, 当前为 no-op，记忆由 on_agent_end hook 写)
  → audit.completed
    → Notifier.handle_audit_completed
    → MemoryManager.handle_audit_completed  (priority=200, 写 ducha insight)
```

approval 分支：
```text
plan.pending_review / tool approval 等待
  → user POST /approvals/decide
    → decree.approved / rejected
      → Executor resume / abandon
```

## 代码路径索引

- `src/tianshu/storage.py`
- `src/tianshu/bus/event_bus.py`
- `src/tianshu/models/events.py`
- `src/tianshu/memory/fts.py`（memory_fts 虚表）
- `src/tianshu/app.py`（订阅链注册，第 336–346 行）
