# EventBus 与 outbox 实现现状

相关设计：[../../design/bus/README.md](../../design/bus/README.md)

## 1. 代码位置

| 位置 | 当前职责 |
|---|---|
| `src/tianshu/bus/event_bus.py` | 进程内 `EventBus`、named consumer 派发报告、本地订阅 |
| `src/tianshu/application/outbox.py` | outbox claim、事件重建、消费进度、失败退避与生命周期 |
| `src/tianshu/storage/outbox_repo.py` | `outbox_events` 与 consumer 进度的数据访问 |
| `src/tianshu/application/event_history.py` | `EventHistoryConsumer`：消费已派发的 Edict 范围事件 |
| `src/tianshu/storage/event_repo.py` | `events` 时间线的幂等 repository / projection 持久化 |
| `src/tianshu/bootstrap/wiring_storage.py` | 创建总线并注册通配历史消费者 |

`EventEnvelope` 与 `make_event` 位于 `src/tianshu/models/events.py`。

## 2. EventBus 当前 API

| 方法 | 当前签名要点 | 行为 |
|---|---|---|
| `dispatch` | `event, skip_consumers=()` | 仅派发 named consumer，返回 `DispatchReport` |
| `emit` | `await emit(event)` | named + local 顺序尽力扇出，不返回消费结果 |
| `fire` | `fire(event)` | 后台尽力扇出；任务引用保存在 `_background_tasks` |
| `on` | 必填 `consumer_name`，可选 `priority` | 注册稳定消费者；重复名称会拒绝 |
| `on_local` | 无 consumer name | 注册不参与 outbox 恢复的临时消费者 |
| `off` / `off_local` | 传同一 handler 引用 | 注销对应订阅 |

构造函数是 `EventBus()`，没有 storage 参数；`_persist` 也已不存在。

## 3. outbox 派发

`OutboxDispatcher` 取得一条持久化记录后：

1. 校验控制字段并重建 `EventEnvelope`。
2. 查询已经成功的 consumer name，作为 `skip_consumers`。
3. 调 `EventBus.dispatch`。
4. 分别记录成功消费者。
5. 全部成功则标记 published；有失败则写脱敏错误并按退避时间重试。

入口提交由 `EdictApplicationService` 在同一事务写 Edict、Memorial、
submission 幂等记录和 outbox。managed run、Decision、终态完成等边界也通过各自
应用服务写 outbox，避免“对象已落库但事件丢了”。

## 4. 装配与扩展检查

- `wiring_storage.py` 注册 `EventHistoryConsumer` 为 `*`、priority=0。
- 其余消费者由 `bootstrap/wiring_*.py` 和各渠道模块注册，全部提供稳定
  `consumer_name`。
- 关键消费者必须可重入，并用持久化事件/领域身份做幂等。
- 仅 UI 流式监听这类短生命周期功能使用 `on_local`。
- 测试新消费者时至少覆盖：优先级、失败报告、重试跳过已成功消费者和重复派发幂等。

运行健康度同时依赖 outbox lifecycle；`/health/ready` 未通过时不能仅凭 HTTP
进程存活就判断后台派发可用。
