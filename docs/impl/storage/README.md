# storage 子系统 · 实现现状

**相关设计**：[../../design/storage/](../../design/storage/)

覆盖 `src/tianshu/storage/`（包）、`src/tianshu/bus/event_bus.py`、`src/tianshu/models/events.py`、`src/tianshu/memory/fts.py`。

## 1. 代码地图

`storage/` 由 `_base`（连接生命周期）+ 15 个领域 Mixin + `facade`（组合根）构成：

| 文件 | 关键符号 | 职责 |
|---|---|---|
| `_base.py` | `_StorageBase` | 连接生命周期：`init_db()`（connect/WAL/建表/迁移/FTS）、`close()`；`_conn`/`_lock` 供 Mixin 共享 |
| `schema.py` | `SCHEMA_SQL_CORE`/`_FEISHU`/`_TELEGRAM`/`_CHANNELS` | 建表 DDL（37 张 `CREATE TABLE`） |
| `migrations.py` | `run_migrations(conn)` | 历史 `ALTER TABLE`/重建表迁移列表（含 `schedule_run` 建表，累计 38+ 张业务表） |
| `mappers.py` | `_row_to_edict` / `_row_to_memorial` / … | row → model/dict 纯函数（从旧 `Storage` staticmethod 抽出） |
| `facade.py` | `Storage` | 组合 `_StorageBase` + 15 个领域 Mixin；仅保留跨域 JOIN 方法（`get_persona_stats`、`list_memorials_by_persona`） |
| `edict_repo.py` `memorial_repo.py` `event_repo.py` `memory_repo.py` `cost_repo.py` `dag_repo.py` `scheduler_repo.py` | `EdictMixin` 等（批 B） | 任务/奏折/事件/记忆/成本/DAG/调度 CRUD |
| `config_repo.py` `persona_repo.py` `universe_repo.py` `credential_repo.py` `orchestrator_repo.py` `channel_repo.py` `feishu_repo.py` `telegram_repo.py` | `ConfigMixin` 等（批 C） | 配置/人格/位面/凭证/编排台账/多实例通道 CRUD |
| `bus/event_bus.py` | `EventBus`、`_HandlerEntry` | 异步事件总线 |
| `models/events.py` | `EventEnvelope`、`make_event` | 事件信封 |
| `memory/fts.py` | `create_fts_table`、`escape_fts5_query`、`search_fts` | FTS5 虚表与全文检索 |

## 2. Storage 怎么跑

`init_db()`（`storage/_base.py`）：connect（`check_same_thread=False`，`row_factory=Row`）→ `PRAGMA journal_mode=WAL` / `foreign_keys=ON` → `_create_tables()`（执行 `schema.py` 四段 `executescript`）→ `_migrate()`（`migrations.run_migrations` + `_migrate_session_tables_add_instance` + `_seed_departments`）→ `_init_fts()`。

并发：`self._lock = threading.Lock()`（`_base.py` 持有，15 个 Mixin 共享），写方法统一 `with self._lock, self._conn:`（约 100+ 处），读方法走 `with self._lock:`，依赖 WAL 不阻塞。

领域 API 形态（调用方不写 SQL，方法按 Mixin 分文件而非集中单类）：
- 任务：`save_edict / get_edict / list_edicts / update_edict_status`（`edict_repo.py`）、`save_memorial / get_memorial / update_memorial / list_memorials`（`memorial_repo.py`）
- 事件：`append_event(edict_id, memorial_id, event_type, payload)` / `list_events`（`event_repo.py`）
- 批红：`save_decree / list_decrees`（`memorial_repo.py`）
- DAG：`save_dag_execution / save_dag_node / list_dag_nodes / update_dag_node_status`（`dag_repo.py`）
- 成本：`save_cost_record / update_budget_spent / get_cost_summary / list_cost_records / upsert_budget`（`cost_repo.py`）
- 配置：`list_llm_configs / save_llm_config / set_active_llm_config`、`save_provider / list_providers / update_provider`（`config_repo.py`）
- 调度：`save_scheduler_job / list_active_scheduler_jobs / set_scheduler_job_status / update_scheduler_job_next_run / delete_scheduler_job`（`scheduler_repo.py`）
- 记忆：`search_memory`（配合 FTS5，`memory_repo.py`）

`_seed_departments`（定义于 `persona_repo.py` 的 `PersonaMixin`）在 `_base.py` 的 `_migrate()` 内被调用——`Storage` 组合后 `self` 才同时具备 `_StorageBase` 与全部 Mixin 方法，这正是 15 个领域文件必须经 `facade.py` 统一组合、任何一个文件都不能单独实例化的原因。

## 3. 迁移怎么扩展

新增一列：在 `migrations.py` 的 `run_migrations()` 的 `migrations` 列表末尾追加 `"ALTER TABLE x ADD COLUMN ..."`。循环逐条 execute，`OperationalError` 中 `duplicate column name` / `no such column` 被吞（幂等），其余 raise。

改主键：参考 `supervision_reports`（`migrations.py:67-111`）—— 建 `_xxx_new` + `INSERT OR IGNORE ... SELECT` 回填 + `DROP` 旧表 + `RENAME`。

分批迁移有独立方法：`_migrate_session_tables_add_instance()`（`_base.py`，多 bot `instance_id`）、`persona_metrics` 合成锁列循环（`migrations.py` 尾段）、`_seed_departments()`（`persona_repo.py`）一次性回填。

## 4. EventBus 怎么跑

`EventBus(storage)`（`bus/event_bus.py`）：`_handlers: dict[event_type, list[_HandlerEntry]]`，`on()` 追加后按 `priority` 升序 `sort`。

- `emit(event)`（`:40`）：`_persist` → 顺序 `await entry.handler(event)`，每个包 try/except。
- `fire(event)`（`:63`）：`_persist` → `asyncio.create_task(self._run_handlers(event))`，task 存入 `_background_tasks` set，`add_done_callback(discard)` 防 GC。
- `_persist`（`:97`）：仅 `event.edict_id` 非空时 `storage.append_event`。

注：events 表 `id` 为 ULID 文本主键（`append_event` 自生成），不是自增整数。

## 5. FTS5 怎么跑

`create_fts_table(conn)`（`memory/fts.py:11`）建 `memory_fts` 虚表 + `memory_fts_insert/delete/update` 三触发器同步 `memory_entries`。`search_fts` 用 `escape_fts5_query` 把查询词逐个加双引号转义后 `MATCH`，支持按 persona_id 过滤；异常时降级返回空（`_init_fts` 设 `_fts_available`）。

## 6. 装配位置

`bootstrap/wiring_storage.py` 的 `wire_storage()` 中：`Storage` 先于 `EventBus(storage)` 初始化，`HookRegistry` 随后创建；订阅链在装配尾段注册（见 `runtime-flow.md` §2 订阅表）。各模块经构造注入 `storage` / `event_bus`。

## 7. 已知约束

- `storage/` 已按领域拆成 `_base` + 15 Mixin + facade（原单文件 ~4000 行 God Module 的拆分目标已完成）；`facade.py` 仅剩 3 个真正跨域方法，其余按领域分文件。
- `events` / `cost_ledger` / `dag_*` 无 FK，删除 edict 不级联清理，需手动 DELETE。
