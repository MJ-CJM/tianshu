# storage 子系统 · 设计总览

> 控制面的单一真相源 + 模块间的事件协议。前者用 SQLite 强一致存状态，后者用 EventBus 解耦时序。

## 1. 职责定位

| 关注点 | 子系统给出的答案 |
|---|---|
| 状态存哪 | 单一 SQLite（WAL），`Storage` 提供面向领域的方法，调用方不写 SQL |
| schema 怎么演进 | 启动时 `_create_tables` + 幂等 `_migrate`（逐条 ALTER，吞 duplicate column） |
| 模块怎么协作 | `EventBus` 事件总线，`emit`（同步保序）vs `fire`（后台调度），事件落 `events` 表 |
| 检索怎么做 | 控制面强一致放 SQLite，记忆全文检索另建 FTS5 虚表 |

立场：**控制面强一致，人格与长期记忆保文件可读性**。主库只放需要事务与查询的控制面数据；人格模板、Markdown 记忆留在文件系统，检索时再建索引（见 `domain-model.md` §4 数据源分层）。

## 2. 核心设计判断

| 判断 | 取舍 |
|---|---|
| 单库 SQLite 而非多库/外部 DB | 单机异步平台，零运维；WAL 让读写不互斥，一把进程锁保证写串行 |
| 迁移用「逐条 ALTER + 吞异常」而非版本号迁移框架 | 幂等、可重入、可重启；duplicate column / no such column 视为已应用而跳过 |
| 事件双模式 `emit`/`fire` | 链内保序需要 `emit` 顺序 await；HTTP 请求不想阻塞用 `fire` 后台 |
| handler 异常隔离 | 单个 handler 失败不连累兄弟（memory 写失败不挡 notifier 告警） |
| 事件按 priority 排序 | 同一事件多消费者时用数字定执行次序（policy<planner<默认<cost<memory） |

## 3. 与相邻子系统关系

| 相邻方 | 关系 |
|---|---|
| domain-model | 领域表（edicts/memorials/events/decrees）的**业务契约**在 `domain-model.md`；本子系统讲**物理表与迁移** |
| 全部子系统 | 都经 `Storage` 读写，经 `EventBus` 收发事件 |
| scheduling | `scheduler_jobs` 表持久化定时任务，事件 `edict.submitted→scheduled→plan.completed` 串起主链路 |
| llm/cost | `llm_configs`/`providers`/`cost_ledger`/`cost_budgets` |
| memory | `memory_entries` + `memory_fts` FTS5 虚表（触发器自动同步） |

## 4. 本目录子文档

| 文档 | 内容 |
|---|---|
| [schema.md](./schema.md) | SQLite 单一真相源、WAL、迁移机制、FTS5，按分组列出控制面物理表 |
| [events.md](./events.md) | EventBus、EventEnvelope、emit vs fire、事件持久化、主链路事件清单 |

**相关实现**：[../../impl/storage/](../../impl/storage/)
