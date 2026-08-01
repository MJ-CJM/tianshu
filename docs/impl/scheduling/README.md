# scheduling 子系统 · 当前实现

**设计约束**：[`../../design/scheduling/`](../../design/scheduling/)
**发布边界**：[`../../CURRENT-STATE.md`](../../CURRENT-STATE.md)

## 1. 代码地图

| 路径 | 当前职责 |
|---|---|
| `models/edict.py` | schedule 模型、IANA timezone 校验、长任务组合校验 |
| `scheduler/schedule_spec.py` | 人类时间表达式 → `EdictSchedule` |
| `scheduler/scheduler.py` | job 恢复、timer/system jobs、用户管理操作和历史投影 |
| `application/scheduled_runs.py` | 事务性准备 schedule-run、Memorial、attempt、outbox 和游标 CAS |
| `storage/scheduler_repo.py` | `scheduler_jobs` 与 `schedule_run` repository |
| `gateway/execution_api.py` | list/cancel/pause/resume/run-now/reschedule/runs API |
| `tools/schedule_edict.py` | Agent/IM 调度工具，复用同一长任务边界 |
| `planner/planner.py` | managed attempt 内的规划、持久 plan revision 和 review suspend |

## 2. 调度模式

| 模式 | 校验 | 终态/下一步 |
|---|---|---|
| immediate | 无时间参数 | 直接进入持久执行入口 |
| once | `at` 必须有时区且在未来 | 触发后 job=`completed` |
| cron | 合法 cron + IANA timezone | CAS 推进下一 UTC cursor |
| interval | `interval_seconds >= 1` | CAS 推进下一 cursor |

非法输入返回 `422` 或将不再合法的旧 job 标为 `failed`；不降级成 immediate。普通周期任务
支持 `concurrency_policy=skip/allow`，默认 skip；misfire 目前只支持 `coalesce`。

outer loop 或 `execution_profile ∈ {checkpointed, background}` 只能 immediate/once，且
必须 skip。API、model helper、Scheduler restore 和 `schedule_edict` 工具使用同一校验。

## 3. 一次触发的持久边界

`ScheduledRunPreparer.prepare` 在 caller-owned transaction 中：

1. 加载 active job、open 且未 archive 的 Edict；
2. 用当前 cursor 和 schedule 算 deterministic run/event/Memorial identity；
3. 校验并发、replay envelope 和 `retry_limit + 1`；
4. 插入/复用 `schedule_run`、root Memorial、execution attempt 和 outbox；
5. 以 expected cursor 做 compare-and-set，once 写 `completed`，周期写下一 cursor；
6. commit 后唤醒 reconciler。

相同槽位重放复用身份；CAS 失败返回 `ScheduledFireConflict`。RunDispatcher 获得 attempt
lease 后执行，fencing token 阻止旧 owner 提交终态。这是 single-node restart safety，
不是分布式 exactly-once 声明。

## 4. 生命周期 API

| API | 实现 |
|---|---|
| `GET /api/scheduler/jobs` | 返回 owner 可见且可管理的 once/cron/interval job（active/paused/completed/failed） |
| `DELETE /api/scheduler/jobs/{id}` | 标 cancelled 并停止 live timer |
| `POST .../{id}/pause` | active → paused |
| `POST .../{id}/resume` | paused/failed 可在合法 envelope 下恢复 active |
| `PATCH .../{id}` | 事务性更新 Edict schedule 与 job cursor，保留 job ID/history |
| `POST .../{id}/run-now` | 要求 Idempotency-Key，独立准备一次 run，不改 schedule |
| `GET .../{id}/runs` | 返回 schedule 状态及关联 Memorial 的 execution status/error |

所有 job API 先通过 `require_owned_scheduler_job`；普通 principal 不能查看或控制其他
submitter 的 job。immediate 也会写入 `scheduler_jobs` 作为幂等与审计证据，但属于内部
执行游标，不在钦天监或 `schedule_edict(action=list)` 的定时任务列表中展示。

## 5. 启动恢复和常驻任务

- `start()` 先把旧进程残留的 system-job `running` 行标为 failed，再恢复 active jobs。
- 恢复周期 misfire 时只补最近一次，随后推进到未来 cursor。
- Edict 已关闭/归档则不再恢复；非法的旧长任务组合标 failed。
- review timeout 与 orphan sweep 是 readiness 所需常驻任务；任一异常退出会使
  `/health/ready` 失败。
- system jobs：daily profile、weekly skill、daily universe evolve、daily code propose
  （后两项为可选实验能力）。同名仍在运行时本轮记 skipped。

## 6. Planner

Planner 在 dispatcher-owned attempt 内执行。直接指定 persona 时生成
`planning_mode=direct` 的 passthrough plan；LLM disabled/empty/invalid/error 时生成
`planning_mode=fallback` 并保存 `fallback_reason`，不会把 fallback 伪装成正常 LLM
规划。LLM usage 携带 edict/memorial/operation 进入统一成本账本。

需要 plan review 时，持久 Decision 会 suspend attempt；决定 resolved 后由 continuation
recovery 恢复，不依赖进程内等待对象。
