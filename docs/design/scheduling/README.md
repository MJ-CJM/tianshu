# scheduling 子系统 · 设计总览

> 下旨之后、执行之前的两道关：排期（什么时候做）与规划（怎么拆、谁来做）。

## 1. 职责定位

| 关注点 | 子系统给出的答案 |
|---|---|
| 何时执行 | `Scheduler` 按 `Edict.schedule` 决策：立即 / 延时一次 / cron / 周期 |
| 重启不丢任务 | 定时任务持久化到 `scheduler_jobs`，启动 `_restore_jobs` 恢复 |
| 怎么拆任务 | `Planner` 产出 `Plan`：直接指派走直通，否则 LLM JSON 规划 |
| 谁来执行 | `PlanTask.assigned_official` 指明 persona，无效则 selector 兜底 |
| 要不要人审 | `plan_review=true` 时发 `plan.pending_review`，审批通过再执行 |

链路位置：`edict.submitted →[Scheduler]→ edict.scheduled →[Planner]→ plan.completed →[Executor]`。

## 2. 核心设计判断

| 判断 | 取舍 |
|---|---|
| 自研轻量 scheduler 而非引第三方 | 只需 asyncio task + croniter，零额外依赖；持久化靠 `scheduler_jobs` 表 |
| 定时任务持久化 + 重启恢复 | 进程重启不丢 cron/once/interval；`_restore_jobs` 重建 task |
| cron 按时区算、统一存 UTC | croniter 用 tz-aware 处理 DST，存储/sleep 一律 UTC |
| Planner 双路径 | 用户直接指派 → 跳过 LLM 省成本；未指派 → LLM 规划，失败兜底 passthrough |
| 规划失败永不阻断 | LLM 空响应/解析失败/异常 → 一律退回单任务 passthrough plan |
| 人审是事件分叉而非阻塞 | `plan_review` 走 `plan.pending_review` 事件，审批 API 再补发 `plan.completed` |

## 3. 与相邻子系统关系

| 相邻方 | 关系 |
|---|---|
| gateway | `POST /api/edicts` fire `edict.submitted` 触发 Scheduler；审批走 `/edicts/{id}/plan/approve` 补发 `plan.completed` |
| storage | `scheduler_jobs` 持久化定时任务；事件落 `events` 表 |
| llm | Planner 直接构造 `LLMClient`（temperature=0.3）做 JSON 规划，可用 planner persona 的命名配置 |
| persona | Planner 用 `OfficialSelector` 列名册、校验 `assigned_official`，无效经 `select_for_task` 兜底 `bingbu` |
| executor | 消费 `plan.completed`；单任务直跑 Agent，多任务转 DAG（`Plan.to_dag`） |

## 4. 本目录子文档

| 文档 | 内容 |
|---|---|
| [scheduler.md](./scheduler.md) | Scheduler 四模式、`scheduler_jobs`、croniter、重启恢复、系统 cron、`edict.submitted→scheduled` |
| [planner.md](./planner.md) | Planner 双路径、Plan/PlanTask、prompt 装配、人审、`edict.scheduled→plan.completed` |

**相关实现**：[../../impl/scheduling/](../../impl/scheduling/)
