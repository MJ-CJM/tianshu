# scheduling · 排期（Scheduler）

> 决定诏令「什么时候执行」。订阅 `edict.submitted`，按 schedule 类型发 `edict.scheduled`。

## 1. 四种调度模式

`Scheduler.schedule(edict)` 按 `Edict.schedule.type` 决策（`scheduler.py:249`）：

| type | 行为 | 持久化 |
|---|---|---|
| `immediate` | 立即准备一次独立 run | 写 `scheduler_jobs`、`schedule_runs`、Memorial、attempt 与 outbox；准备后 job 标 `completed` |
| `once` | 必须有未来的 `at`；到点准备一个独立 run | 写 `scheduler_jobs`，触发后标 `completed` |
| `cron` | croniter 算下次触发，循环 emit | 写 `scheduler_jobs`（带 `cron_expr` + `next_run`） |
| `interval` | 每 `interval_seconds` 周期 emit（每次新 memorial） | 写 `scheduler_jobs`（带 `interval_seconds`） |

> 核心是「立即 / 延时一次 / 周期」三类。`cron`（日历周期）与 `interval`（固定间隔）
> 都属周期类。非法时区、缺少/过期 once 时间、空 cron、非法 interval 均显式拒绝，不会
> 降级为 immediate。outer loop 或 `checkpointed/background` 长任务只能
> `immediate/once`，并强制 `concurrency_policy=skip`。

## 2. schedule 表达式解析

`schedule_spec.parse_spec(spec)` 把人类友好串解析为 `EdictSchedule`（供对话/工具下发定时任务），按优先级匹配：

| 写法 | 结果 |
|---|---|
| `every 2h` / `every 30m` | `interval`（`interval_seconds`） |
| `30m` / `2h` / `1d` / `45s` | `once`（at = now + Δ） |
| `0 9 * * *`（croniter 合法） | `cron` |
| ISO 8601（`2026-06-10T09:00:00+08:00`） | `once`（at = 该时刻） |

## 3. 时区与 cron

| 决策 | 内容 |
|---|---|
| 算下次触发 | `_next_cron_utc(cron_expr, tz_name)`：croniter 用 tz-aware base 做日历推算（处理 DST），结果 `astimezone(UTC)` |
| 存储 | 一律 UTC（`next_run` 列、内存 `_Job.next_run`） |
| 非法时区 | 模型/API/恢复路径显式失败，不猜测 UTC |

立场：**时区只在「算触发时刻」时存在，存储与 sleep 计算统一 UTC**，避免跨时区漂移。

## 4. 重启恢复与生命周期

| 能力 | 实现 |
|---|---|
| 启动恢复 | `_restore_jobs()` 从 active job 重建；周期 misfire 只补最近槽位；不再合法的长任务组合标 `failed` |
| 取消/暂停/恢复 | `cancel` / `pause`（停 timer 留行）/ `resume`（按持久化 schedule 重建 timer） |
| 修改时间 | `reschedule` 事务性更新 Edict schedule 与 job 游标；原 active job 修改后恢复 active |
| 手动触发 | `run_now(job_id, idempotency_key)`：准备独立 run，不动原 schedule |
| 列举 | `list_jobs()` 只返回 once/cron/interval 的 active/paused/failed/completed，并附 timezone、title、last_run；immediate 游标仅留作幂等与审计证据 |
| 历史 | `list_job_runs()` 关联真实 Memorial 终态、完成时间和错误 |
| 停机 | `stop()` cancel 所有 task |

job 状态语义：`active` 可触发、`paused` 保留但不触发、`completed` 是一次任务正常完成、
`failed` 是恢复/执行边界无法继续、`cancelled` 是用户取消。归档 Edict 会事务性取消其
schedule；列表不再显示归档任务。

每个触发槽位由 `ScheduledRunPreparer` 在一个事务中校验 Edict、建立确定性 run/Memorial/
attempt/outbox，并以 compare-and-set 推进 job 游标。重放同一槽位复用身份；租约和 fencing
阻止失去执行权的旧 runner 提交终态。

## 5. 系统级 cron

`register_system_jobs` 注册内置周期任务（不经 `scheduler_jobs`，独立 asyncio loop，UTC）：

| cron | 名称 | 触发 |
|---|---|---|
| `0 3 * * *` | `profile.daily_synthesis` | 人格画像每日合成 |
| `0 4 * * 0` | `skill.weekly_curate` | 兼容调度；非 dry-run 因 governed 写入未开放而跳过 |
| `0 5 * * *` | `universe.daily_evolve` | 平行位面每日演化（可选） |
| `30 5 * * *` | `universe.daily_code_propose` | 代码候选提案（可选、实验） |

系统 job 启动时会把上个进程残留的 `running` 台账收敛为 `failed`；同名 system job
仍在运行时，本轮记 `skipped`，不并发重入。

## 6. review 超时巡检

`_review_timeout_loop`（每 5 分钟）扫 `needs_review` 奏折：`completed_at` 超 1 小时未处理 → emit `review.timeout`（producer=scheduler），交 Notifier 升级提醒。

## 7. 输出事件

兼容路径仍可发 `edict.scheduled`，新路径以持久 schedule-run/attempt 为执行权威。事件用于
观察和升级兼容，不能单独证明某个槽位只执行了一次。

**相关实现**：[../../impl/scheduling/](../../impl/scheduling/)
