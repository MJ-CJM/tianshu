# scheduling · 规划（Planner）

> 决定诏令「怎么拆、谁来做」。订阅 `edict.scheduled`，产出 `Plan`，发 `plan.completed`（或 `plan.pending_review`）。

## 1. 双路径决策

`Planner.plan(edict)`（`planner.py:48`）按是否直接指派分两条路：

| 条件 | 路径 | 成本 |
|---|---|---|
| `assigned_persona_id` 非空 | **passthrough**：单任务 plan，整个 goal 直通该 persona | 零 LLM |
| 未指派 | **cabinet LLM 规划**：构造 prompt → LLM 返 JSON → 解析 tasks | 一次 LLM 调用 |

立场：**能省 LLM 就省**。用户已点名执行官时，规划这步纯属多余；只有「让内阁决定怎么拆」时才花 LLM。

## 2. LLM 规划的 prompt 装配

cabinet 路径的 system prompt 由 `build_planning_prompt` 拼四段：

| 段 | 来源 |
|---|---|
| 规划官 persona 上下文 | `PromptBuilder.build`（仅当 `planner_persona_id` 指定且有 persona），skills 预算 5000 字符 |
| 规划职责说明 | `_PLANNING_ROLE`（含简单/复杂任务判定规则 + JSON 输出示例） |
| 可用执行官名册 | `format_officials_roster`（非 neige 部门 persona 的 Markdown 表：ID/名称/部门/工具/可委派） |
| 可用工具列表 | `format_tools_list`（ToolRegistry 的 definition 名） |

user 消息用 `PLANNING_USER_TEMPLATE` 填 goal / context / constraints / output_format。

LLM 用 `temperature=0.3`、`max_tokens=2048`（确定性优先）。配置来源：默认 active；若 `planner_persona_id` 的 persona 有 `llm_config_name` 且 enabled，则用该命名配置（用 planner persona 自己的脑子规划）。

## 3. Plan / PlanTask 契约

| 模型 | 字段 |
|---|---|
| `PlanTask` | `task_id / description / depends_on / tools_required / can_run_parallel / estimated_tokens / assigned_official` |
| `Plan` | `tasks / priority_order / total_estimated_tokens`，`to_dag(edict_id, max_concurrency)` 转 DAGExecution |

单任务 plan → 单节点 DAG（向后兼容）；多任务 → 按 `depends_on` 构 DAG，交 DAGScheduler 拓扑调度。

## 4. 容错与兜底

规划失败永不阻断主链路，**一律退回 passthrough**（`_passthrough_plan`，默认 persona `bingbu`）：

| 失败点 | 处理 |
|---|---|
| LLM 配置 disabled | passthrough |
| LLM 空响应 | passthrough（reasoner 模型先尝试用 `reasoning_content` 当来源） |
| JSON 解析失败 | passthrough（`_extract_json` 三级降级：直 parse → ```` ```json ```` → 首个 `{...}`） |
| tasks 为空 | passthrough |
| 任意异常 | passthrough（logged） |

`assigned_official` 校验（`_validate_assignments`）：不在合法 persona id 集合内 → 用 `OfficialSelector.select_for_task(description)` 按描述兜底，再不行落 `bingbu`。

## 5. 人工审批分叉

`handle_scheduled`（`planner.py:196`）产出 plan 后：

| `plan_review` | 行为 |
|---|---|
| true 且 tasks 非空 | memorial → `NEEDS_REVIEW`，emit `plan.pending_review`，**不触发执行** |
| 否则 | emit `plan.completed`，触发 Executor |

审批通过由 gateway（`/edicts/{id}/plan/approve`）落 `plan.approved` 记录后**补发** `plan.completed`（`fire`，不阻塞），memorial 回到 `PLANNING`。设计上 Planner 不等待审批 —— 审批是另一条 API 触发的事件，二者经 `plan.completed` 汇合。

## 6. 状态推进

memorial 状态在链路上逐步推进：`submitted/scheduled → PLANNING`（handle_scheduled 入口）→（审批分支）`NEEDS_REVIEW` →（通过）`PLANNING`。

**相关实现**：[../../impl/scheduling/](../../impl/scheduling/)
