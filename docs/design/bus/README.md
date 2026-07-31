# bus · 持久化 outbox 与进程内 EventBus

天枢把“工作不能因进程退出而丢失”和“模块之间低耦合”拆成两层：

- **outbox 是关键事件的持久化权威**：业务对象与 outbox 事件在同一事务提交，
  后台可重试派发。
- **EventBus 是进程内扇出器**：按优先级调用已命名消费者，也支持不保证恢复的
  本地通知；它自身不写库、不提供跨进程消息队列。

相关实现：[../../impl/bus/README.md](../../impl/bus/README.md)
事件契约：[../storage/events.md](../storage/events.md)

## 1. 当前主路径

```text
入口 / 领域服务
  -> transaction(domain rows + outbox event)
  -> OutboxDispatcher claim
  -> EventBus.dispatch(event)
  -> 逐个记录 named consumer 成功结果
  -> 全部成功: published
     任一失败: 退避后重试，已成功 consumer 不重复调用
```

`edict.submitted`、运行终态、Decision 结果等关键状态都应从事务 outbox 进入这条
路径。`events` 时间线由通配的 `EventHistoryConsumer`（priority=0）消费并写入，
不是 `EventBus` 自动持久化。

## 2. 三种派发语义

| API | 语义 | 适用范围 |
|---|---|---|
| `dispatch` | 顺序调用 named consumer，返回每个消费者的成功/失败报告 | outbox 的可恢复派发 |
| `emit` | 顺序 await，但吞掉单个 handler 异常 | 进程内、尽力而为的兼容链路 |
| `fire` | `create_task` 后立即返回，后台尽力扇出 | toast、实时提示等非关键通知 |

`emit` / `fire` 都没有自动持久化、租约或重投保证，不得作为“任务已可靠提交”的
依据。HTTP/飞书/MCP 等入口必须先走共享应用服务或 managed-run ingress。

## 3. named consumer 与本地订阅

`on(event_type, handler, consumer_name=..., priority=100)` 注册稳定消费者名。
消费者名用于 outbox 记录消费进度，升级时应保持稳定，避免旧事件被当成从未消费。
同一事件内数字越小越先执行。

`on_local(...)` 只用于当前进程的临时订阅，例如页面流式更新。它不参与
`dispatch` 报告，也不会被 outbox 恢复；这正是本地实时体验与持久化业务语义之间的
边界。

## 4. 失败与幂等边界

- 一个消费者抛异常不会阻止同轮其它消费者执行；`dispatch` 会把失败交还 outbox。
- outbox 重试时跳过已经记录成功的 named consumer。
- 消费者仍需按事件身份保持幂等，不能依赖“绝对只调用一次”。
- 当前发布与验证基线是**单实例 SQLite 部署**；这些租约与消费记录不能被宣传为
  已验证的多节点消息系统。

## 5. 扩展规则

新增关键事件时，先在产生领域变更的事务中写 outbox，再注册带稳定
`consumer_name` 的消费者。只有明确允许丢失的 UI/实时旁路才直接使用
`emit`、`fire` 或 `on_local`。
