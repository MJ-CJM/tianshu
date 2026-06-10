# storage 子系统 · 实现现状

**相关设计**：[../../design/storage/](../../design/storage/)

覆盖 `src/tianshu/storage.py`、`src/tianshu/bus/event_bus.py`、`src/tianshu/models/events.py`、`src/tianshu/memory/fts.py`。

## 1. 代码地图

| 文件 | 关键符号 | 职责 |
|---|---|---|
| `storage.py` | `Storage` | 单库连接 + 全部领域 CRUD（~3000 行，单文件） |
| `bus/event_bus.py` | `EventBus`、`_HandlerEntry` | 异步事件总线 |
| `models/events.py` | `EventEnvelope`、`make_event` | 事件信封 |
| `memory/fts.py` | `create_fts_table`、`escape_fts5_query`、`search_fts` | FTS5 虚表与全文检索 |

## 2. Storage 怎么跑

`init_db()`（`storage.py:55`）：connect（`check_same_thread=False`，`row_factory=Row`）→ `PRAGMA journal_mode=WAL` / `foreign_keys=ON` → `_create_tables()` → `_migrate()` → `_init_fts()`。

并发：`self._lock = threading.Lock()`，写方法统一 `with self._lock, self._conn:`（约 100+ 处），读方法走 `with self._lock:`，依赖 WAL 不阻塞。

领域 API 形态（调用方不写 SQL）：
- 任务：`save_edict / get_edict / list_edicts / update_edict_status`、`save_memorial / get_memorial / update_memorial / list_memorials`
- 事件：`append_event(edict_id, memorial_id, event_type, payload)` / `list_events`
- 批红：`save_decree / list_decrees`
- DAG：`save_dag_execution / save_dag_node / list_dag_nodes / update_dag_node_status`
- 成本：`save_cost_record / update_budget_spent / get_cost_summary / list_cost_records / upsert_budget`
- 配置：`list_llm_configs / save_llm_config / set_active_llm_config`、`save_provider / list_providers / update_provider`
- 调度：`save_scheduler_job / list_active_scheduler_jobs / set_scheduler_job_status / update_scheduler_job_next_run / delete_scheduler_job`
- 记忆：`search_memory`（配合 FTS5）

## 3. 迁移怎么扩展

新增一列：在 `_migrate()`（`storage.py:534`）的 `migrations` 列表末尾追加 `"ALTER TABLE x ADD COLUMN ..."`。循环逐条 execute，`OperationalError` 中 `duplicate column name` / `no such column` 被吞（幂等），其余 raise（`storage.py:694`）。

改主键：参考 `supervision_reports`（`storage.py:594-640`）—— 建 `_xxx_new` + `INSERT OR IGNORE ... SELECT` 回填 + `DROP` 旧表 + `RENAME`。

分批迁移有独立方法：`_migrate_session_tables_add_instance()`（多 bot `instance_id`）、`persona_metrics` 合成锁列循环（`storage.py:703`）、`_seed_departments()` 一次性回填。

## 4. EventBus 怎么跑

`EventBus(storage)`（`bus/event_bus.py`）：`_handlers: dict[event_type, list[_HandlerEntry]]`，`on()` 追加后按 `priority` 升序 `sort`。

- `emit(event)`（`:40`）：`_persist` → 顺序 `await entry.handler(event)`，每个包 try/except。
- `fire(event)`（`:63`）：`_persist` → `asyncio.create_task(self._run_handlers(event))`，task 存入 `_background_tasks` set，`add_done_callback(discard)` 防 GC。
- `_persist`（`:97`）：仅 `event.edict_id` 非空时 `storage.append_event`。

注：events 表 `id` 为 ULID 文本主键（`append_event` 自生成），不是自增整数。

## 5. FTS5 怎么跑

`create_fts_table(conn)`（`memory/fts.py:11`）建 `memory_fts` 虚表 + `memory_fts_insert/delete/update` 三触发器同步 `memory_entries`。`search_fts` 用 `escape_fts5_query` 把查询词逐个加双引号转义后 `MATCH`，支持按 persona_id 过滤；异常时降级返回空（`_init_fts` 设 `_fts_available`）。

## 6. 装配位置

`app.py` `lifespan()` 中：`Storage` 先于 `EventBus(storage)` 初始化，订阅链在装配尾段注册（见 `runtime-flow.md` §2 订阅表）。各模块经构造注入 `storage` / `event_bus`。

## 7. 已知约束

- `storage.py` 单文件偏大（远超 800 行 file-size 软上限），按领域拆分是潜在重构方向。
- `events` / `cost_ledger` / `dag_*` 无 FK，删除 edict 不级联清理，需手动 DELETE。
