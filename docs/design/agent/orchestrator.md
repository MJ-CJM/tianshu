# 长任务 Outer Loop

## 1. 设计意图

普通 ReAct 是「一次输出即终态」，无法保证产出真正达标。当 `Edict.acceptance` 非空时，Executor 进入 orchestrator outer loop：把执行变成 **actor → checks → critic → completion audit → 升级判断** 的外循环，反复优化直到验收通过或预算/迭代耗尽。核心设计判断：

- **验收契约前置**：`AcceptanceCriteria` 把「什么叫做完」写成可执行的 checks + critic 严苛度 + 升级策略，而非靠 LLM 自我感觉。
- **双重门禁**：critic pass 之后还要过 completion audit「覆盖审」，避免「critic 通过但目标漏项」的终态漏洞。
- **状态不可变**：`OuterLoopState`（frozen dataclass）每轮 `advance` 返回新对象，同类问题 streak、L1/L2 轮次计数、累计成本都在状态里，升级是纯函数决策。
- **软着陆**：预算/时间逼近上限时进入 `winding_down`，注入收尾 prompt 并拦截有副作用工具，避免硬切断留下半成品。

## 2. AcceptanceCriteria 契约

| 子对象 | 关键字段 | 作用 |
|---|---|---|
| `CheckSpec` | `kind`(bash/lint/rubric)、`command`、`rubric`、`pass_threshold` | 客观指标层 |
| `CriticSpec` | `persona_ids`、`model`、`same_issue_threshold`(默认2)、`strictness`(lenient/balanced/strict) | 监督官层 |
| `EscalationSpec` | `enabled_levels`、`l1_max_rounds`、`l2_max_rounds`、`l1_thinking_budget`、`l1_model_upgrade`、`l2_consultation_personas` | 升级策略 |
| 顶层 | `min_outer_iterations`、`max_outer_iterations`(默认5)、`deadline_seconds`、`on_exhaustion`、`on_critic_unavailable`、`on_approval_timeout` | 全局预算与兜底 |

### 2.1 兜底策略（三个 `on_*` 字段）

三个字段都是「正常路径失效时该退化成什么」的显式契约，避免外环遇到异常静默挂死或硬切断。语义见 `AcceptanceCriteria`(`models/acceptance.py`)与 `loop.py` 的 `_handle_exhaustion` / `_escalate_to_human`：

| 字段 | 取值 | 触发时机 | 行为 |
|---|---|---|---|
| `on_exhaustion` | `escalate`(默认) / `best_effort` / `fail` | `iteration >= max_outer_iterations`、`total_cost_cny >= cost_budget_cny`，或 L1/L2 轮次用尽且无更高 level 可升 → `decide_escalation` 返回 `EXHAUSTED` | `escalate`：升 L3 走人工审批（被 abort 才 FAILED，否则 COMPLETED）；`best_effort`：直接拿最后一轮产出 COMPLETED；`fail`：FAILED |
| `on_critic_unavailable` | `skip`(默认) / `escalate` | critic LLM 调用抛 `CriticUnavailable`（主+fallback 均失败） | `skip`：伪造一条 `verdict=pass` 记录放行，且本轮 completion audit 退化为 actor 自审（`audit_executor="actor_self_audit"`）；`escalate`：伪造 `verdict=fail, issue_class="other"` 走升级链 |
| `on_approval_timeout` | `best_effort`(默认) / `fail` | L3 等审批超时、无 `approvals` 注入、或 `wait_for_outer_loop_decision` 抛异常 | `best_effort`：等价 `accept_as_is`（COMPLETED）；`fail`：等价 `abort`（FAILED） |

注：`on_critic_unavailable=skip` 是「宁可放行也不卡死」的软容错——critic 不可用不等于产出不合格，故退化为 actor 自审而非直接判失败。

## 3. 单轮结构

```text
检查 pause / 预算 / lifecycle
  -> derive_actor_override（注入上轮 critic feedback / 会诊建议）
  -> actor Agent.execute
  -> run_checks（bash / lint / rubric 并发）
  -> critic review（checks 通过才进 critic）
  -> persist outer_loop_iterations
  -> completion audit（critic pass 后的覆盖审）
  -> pass / continue / escalate / exhaust
```

checks 不通过时，结果直接转 fail、issue_class=`checks_failed`，不进 critic。

## 4. checks 与 critic 与 audit

- **checks**（`run_checks`）：bash/lint 跑 subprocess 看 returncode；rubric 调 LLM 评分对比 `pass_threshold`。命令 not found 等配置错抛 `ChecksConfigError`，整个 outer loop abort。
- **critic**（`review`）：独立 LLM 调用，按 strictness 出结构化 `{verdict, issue_class, feedback, suggested_fix, improvement_hints}`。`issue_class` 限定在内置集合（factual_error / incomplete_coverage / structure_mismatch / …）。多 critic persona 时聚合。critic 不可用按 `on_critic_unavailable`（skip 退化为 actor 自审 / escalate）。
- **completion audit**（`run_completion_audit`）：critic pass 后的覆盖审，核实 acceptance 每条要求都有具体证据，产出 `AuditGap` 列表。不通过则 `format_gaps_for_continuation` 渲染成续转 prompt 进下一轮。

## 5. 升级路径 L0–L3

`decide_escalation` 是纯函数 FSM，按当前 level、同类问题 streak、轮次计数与硬上限决策：

| Level | 含义 |
|---|---|
| L0 | 基线：注入上轮 critic feedback 重试；同类问题 streak 达 `same_issue_threshold` 才升级 |
| L1 | 注入 feedback + 可配 `l1_thinking_budget` / `l1_model_upgrade`（actor override） |
| L2 | 触发 ConsultationSession，多 persona 会诊给 actor 建议 |
| L3 | 请求人工决策：`continue` / `accept_as_is` / `abort` / `modify_acceptance` |

硬上限：`iteration >= max_outer_iterations` 或累计成本超 `cost_budget_cny` → `EXHAUSTED`，按 `on_exhaustion`（escalate / best_effort / fail）兜底。L2 会诊失败自动降级到 L3。

### 5.1 FSM 转移规则（`decide_escalation`）

纯函数，输入 `(state, edict, acceptance, last_critic_passed)`，输出 `L0|L1|L2|L3|EXHAUSTED`。转移只由当前 `current_level` + `same_issue_streak` + `l1_rounds_used` / `l2_rounds_used` + 硬上限触发，与 history 无关：

```text
decide_escalation(state, edict, acceptance):
    if last_critic_passed: return current_level        # 哨兵，调用方此时应直接收工

    # 硬上限优先于一切升级
    if iteration >= max_outer_iterations:           return EXHAUSTED
    if cost_budget_cny set and total_cost >= budget: return EXHAUSTED

    enabled   = escalation.enabled_levels            # 默认 [L1,L2,L3]
    threshold = critic.same_issue_threshold          # 默认 2

    L0: if same_issue_streak >= threshold:           # 同类问题连犯够阈值才升
            升到 enabled 里第一个可用的 L1/L2/L3
        else stay L0                                 # 只注入上轮 feedback 原地重试
    L1: if l1_rounds_used >= l1_max_rounds: → L2/L3/EXHAUSTED(按 enabled)
        else stay L1
    L2: if l2_rounds_used >= l2_max_rounds: → L3/EXHAUSTED
        else stay L2
    L3: stay L3                                       # 不再升级，只能等审批
```

关键判断：**streak 是「同一 `issue_class` 连续打回」的计数**（`advance` 里 cur==last 才 +1，换类问题清 1），所以「换着花样犯错」不会触发升级，只有「同一处反复改不对」才升级——把升级预算花在真正卡住的地方。`enabled_levels` 缺某级时（如关掉 L1）直接跳到下一个开启的级。

### 5.2 feedback 注入链路（上轮喂下轮）

外环的「学习」全靠把上轮信号注入下一轮 actor 的 prompt，载体是 `OuterLoopState.consultation_advice` + `derive_actor_override`：

```text
上轮 critic.feedback / suggested_fix
  + L2 会诊 synthesis（_run_consultation）
  + completion audit gaps（format_gaps_for_continuation → CONTINUATION 模板）
  + 软着陆 wind-down prompt
  + L3 用户 continue/modify 反馈
        ↓ 都写进 state.consultation_advice
  derive_actor_override(state, edict) → override.extra_system_msg
        ↓
  augmented_content = goal + context + "## 上一轮反馈与建议\n{extra_system_msg}"
        ↓
  ctx.agent.execute(user_content=augmented_content)   # 下一轮 actor 带着反馈重跑
```

注意 `issue_class` 本身**不直接进 prompt**——它只驱动 FSM 升级决策（决定要不要加 thinking budget / 升模型 / 开会诊）；真正喂给 actor 的是 critic 的自然语言 `feedback`/`suggested_fix`。

### 5.3 L3 阻塞语义

L3 是唯一会**阻塞外环执行**的级。命中 L3（含 `on_exhaustion=escalate` 兜底进 L3）时，`_escalate_to_human` 发 `outer_loop.approval.requested` 事件 + 推送通知，然后 `await ctx.approvals.wait_for_outer_loop_decision(...)`，整个 `run()` 协程在此挂起，直到 ApprovalManager 收到 decree 回填 `HumanDecision`：

| `action` | `_apply_human_decision` 处理 | 结果 |
|---|---|---|
| `continue` | 回 L0，streak 清 0，feedback 注入下轮 | 继续循环 |
| `modify_acceptance` | 换 `edict.acceptance`，回 L0，streak/issue_class 清空，按新标准重跑 | 继续循环 |
| `accept_as_is` | — | 终态 COMPLETED（拿最后一轮产出） |
| `abort` | — | 终态 FAILED |

超时/无 approvals 注入时按 `on_approval_timeout` 退化（见 §2.1），不会无限挂起。

## 6. 预算、lifecycle 与软着陆

每轮前计算跨 token/cost/time 三维的 `usage_ratio`（取最大维度）：

| 阈值 | 行为 |
|---|---|
| ≥ `SOFT_LANDING_THRESHOLD`(0.9) 且 active | 转 `winding_down`，注入收尾 prompt |
| ≥ `HARD_LIMIT`(1.0) | 已 winding_down 则强制终止，否则先进 winding_down |

`winding_down` 阶段 ToolRegistry 拦截 `side_effect=True` 工具。lifecycle 合法转移：active↔paused、active→winding_down→complete、任意→complete（终态不可转出）。pause 状态下 checkpointed/background profile 保存 checkpoint 返回 `needs_review`。

## 7. Checkpoint 与续跑

### 7.1 Checkpoint scope

- **粒度**：per-edict。`OuterLoopCheckpoint`(`executor/checkpoint.py`，`KIND="outer_loop"`)序列化一整个 `OuterLoopState` 到 `outer_loop_checkpoints` 表，区别于 DAG node 级 `Checkpoint`。
- **启用条件**：仅 `execution_profile in (checkpointed, background)` 才存/读 checkpoint；默认 profile 不落盘、不可续跑。
- **存点时机**：每轮 FAIL 升级后、completion audit 未过续转前、持续优化续转前、进入 paused 入场时各存一次（`_save_checkpoint`）。
- **清点**：任意终态由 `_finalize_with_supervision` 调 `clear_outer_loop_checkpoint`，避免下次同 edict 误续。

### 7.2 Resume 语义

进程重启 / follow-up 重入 `run()` 时，`_load_checkpoint` 把 `state_dict` 还原成 `OuterLoopState` 从**当前 `current_level` 继续**——保留 `iteration` / `same_issue_streak` / `l1_rounds_used` / `l2_rounds_used` / `total_cost_cny`，发 `outer_loop.resumed` 事件后从该 level 接着跑，不重放已完成的迭代。

**关键取舍：`history` 不持久化**。`_state_from_dict` 显式 `history=()`——续跑后 `state.history` 为空。影响：依赖 `history[-1]` 取「最后一轮产出」的路径（如 L3 payload 的 `best_output`、exhaustion 的 `last_output`）在续跑首轮会取到 `None`，需先跑出一轮新 record 才有值。这是「状态可续、产出不留底」的有意设计——checkpoint 只为续跑决策，不当结果存储。

### 7.3 幂等边界（重要）

续跑**不是确定性重放**。`OuterLoopState.advance` 本身是纯函数，但外环每轮要调 actor LLM、critic LLM、completion audit，这些都是非确定性的：

- **L2 中途续跑会重跑会诊**：会诊建议 `consultation_advice` 不进 checkpoint，续跑后若仍在 L2 会**重新触发 `ConsultationSession`**，多 persona 重新出意见、重新计费，结果可能与中断前不同。
- **同一 `iteration` 不保证同一产出**：续跑后那一轮 actor/critic 重新调 LLM，输出与中断前那次无关联。
- **幂等的只有 FSM 决策**：给定相同 `OuterLoopState`，`decide_escalation` 必返相同 level；非确定性全部来自 LLM 调用与外部 IO（budget 快照、lifecycle 重读）。

因此 checkpoint 的安全使用前提是：**外环各轮副作用要么幂等、要么可接受重复**（会诊重计费、actor 重生成均属「可接受重复」）。

### 7.4 OuterLoopState 生命周期状态机

`OuterLoopState` 本身无 status 字段——外环的「状态」分两层：运行中的 `EdictRuntime.lifecycle_phase`（可变态，`lifecycle.py` 管转移）+ `run()` 返回的终态 `TaskStatus`。合并视角：

```text
        run() 入口（acceptance != None）
                 │  TaskStatus.SUBMITTED → RUNNING
                 ▼
        ┌──────────────┐  用户 pause     ┌──────────┐
        │  active      │ ───────────────▶│  paused  │  (lifecycle_phase)
        │  (RUNNING)   │ ◀─────────────── │          │  轮询 PAUSE_POLL
        └──────────────┘     resume       └────┬─────┘
           │      │                            │ 外部置 complete / 删除
           │      │ usage_ratio≥0.9            ▼
           │      ▼                       CANCELLED (终态)
           │  ┌──────────────┐
           │  │ winding_down │ 注入收尾 prompt + 拦截副作用工具
           │  └──────┬───────┘ 再超 1.0 → 强制 complete
           │         │
           ▼         ▼
   ┌─────────────────────────────────────────┐
   │  终态 TaskStatus（_finalize_with_supervision）│
   ├─────────────────────────────────────────┤
   │  COMPLETED  critic+audit 双过 / best_effort / accept_as_is │
   │  FAILED     exhaustion(fail) / abort / 预算耗尽 / checks 配置错 │
   │  CANCELLED  paused 期间被外部终结或删除                      │
   └─────────────────────────────────────────┘
```

> 说明：任务语境里的 **ESCALATED / EXHAUSTED 不是独立 `TaskStatus`**——它们是外环内部决策态（L3 升级 / `decide_escalation==EXHAUSTED`），最终都按 `on_exhaustion` / 审批结果**收敛到 COMPLETED 或 FAILED**。`TaskStatus` 实际枚举见 `models/common.py`（SUBMITTED/RUNNING/COMPLETED/FAILED/CANCELLED/…），外环只产出上表四种终态。`lifecycle_phase` 合法转移（`can_transition`）：active↔paused、active→winding_down→complete、任意→complete，complete 不可转出。

## 8. 监督报告

终态时若配置 critic persona，`generate_supervision_report` 产出 4 章节复盘（观察问题/做得好/做得不够/建议），按 `(memorial_id, persona_id)` 存储，避免 follow-up 间互相覆盖。LLM 失败吞异常、不阻塞终态返回。

**相关实现**：[../../impl/agent/](../../impl/agent/)
