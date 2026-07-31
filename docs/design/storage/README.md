# storage 子系统 · 设计总览

> 控制面的单一真相源 + 模块间的事件协议。前者用 SQLite 强一致存状态，后者用 EventBus 解耦时序。

## 1. 职责定位

| 关注点 | 子系统给出的答案 |
|---|---|
| 状态存哪 | 单一 SQLite（WAL），`Storage` 提供面向领域的方法，调用方不写 SQL |
| schema 怎么演进 | `schema_migrations` 账本 + immutable version/name/checksum；当前 V1–V24 |
| 模块怎么协作 | 业务广播用 EventBus；执行权威用 outbox + RunState + attempt lease/fencing |
| 检索怎么做 | 控制面强一致放 SQLite，记忆全文检索另建 FTS5 虚表 |

立场：**控制面强一致，人格与长期记忆保文件可读性**。主库只放需要事务与查询的控制面数据；人格模板、Markdown 记忆留在文件系统，检索时再建索引（见 `domain-model.md` §4 数据源分层）。

## 2. 核心设计判断

| 判断 | 取舍 |
|---|---|
| 单库 SQLite 而非多库/外部 DB | 单机异步平台，零运维；WAL 让读写不互斥，一把进程锁保证写串行 |
| 版本化迁移 | callback 由框架持有事务；ledger/checksum 漂移 fail closed；旧库只在权威形状一致时 adoption |
| 事件双模式 `emit`/`fire` | 链内保序需要 `emit` 顺序 await；HTTP 请求不想阻塞用 `fire` 后台 |
| handler 异常隔离 | 单个 handler 失败不连累兄弟（memory 写失败不挡 notifier 告警） |
| 事件按 priority 排序 | 同一事件多消费者时用数字定执行次序（policy<planner<默认<cost<memory） |
| 根执行可恢复 | Memorial、outbox、attempt 在事务内绑定；lease heartbeat + fencing 决定谁能提交终态 |

## 3. 与相邻子系统关系

| 相邻方 | 关系 |
|---|---|
| domain-model | 领域表（edicts/memorials/events/decrees）的**业务契约**在 `domain-model.md`；本子系统讲**物理表与迁移** |
| 全部子系统 | 都经 `Storage` 读写，经 `EventBus` 收发事件 |
| scheduling | `scheduler_jobs` 保存 cursor，`schedule_run` 保存槽位，ScheduledRunPreparer 事务性创建 attempt/outbox |
| llm/cost | `llm_configs`/`providers`/`cost_ledger`/`cost_budgets` |
| memory | `memory_entries` + `memory_fts` FTS5 虚表（触发器自动同步） |

## 4. 本目录子文档

| 文档 | 内容 |
|---|---|
| [schema.md](./schema.md) | SQLite 单一真相源、WAL、迁移机制、FTS5，按分组列出控制面物理表 |
| [events.md](./events.md) | outbox、EventBus、EventEnvelope、派发语义与主链路事件清单 |

**相关实现**：[../../impl/storage/](../../impl/storage/)
