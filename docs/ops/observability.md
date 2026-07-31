# 可观测性与故障排查

> 当前是单主机 SQLite 运行模型。不要只凭一个事件或一行状态判断“任务已经完成”；根执行、
> 调度、通知和安全审计各有自己的持久身份。

## 1. 五类相关身份

| 关注点 | 主要身份/表 | 回答的问题 |
|---|---|---|
| 用户任务 | `edicts.id` | 用户下达的长期业务对象是什么 |
| 一次执行 | `memorials.id` | 这一轮/follow-up/周期触发结果是什么 |
| 运行权威 | `run_states`、`execution_attempts` | 谁持有 lease，attempt 是否 retry/suspended/terminal |
| 定时触发 | `scheduler_jobs.job_id`、`schedule_run.id` | 哪个时间槽位触发，cursor 是否推进 |
| 通知 | `internal_notification_deliveries.delivery_id` | 哪些渠道已接受，是否 retry/dead-letter |

`events` 是按 Edict 观察业务时间线的入口，但 EventBus 事件存在不等于 attempt fenced
completion 已提交。最终任务状态看 Memorial + attempt/run state；最终定时触发看
schedule-run + job cursor；最终通知看 delivery 行。

## 2. 用户/API 视图

普通 principal 只能访问自己 `Edict.submitter` 匹配的资源：

| API | 用途 |
|---|---|
| `GET /api/edicts/{id}/detail` | Edict、最新 Memorial、运行与治理摘要 |
| `GET /api/edicts/{id}/events` | 单任务业务事件 |
| `GET /api/edicts/{id}/iterations` | outer-loop 迭代 |
| `GET /api/edicts/{id}/evidence` | Evidence bundle |
| `GET /api/scheduler/jobs` | 自己的 job 和 last_run |
| `GET /api/scheduler/jobs/{id}/runs` | 某 job 的触发/执行历史 |

越权资源统一返回 404。全局审计统计、network events、Worker 列表/状态、记忆、全局成本和
SystemAudit 需要 admin；不要用普通 PAT 做运维全局探测。

## 3. 任务卡住时

按以下顺序检查：

1. `/health/ready` 是否为 ready；只看 `/health/live` 不足；
2. Edict 是否 open、是否 archived、`runtime.lifecycle_phase` 是否 paused/winding_down；
3. 最新根 Memorial 是 submitted/planning/running/needs_review/terminal 哪一态；
4. 是否存在 pending Decision（plan/tool/L3）；
5. `execution_attempts` 是否有有效 lease、heartbeat、retry_at 或 suspended；
6. outer loop 是否有 checkpoint，最近 iteration/check/critic 是什么；
7. 若进程刚重启，给 reconciler 一次恢复窗口，再判断是否 orphan。

Scheduler 的 orphan sweep 会收敛真正失去进展的旧 Memorial；DAG 节点和有受监督 attempt
的根执行由各自恢复机制负责，不应被通用 sweep 抢占。

## 4. Scheduler 排查

job 状态：

- `active`：等待/循环触发；
- `paused`：保留 cursor，不触发；
- `completed`：once 已正常准备 run；
- `failed`：恢复或 worker 边界不能继续，可在修复原因后 resume；
- `cancelled`：用户取消或 Edict archive。

周期错过多个槽位时默认 `coalesce`，只准备最近一次。`run-now` 创建独立、幂等 run，不改变
原 schedule。若 job cursor 已推进但页面还没有 terminal result，继续看关联 Memorial 和
attempt，而不是再次 run-now 制造额外业务运行。

## 5. 长程任务排查

- pause 在当前轮边界生效；看到 API 返回 paused 后，当前 actor 可能仍在收尾该轮。
- steer 进入 pending 后，下一轮才注入；只有 checkpoint 成功后才 ack 删除。
- checkpointed/background 在重启后优先从 durable attempt/continuation 恢复，旧 checkpoint
  是兼容 fallback。
- actor 明确 failed/cancelled 后 critic 不再改变终态。
- checkpoint 只有在最终 Memorial 终态和监督收口持久化后才清理；终态后仍残留 checkpoint
  应作为一致性问题排查。

## 6. 通知排查（V24）

delivery 状态：

| 状态 | 含义 |
|---|---|
| pending | 等待 claim |
| claimed | worker 持有有效 lease |
| retry_wait | 至少一个未成功渠道等待重试 |
| delivered | 所有配置渠道 adapter/provider 均接受 |
| dead_letter | 超过 deadline 或 max attempts |

检查 `accepted_channels_json`。若 `["feishu"]` 已存在而 email 失败，下一次只发 email；不要
手工清空数组，否则可能重复通知。accepted 不证明收件人阅读或第三方最终送达。

## 7. 成本与用量

`cost_ledger` 按 Edict/Memorial 保存 prompt、completion、total、cache-read tokens 和 CNY
估算。失败和取消也会结算已发生用量；多 provider/model run 标签为 `multiple`；平台级
调用归 `__platform__`。本地数字取决于 provider usage 和价格配置，不等于供应商账单。

## 8. SystemAudit

`system_audit_events` 是独立于普通 Edict events 的 tamper-evident hash chain。读取和 export
会从 genesis 校验 sequence、previous hash 和 event hash；发现 gap/篡改返回完整性错误。

- `GET /api/audit/system`
- `GET /api/audit/system/export`

两者只允许 admin。该链能检测本地数据库边界内的普通篡改，但不能抵抗特权攻击者同时替换
数据库和 trust root，也不是外部 WORM 存储。

## 9. 直接查 SQLite

优先使用 API，因为 API 会执行 ownership、admin 和完整性校验。只有在停机或可信本地排障
时才直接打开数据库；先备份 DB/WAL/SHM，不要在运行中手工 UPDATE 状态或迁移表。尤其不要
手工修改：

- `schema_migrations`；
- `execution_attempts` fencing/lease；
- `scheduler_jobs.next_run`；
- `accepted_channels_json`；
- `system_audit_events`。

这些字段有跨表或 hash/CAS 约束，手改一行可能制造无法安全恢复的假状态。
