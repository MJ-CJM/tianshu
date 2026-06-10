# scheduling 子系统 · 实现现状

**相关设计**：[../../design/scheduling/](../../design/scheduling/)

覆盖 `src/tianshu/scheduler/`、`src/tianshu/planner/`。

## 1. 代码地图

| 文件 | 关键符号 | 职责 |
|---|---|---|
| `scheduler/scheduler.py` | `Scheduler`、`_Job`、`_next_cron_utc`、`_resolve_tz` | 四模式调度 + 持久化恢复 + 系统 cron |
| `scheduler/schedule_spec.py` | `parse_spec` | 人类友好串 → `EdictSchedule` |
| `planner/planner.py` | `Planner` | 双路径规划 + 校验 + 审批分叉 |
| `planner/prompts.py` | `build_planning_prompt`、`format_officials_roster`、`format_tools_list`、`PLANNING_USER_TEMPLATE`、`_PLANNING_ROLE` | prompt 装配 |
| `models/plan.py` | `Plan`、`PlanTask` | 规划结果模型 + `to_dag` |
| `models/edict.py` | `EdictSchedule` | `type ∈ {immediate,once,cron,interval}` / `at` / `cron` / `interval_seconds` / `timezone` |

## 2. Scheduler 怎么跑

构造：`Scheduler(event_bus, storage)`，持 `_jobs: dict[job_id, _Job]`、`_system_jobs`、`_system_cron_tasks`。

入口 `handle_submitted(event)`（`scheduler.py:418`）订阅 `edict.submitted` → `get_edict` → `schedule(edict, memorial_id)`。

`schedule()`（`:249`）按 `schedule.type` 分支建 `_Job` + 可选 `asyncio.create_task`：
- `immediate` → 直接 `_emit_scheduled`。
- `once` → `_delayed_emit(edict, delay)`；未来时刻 `save_scheduler_job(..., next_run=at)`。
- `cron` → `_cron_loop(edict, cron, job_id, tz)`；`save_scheduler_job(..., cron_expr, next_run)`。
- `interval` → `_interval_loop`；`save_scheduler_job(..., interval_seconds, next_run)`。

loop 内每轮 `get_edict` 取最新状态，非 `open` 即 break。`_emit_scheduled`（`:429`）把 submitted memorial 推到 `SCHEDULED` 后 `emit("edict.scheduled")`。

生命周期方法：`start`（`_restore_jobs` + 启 `_review_timeout_loop` + 系统 cron）、`stop`、`cancel` / `pause` / `resume` / `run_now` / `list_jobs`。

系统 cron：`register_system_jobs(profile_trigger, skill_curator, universe_evolver)`（`:84`）注册 `0 3 * * *` / `0 4 * * 0` / `0 5 * * *`，各自 `_system_cron_loop`（UTC）。

`scheduler_jobs` 表 storage API：`save_scheduler_job / list_active_scheduler_jobs / get_scheduler_job / list_scheduler_jobs / set_scheduler_job_status / update_scheduler_job_next_run / delete_scheduler_job`。

## 3. Planner 怎么跑

构造注入：`event_bus / storage / config_manager / official_selector / persona_loader / prompt_builder / tool_registry`（均经 `app.py` 装配）。

`handle_scheduled(event)`（`planner.py:196`）订阅 `edict.scheduled`：推 memorial → `PLANNING` → `await self.plan(edict)` → 按 `plan_review` 发 `plan.pending_review` 或 `plan.completed`。

`plan(edict)`（`:48`）：
1. `assigned_persona_id` 非空 → `_passthrough_plan(persona_id=...)`。
2. 否则选配置（active 或 planner persona 的 `llm_config_name`）→ 构 `LLMClient(temperature=0.3, max_tokens=2048)`。
3. 装配 prompt（persona_context + `_PLANNING_ROLE` + roster + tools）→ `llm.chat(messages)`。
4. `_extract_json`（三级降级）→ `PlanTask(**t)` → `_validate_assignments` → `Plan`。
5. 任意失败 → `_passthrough_plan`（默认 `DEFAULT_EXECUTOR_ID = "bingbu"`）。

`_validate_assignments`（`:287`）：非法 `assigned_official` 经 `selector.select_for_task(description)` 兜底，无 selector 落 `bingbu`。

## 4. 扩展点

| 想做什么 | 改哪里 |
|---|---|
| 加新调度模式 | `EdictSchedule.type` + `Scheduler.schedule` 分支 + `_restore_jobs` 恢复分支 + `parse_spec` |
| 加系统周期任务 | `register_system_jobs` 追加 `_system_jobs` 项 |
| 改规划 prompt | `planner/prompts.py`（`_PLANNING_ROLE` / roster / tools 格式化） |
| 换规划模型 | persona 的 `llm_config_name`，或 `edict.planner_persona_id` |
| 改 passthrough 默认执行官 | `_passthrough_plan` 的 `persona_id` 默认值 / `DEFAULT_EXECUTOR_ID` |

## 5. 审批补发链

gateway `api.py` `/edicts/{id}/plan/approve`（`:370`）：memorial `NEEDS_REVIEW→PLANNING` → `append_event("plan.approved")` → `event_bus.fire("plan.completed", producer="planner", payload=plan_payload)`。Planner 本身不轮询审批，靠该 API 补发 `plan.completed` 汇入执行链。

## 6. 已知约束

- `_validate_assignments` 直接访问 `persona_loader._personas`（私有属性），耦合到 PersonaLoader 内部结构。
- cron/interval loop 持有构造期的 `edict` 引用，但每轮 `get_edict` 拉新状态，schedule 本身变更需重建 job（pause+resume）。
