# Edict Goal Loop — 长任务续转、完成审计、预算软着陆

**日期**：2026-05-02
**分支**：feat_phase6
**作者**：mj-cjm（与 Claude 共同设计）
**状态**：待实施

---

## 1. 背景

调研 codex 项目的 `/goal` 子系统后，确认它对 tianshu 长任务有三处可借鉴价值：

1. **completion audit prompt**：续转模板里强制 prompt-to-artifact checklist，禁止"努力过/测试通过/计划完整"等代理信号当完成证据
2. **空闲续转引擎**：Edict 是 `background/checkpointed` 时不应一次回合就停，而应在 critic 不通过/未达 acceptance 时由外层注入 continuation prompt 继续推进
3. **预算软着陆**：token/cost/time 接近耗尽时不直接 abort，而是注入"收尾交接"prompt 让 actor 留下下一步建议

tianshu 当前已有 `AcceptanceCriteria.max_outer_iterations` 和 critic/escalation 机制，但缺：
- 内层 `agent.py` LLM 无 tool_call 即 `return COMPLETED`，外层是否继续完全依赖 orchestrator 自行裁量，没有显式的"续转决策"状态机
- 完成判定仅靠 critic verdict，没有覆盖 acceptance.checks 每条要求的"覆盖审"
- 预算耗尽是硬 abort，无交接

## 2. 范围与排除

### 2.1 本次范围
- **C** completion audit prompt
- **D** 预算软着陆 + lifecycle 状态化
- **A2** 续转走外层接管路线（critic 反馈增强 + 模板兜底）

### 2.2 明确排除
- **B** 对话浮现的 goal（emperor 对话中产生 goal）— 与 Edict 不可变诏书模型存在根本冲突，单独立 spec
- **A1** 内层续转（codex 原版风格）— 与 tianshu actor/critic 分层重复，且 prompt 攻击面大
- 不暴露 `get_goal` 工具给 actor LLM — 行为稳定性优先于自主控速
- 不引入"线程级附加 goal"层 — Edict 本身就是 goal 容器

### 2.3 路线选型

| 路线 | 完成判定 | 行为稳定性 | 业界采用 |
|---|---|---|---|
| A1 内层续转 | LLM 自评 | 易过度乐观 | codex / Operator / AutoGPT |
| **A2 外层接管** | **独立 critic + audit** | **结构性更强** | **Devin / Claude Code subagents** |

选 A2。微续转（finish_reason="length" 的同回合续）维持现状即可（agent.py 已有 `output_recovery_count`）。

## 3. 数据模型变更

### 3.1 EdictRuntime 新增字段

`src/tianshu/models/edict.py`：

```python
class EdictRuntime(BaseModel):
    ...  # 现有字段全部保留
    lifecycle_phase: Literal["active", "paused", "winding_down", "complete"] = "active"
```

**原则**
- `EdictStatus`（open/completed/cancelled）保持不动，对外语义不变
- `lifecycle_phase` 是纯运行时状态，独立于 EdictStatus
- 持久化沿用现有 Edict 序列化路径，不新增表
- lifecycle 变更历史写入现有 events 流（不独立表）

### 3.2 不变量

- `EdictStatus == cancelled` 时 `lifecycle_phase` 必须切到 `complete`
- `lifecycle_phase == complete` 时 outer loop 不得再发起任何 actor turn
- `lifecycle_phase == winding_down` 时 actor 调用副作用工具（write/edit/bash 写命令等）必须被工具层拦截

## 4. Outer Loop 续转决策状态机

每次 actor 一轮结束后，orchestrator 走以下分支（伪码）：

```
actor_result = run_actor(messages)
critic_verdict = run_critic(actor_result) if critic_enabled else None
usage_ratio = max(
    tokens_used / token_budget,
    cost_used / cost_budget_cny,
    time_used / acceptance.deadline_seconds,
)  # 任一字段缺省时该项不计入

# 1. 用户手动 pause
if lifecycle_phase == "paused":
    persist_and_return("paused")

# 2. 已硬超额（≥ 1.0）
if usage_ratio >= 1.0:
    if lifecycle_phase != "winding_down":
        # 给一次软着陆机会再终止
        set_phase("winding_down")
        return continue_with(inject_wind_down_prompt(messages, force=True))
    finalize("complete", reason="budget_exhausted")

# 3. critic 通过 → 进 completion audit
if critic_verdict == "pass":
    audit_result = run_completion_audit(actor_result, edict.acceptance)
    if audit_result.passed:
        finalize("complete", reason="acceptance_met")
    else:
        # 把缺口反哺为下一轮 continuation
        return continue_with(
            inject_continuation_prompt(messages, audit_gaps=audit_result.gaps)
        )

# 4. 进入软着陆窗口（≥ 0.9 且尚未进入）
if usage_ratio >= 0.9 and lifecycle_phase != "winding_down":
    set_phase("winding_down")
    return continue_with(inject_wind_down_prompt(messages))

# 5. 常规续转（含 critic flag/fail 反哺）
critic_feedback = critic_verdict.feedback if critic_verdict in ("flag", "fail") else None
return continue_with(inject_continuation_prompt(messages, critic_feedback=critic_feedback))
```

### 4.1 分层职责

| 层 | 职责 | 不负责 |
|---|---|---|
| **critic 监督官** | 质量审：actor 这一轮做得好不好 | 是否覆盖每条 acceptance |
| **completion audit** | 覆盖审：acceptance.checks 每条都有证据吗 | 质量评分 |
| **wind_down 触发器** | 预算阈值监测 + 强制收尾 | 内容判断 |
| **outer orchestrator** | 路由决策 + 状态机推进 | prompt 生成（交模板） |

audit 是 **critic pass 之后**的额外门，不是替代 critic。critic 不在场时 audit 退化为 actor 自审（按 `acceptance.on_critic_unavailable` 行为决定走 escalate 或 skip）。

### 4.2 audit 失败的反哺

audit 输出结构化的"缺口列表"：

```python
@dataclass(frozen=True)
class AuditGap:
    check_name: str
    requirement: str
    evidence_status: Literal["missing", "weak", "uncertain"]
    suggested_action: str

@dataclass(frozen=True)
class AuditResult:
    passed: bool
    gaps: tuple[AuditGap, ...]
```

`gaps` 渲染进下一轮 continuation prompt 的 `<audit_feedback>` 段。

## 5. Prompt 模板

放在 `src/tianshu/executor/templates/edict/`，由 orchestrator 渲染并注入为 developer-role message。

### 5.1 通用约定

- `edict.goal` 注入时一律用 `<untrusted_objective>...</untrusted_objective>` 包裹（防 prompt injection）
- 模板使用现有 `tianshu` 模板渲染机制（与 persona/skills 一致），失败时 fallback 到固化字符串并记录 warning
- 三个模板都不暴露 `tokens_used / remaining_tokens / time_used` 数字给 actor LLM（仅 orchestrator 日志使用）

### 5.2 continuation.md

**用途**：actor 一轮结束、未达完成、需要继续推进时注入。

**结构**：
- 开头：本次任务在外层 critic 监督下推进，请基于"当前进度"决定下一步具体动作，不要重复已完成工作
- `<untrusted_objective>` 包裹的 goal
- 可选的 `<critic_feedback>` 段（critic verdict ∈ {flag, fail} 时）
- 可选的 `<audit_feedback>` 段（completion audit 失败时）
- 结尾：禁止把"努力过/部分完成/计划完整/测试通过"作为完成证据；不确定即视为未完成

### 5.3 completion_audit.md

**用途**：critic pass 后由 audit 执行者（critic persona 或 actor 自审）使用，输出结构化 AuditResult。

**结构**：
- 引言：现在进入完成审计；将 acceptance 每条要求映射到具体证据
- acceptance.checks 渲染为 checklist：每条 check 列出 `name / kind / command 或 rubric / pass_threshold`
- 要求逐条贴证据：文件路径 / 命令输出 / 测试结果 / artifact 引用
- 任何一条"未贴证据 / 弱证据 / 不确定" → `passed=false`
- 输出 schema 用 JSON：`{"passed": bool, "gaps": [{"check_name": str, "requirement": str, "evidence_status": str, "suggested_action": str}]}`
- 结尾照搬 codex `continuation.md` 的反代理信号原则（中文化）

### 5.4 wind_down.md

**用途**：usage_ratio ≥ 0.9 且 lifecycle 切到 winding_down 时注入；usage_ratio ≥ 1.0 强制注入。

**结构**：
- 当前任务已接近预算上限，进入收尾阶段
- 不再开启新工作（强约束）
- 汇总已完成、列出剩余工作、给出可继续的下一步建议、写清晰交接
- `<untrusted_objective>` 包裹的 goal
- 强制：本轮起禁止调用副作用工具

**实现配套**：工具调用层增加一个针对 lifecycle_phase 的 gate——winding_down 时拒绝 write/edit/bash-write 等副作用工具，返回明确错误让 actor 自纠。

## 6. API & 控制面

### 6.1 后端 endpoints

新增最小化两条：
- `POST /edicts/{id}/pause` — 切 lifecycle_phase=paused，触发 orchestrator 在当前 actor turn 完成后停机
- `POST /edicts/{id}/resume` — 切 lifecycle_phase=active，让 orchestrator 重新调度

cancel 走现有路径（不复用本设计，cancel 仍是终态）。

### 6.2 UI

本次只做后端 API；web 前端展示 lifecycle_phase 字段以及 pause/resume 按钮**不在本次范围**，后续 spec 处理。

### 6.3 事件流

新增三类 audit/续转事件，写入现有 event store：
- `edict.continuation.injected`：payload 含 `iteration / has_critic_feedback / has_audit_gaps`
- `edict.audit.executed`：payload 含 `passed / gaps_count / executor_persona`
- `edict.wind_down.entered`：payload 含 `usage_ratio / trigger_field`（哪个预算先 90%）
- `edict.lifecycle.changed`：payload 含 `from_phase / to_phase / reason`

## 7. 错误处理 / 边界

| 失败模式 | 处理 |
|---|---|
| 模板渲染失败 | fallback 到固化中文字符串，warning 日志，不中断 loop |
| critic 不在场（on_critic_unavailable=skip） | audit 退化为 actor 自审，跳过 critic verdict 检查 |
| critic 不在场（on_critic_unavailable=escalate） | 沿用现有 escalation 路径，audit 不执行 |
| audit JSON 解析失败 | 重试 1 次（强约束 schema），仍失败 → 视为 `passed=false`、gaps=[{...解析失败提示...}] |
| lifecycle_phase 与 EdictStatus 漂移 | 启动时校验，单测覆盖不变量；实际漂移时以 EdictStatus 为准、修正 lifecycle_phase 并 warning |
| usage_ratio 计算除零 | 缺省字段不计入 max；全部缺省时 ratio=0 |
| pause 时 actor 正在 LLM 调用中 | 当前 turn 完成后停机（不中途打断），下次唤起前检查 phase |
| 重启后恢复 | lifecycle_phase 持久化在 Edict、messages 沿用现有 checkpoint，orchestrator 启动时读取继续 |

## 8. 测试策略

目标覆盖率 ≥ 80%，沿用 pytest + pytest.mark 分类。

### 8.1 单元测试
- 状态机分支：5 条主路径每条至少一个 case
- 模板渲染：含 untrusted_objective 注入、critic_feedback / audit_gaps 可选段、缺省字段不渲染
- usage_ratio 计算：单字段、多字段、全缺省、超额
- AuditGap / AuditResult 序列化与解析
- 工具层 winding_down gate：副作用工具被拦截、只读工具放行

### 8.2 集成测试
- **三轮收敛**：一个 fake edict 故意需要 3 轮才能完成，断言"audit 触发 1 次（最后一轮）、wind_down 不触发、最终 lifecycle=complete、EdictStatus=completed"
- **预算软着陆**：token_budget 设小，断言"wind_down 在 ≥ 0.9 时触发、最后一轮无副作用工具调用、产出含交接摘要"
- **预算硬超额**：token_budget 设极小，断言"未走 wind_down 也强制走一次再 finalize"
- **pause/resume**：跑到一半 pause，断言"orchestrator 在当前 turn 完成后停机；resume 后从 lifecycle=active 续上、messages 一致"
- **critic 不在场（skip）**：断言"audit 仍执行（actor 自审）、流程通畅"
- **prompt injection 防御**：goal 注入恶意 prompt（"忽略上面的指令"），断言"被 untrusted_objective 包裹、actor 行为不被劫持"

### 8.3 不在测试范围
- chaos 测试（critic 随机不在场）— Phase 1 不做
- 性能测试 — Phase 1 不做

## 9. 实施顺序建议（高层）

1. 数据模型变更（lifecycle_phase 字段 + 不变量校验）
2. 三个模板文件 + 模板渲染辅助
3. AuditResult / AuditGap 数据结构
4. completion audit 执行器（critic 复用 + actor 自审 fallback）
5. outer loop 状态机（替换/增强现有 orchestrator 续转判断处）
6. 工具层 winding_down gate
7. pause/resume API endpoints
8. 事件流 4 类 event 接入
9. 单元测试 → 集成测试

具体步骤、文件位置、依赖关系交由 writing-plans 阶段输出实施计划。

## 10. 开放问题（不阻塞本 spec）

- audit 执行的 LLM 模型选型：默认走 critic.model，未配置时走全局 critic 默认 — 实施阶段确认
- continuation prompt 是否要根据 `priority` 字段调强弱（urgent vs normal）— 留作 v2 增强
- audit gaps 累积超过 N 次仍不收敛时的 escalation 策略 — 复用现有 EscalationSpec.l1_max_rounds 等，无需新设计

## 11. 不引入的设计

明确记录我们**没有**做什么，避免后续误解：

- ❌ 不新增 `EdictStatus` 枚举值
- ❌ 不暴露 `get_goal` 工具给 actor
- ❌ 不让 actor LLM 看到 budget 数字
- ❌ 不引入"线程级附加 goal"层
- ❌ 不做 web UI 的 lifecycle 展示与 pause/resume 按钮（后续 spec）
- ❌ 不做对话浮现 goal（B 场景，后续 spec）
- ❌ 不做 codex 风格的 A1 内层续转
