# scheduling · 排期（Scheduler）

> 决定诏令「什么时候执行」。订阅 `edict.submitted`，按 schedule 类型发 `edict.scheduled`。

## 1. 四种调度模式

`Scheduler.schedule(edict)` 按 `Edict.schedule.type` 决策（`scheduler.py:249`）：

| type | 行为 | 持久化 |
|---|---|---|
| `immediate` | 立即 `emit("edict.scheduled")` | 否（内存 job 仅记录） |
| `once` | `at` 为空或已过期 → 立即；否则延时一次 emit | `at` 在未来时写 `scheduler_jobs` |
| `cron` | croniter 算下次触发，循环 emit | 写 `scheduler_jobs`（带 `cron_expr` + `next_run`） |
| `interval` | 每 `interval_seconds` 周期 emit（每次新 memorial） | 写 `scheduler_jobs`（带 `interval_seconds`） |

> 设计说明：核心是「立即 / 延时一次 / 周期」三类。`cron`（日历周期）与 `interval`（固定间隔）都属周期类，分开是因为 cron 要处理时区与 DST，interval 只是定长 sleep。非法配置（cron 无表达式、interval<1）一律降级为 immediate。

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
| 非法时区 | `_resolve_tz` 回退 UTC 并 warn |

立场：**时区只在「算触发时刻」时存在，存储与 sleep 计算统一 UTC**，避免跨时区漂移。

## 4. 重启恢复与生命周期

| 能力 | 实现 |
|---|---|
| 启动恢复 | `_restore_jobs()` 从 `list_active_scheduler_jobs` 重建 task；edict 已非 `open` 则删 job；`once` 已过期则立即 emit |
| 取消/暂停/恢复 | `cancel` / `pause`（cancel timer 留行）/ `resume`（按持久化 schedule 重建 timer） |
| 手动触发 | `run_now(job_id)`：立即 emit 一次，不动原 schedule |
| 列举 | `list_jobs()`：合并 DB 行与内存 live `next_run` |
| 停机 | `stop()` cancel 所有 task |

cron/interval loop 每轮都 `get_edict` 拉**最新**状态：edict 不再 `open` 即停 loop（任务被关闭后定时器自动退出）。

## 5. 系统级 cron

`register_system_jobs` 注册内置周期任务（不经 `scheduler_jobs`，独立 asyncio loop，UTC）：

| cron | 名称 | 触发 |
|---|---|---|
| `0 3 * * *` | `profile.daily_synthesis` | 人格画像每日合成 |
| `0 4 * * 0` | `skill.weekly_curate` | skill 每周整理（可选） |
| `0 5 * * *` | `universe.daily_evolve` | 平行位面每日演化（可选） |

## 6. review 超时巡检

`_review_timeout_loop`（每 5 分钟）扫 `needs_review` 奏折：`completed_at` 超 1 小时未处理 → emit `review.timeout`（producer=scheduler），交 Notifier 升级提醒。

## 7. 输出事件

`_emit_scheduled(edict, memorial_id)`：把 `submitted` 状态的 memorial 推进到 `SCHEDULED`，再 `emit("edict.scheduled", payload={"goal": ...})` 交给 Planner。

**相关实现**：[../../impl/scheduling/](../../impl/scheduling/)
