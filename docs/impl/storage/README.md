# storage 子系统 · 当前实现

**设计**：[`../../design/storage/`](../../design/storage/)
**发布边界**：single-node SQLite，见
[`../../CURRENT-STATE.md`](../../CURRENT-STATE.md)。

## 1. 代码地图

| 路径 | 职责 |
|---|---|
| `storage/_base.py` | connection、WAL/foreign keys、启动迁移锁、敏感迁移恢复备份 |
| `storage/schema.py` | V1 baseline DDL |
| `storage/migrations.py` | V1–V24 immutable migration definitions |
| `storage/migration_ledger.py` | ledger 校验、事务性 apply/adopt、checksum |
| `storage/facade.py` | Storage 组合根和跨域事务（含 Edict tombstone） |
| `storage/*_repo.py` | Edict、Memorial、Scheduler、Cost、Notification、Orchestrator 等领域访问 |
| `storage/unit_of_work.py` | caller-owned SQLite transaction |
| `storage/outbox_repo.py` | durable event outbox（提交后分发权威） |
| `application/event_history.py`、`storage/event_repo.py` | 可查询的持久事件历史 |
| `storage/attempt_ledger.py` | execution attempt lease/fencing |
| `bus/event_bus.py` | 进程内实时广播；不是跨重启历史或待投递权威 |

## 2. 初始化与并发

`Storage.init_db()` 打开共享 SQLite connection，启用 WAL 和 foreign keys，在同一数据库的
跨进程 startup lock 内检查 pending migration，并在成功后初始化 FTS。Storage 的共享
connection 由一把进程级 lock 保护；跨表原子操作使用 UnitOfWork。

这套模型只证明单主机、single-node 行为。WAL 不是多节点协调、共识、replica recovery 或
跨进程业务 exactly-once 协议。

## 3. Migration ledger

`schema_migrations` 保存 `version/name/checksum/applied_at`。定义必须严格递增且 checksum
固定；已应用条目与代码不一致会 fail closed。Migration callback 不能自行控制事务。

对于无 ledger 的旧库，只有当所有 migration-owned 对象与“在内存库从 V1 完整重放到当前
版本”的权威形状语义等价时，才 adoption；残留临时表、缺失对象或漂移 schema 会拒绝。

当前尾部：

| 版本 | 名称 | 变化 |
|---|---|---|
| V23 | `0023_cost_cache_read_tokens` | `cost_ledger.cache_read_tokens` |
| V24 | `0024_notification_channel_progress` | `internal_notification_deliveries.accepted_channels_json` |

敏感 secret migration 之前先 checkpoint WAL，并使用数据库旁确定性
`pre-migration-recovery.legacy-sensitive.bak`。备份含旧明文，必须按 secret 保护并在恢复后
清理；详见 [`../../ops/credentials.md`](../../ops/credentials.md)。

## 4. 任务、调度与执行权威

- `edicts`/`memorials` 是业务对象；
- `outbox_events` 是提交后待投影事件；
- `run_states`/`execution_attempts` 保存恢复状态、lease、heartbeat 和 fencing token；
- `scheduler_jobs` 保存持久 cursor；
- `schedule_run` 保存每个触发槽位；
- `decision_requests` 保存可跨重启恢复的人工决定；
- `artifact_records`/`evidence_bundles` 保存不可变交付证据。

调度 fire、follow-up 和普通根执行都先在事务里绑定 Memorial、attempt 与 outbox，commit
后再唤醒 reconciler。Fencing 阻止已经失去 lease 的旧 runner 提交终态。

## 5. Edict tombstone

`Storage.tombstone_edict()` 在同一事务中：

1. 确认没有未结束根执行，否则抛 `EdictArchiveConflict`；
2. 写 `metadata.archived_at`；
3. 把关联 active/paused scheduler jobs 标 cancelled；
4. 幂等追加一条 `edict.archived` 事件。

普通列表过滤 archived 行，但按 ID 仍可读取治理历史。Gateway 的 DELETE 不触发物理级联。

## 6. V23 成本

`cost_ledger` 保存 prompt/completion/total/cache-read tokens 和 CNY 成本。一次 run 观察到多
provider/model 时使用 `multiple` 聚合标签；取消、失败和成功终态都会结算已有 tracker。

## 7. V24 通知

`internal_notification_deliveries` 状态为
`pending/claimed/retry_wait/delivered/dead_letter`。claim 由 lease/version CAS 保护。每个
渠道成功后立即把名称加入 JSON array；重试跳过数组中已有渠道。全部渠道 accepted 才能
delivered，deadline 或 max attempts 后进入 dead letter。

accepted 是本地 adapter/provider acceptance，不是收件人阅读证明。

## 8. 记忆

主库 `memory_entries + memory_fts` 是 Markdown 的派生索引；
`~/.tianshu/memory/drawers.sqlite3` 是独立 Drawer 库。新日志使用稳定 ID；索引可以从
Markdown 重建。删除先修改真相源，拒绝 index-only 假删除。
