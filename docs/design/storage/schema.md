# storage · 物理 schema 与迁移

> 单一 SQLite 的物理结构。领域对象的业务契约见 [../domain-model.md](../domain-model.md)；本篇只讲物理表、PRAGMA 与 schema 演进。

## 1. 单库与 PRAGMA

`Storage(db_path).init_db()` 的装配（`storage/_base.py`）：

| 步骤 | 内容 |
|---|---|
| 连接 | `sqlite3.connect(db_path, check_same_thread=False)`，`row_factory=sqlite3.Row`（行按字段名访问） |
| `PRAGMA journal_mode=WAL` | 读写不互斥，读不阻塞写 |
| `PRAGMA foreign_keys=ON` | 启用外键级联 |
| `_create_tables()` | `CREATE TABLE IF NOT EXISTS` 建全部基础表 |
| `_migrate()` | 幂等迁移（增列、改 PK 重建） |
| `_init_fts()` | 建 `memory_fts` FTS5 虚表 + 3 触发器 |

并发模型：一把进程级 `threading.Lock` 保护共享连接，读写都通过 Storage 方法进入该
边界；WAL 改善数据库层读写并发，但不把同一 Python connection 变成无锁可并发对象。
默认库 `~/.tianshu/tianshu.db`，Drawer 记忆是独立库
`~/.tianshu/memory/drawers.sqlite3`（见 memory 子系统）。这是单进程、single-node
SQLite 设计，不是多写者协议。

## 2. 控制面表（按分组）

> 表名、列名均以 `storage/schema.py`（DDL）+ `storage/migrations.py`（迁移）为准。领域核心四表的字段语义见 domain-model。

**任务核心**

| 表 | 主键 | 关键列 / 外键 |
|---|---|---|
| `edicts` | id | 业务列经迁移补全：`status / title / source / submitter / idempotency_key / priority / schedule_json / dispatch_json / runtime_json / assigned_persona_id / planner_persona_id / plan_review / acceptance_json / execution_profile` |
| `memorials` | id | FK `edict_id→edicts.id` ON DELETE CASCADE；`status / usage_json / attempt / parent_memorial_id / dag_node_id / persona_id / runtime_override_json / acceptance_override_json / reasoning_content / universe_id / feedback_score` |
| `events` | id (TEXT/ULID) | FK `edict_id→edicts.id` CASCADE；`memorial_id / event_type / payload_json` |
| `decrees` | id | `memorial_id / action / comment / amended_goal / actor` |

**DAG**

| 表 | 主键 |
|---|---|
| `dag_executions` | id（`edict_id / plan_json / status / root_memorial_id`） |
| `dag_nodes` | (dag_execution_id, node_id)（`depends_on_json / checkpoint_json / memorial_id / assigned_official`） |

**记忆索引**

| 表 | 备注 |
|---|---|
| `memory_entries` | `persona_id / edict_id / memorial_id / category / content / source / confidence / entity_refs_json / expires_at / access_level` |
| `memory_fts` | FTS5 虚表，rowid 关联 `memory_entries`，触发器自动同步 |

**成本**

| 表 | 备注 |
|---|---|
| `cost_ledger` | `edict_id / memorial_id / provider_name / model / prompt/completion/total/cache_read tokens / cost_cny`；V23 增 `cache_read_tokens` |
| `cost_budgets` | `scope / budget_cny / spent_cny / period / reset_at` |

**配置**

| 表 | 备注 |
|---|---|
| `llm_configs` | ConfigManager 命名配置，含 `is_active` |
| `providers` | `model / api_base / capabilities / status / priority / rpm_limit / tpm_limit / cost_per_1k_prompt/completion/cache_read` |
| `engine_preferences` | 引擎偏好（含 scrapling 动态/隐身开关） |
| `tool_switches` | 工具禁用列表 |
| `mcp_server_overrides` | MCP server 完整定义（`transport / command / args_json / url / headers_json / default_tier / tool_overrides_json` …） |

**人格与 skill**

| 表 | 备注 |
|---|---|
| `departments` | 部门元数据（六部） |
| `personas` | `skills_allowed / llm_config_name / title / memory_global_read` 经迁移补全 |
| `persona_metrics` | 含 PROFILE 合成锁列（`synthesis_in_progress / started_at / tasks_since_last_synthesis`） |
| `skill_metrics` | `used_count / success_rate / state / pinned / archived_at / absorbed_into / human_curated` |

**权限**

| 表 | 备注 |
|---|---|
| `session_rules` | Policy 会话级规则（tier / allow / deny） |
| `network_credentials` | `host_pattern / kind(edict_auth\|engine_provider) / provider_name / enabled`，软删 `deleted_at` |

**长任务**

| 表 | 备注 |
|---|---|
| `outer_loop_iterations` | 外循环迭代记录 |
| `outer_loop_checkpoints` | 检查点 |
| `supervision_reports` | PK `(memorial_id, persona_id)`（迁移自 edict 维度，支持多监督官 + follow-up 多奏折） |

**飞书 / 渠道**

| 表 | 备注 |
|---|---|
| `feishu_session_anchor / feishu_seen_messages / feishu_pending_cards / feishu_thinking_messages` | 飞书会话/去重/卡片/typing |
| `telegram_session_anchor / telegram_seen_messages / telegram_pending_buttons / telegram_thinking_messages` | telegram 对应表 |
| `channel_configs / channel_instances` | 多渠道/多 bot 实例配置（会话表经迁移加 `instance_id` 维度） |
| `internal_notification_deliveries` | durable outbox；V24 的 `accepted_channels_json` 逐渠道保存 provider acceptance，重试跳过已成功渠道 |

**平行位面 / 插件**

| 表 | 备注 |
|---|---|
| `universes` | 平行位面（`code_ref` 经迁移补） |
| `variant_eval_runs` | 变体评估记录 |
| `plugins` | `manifest_json / sha256 / enabled` |

## 3. 版本迁移机制（当前 V1–V24）

| 机制 | 实现 |
|---|---|
| 迁移账本 | `schema_migrations(version,name,checksum,applied_at)`；定义必须严格递增、名称唯一、checksum 固定 |
| 事务所有权 | `apply_migrations` 逐项控制事务；migration callback 不能自行 commit/rollback、执行事务 SQL 或 `executescript` |
| 旧库接纳 | 无 ledger 但物理 schema 与完整权威形状语义等价时，记录 adoption；形状不一致则 fail closed |
| 敏感迁移 | 启动锁串行化同一数据库升级；敏感明文迁移前处理 WAL 并使用确定性恢复备份 |
| 当前尾部 | V23 `cost_cache_read_tokens`；V24 `notification_channel_progress` |

设计立场：**迁移序列、账本与 checksum 共同定义当前 schema**。不能手工补列后伪装成已
迁移，也不能把一次 `ALTER TABLE` 成功等同于完整迁移完成。当前最新版本为 V24。

## 4. FTS5 全文检索

`memory_fts`（`memory/fts.py`）是 `CREATE VIRTUAL TABLE ... USING fts5`，索引 `memory_entries` 的 `id/persona_id/category/content`。3 个触发器（`memory_fts_insert/delete/update`）在 `memory_entries` 增删改时自动同步索引。查询经 `escape_fts5_query` 转义后 `MATCH`，FTS 不可用时降级返回空。

## 5. 级联与清理

面向用户的 Edict 删除现在是 tombstone/archive，不执行这条物理级联：列表隐藏记录并取消
schedule，但保留治理历史。只有显式物理清理路径才会触发底层 FK 行为；开源默认 UI/API
不把“删除”描述为抹除审计证据。

**相关实现**：[../../impl/storage/](../../impl/storage/)
