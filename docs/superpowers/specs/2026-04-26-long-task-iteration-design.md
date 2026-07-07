# 长任务多轮迭代设计（Long-Task Outer Loop）

**Date**: 2026-04-26
**Status**: Spec — pending implementation plan
**Author**: emperor + 六部协商
**Related**: `docs/superpowers/specs/2026-04-02-agent-core-optimization-design.md` (current single-pass agent loop), `docs/superpowers/specs/2026-04-18-persona-growth-profile-design.md` (persona growth fuel)

## 1. 背景

当前 `executor → agent.run()` 是**单回合**模型：edict 派发 → agent 在内部跑最多 `max_iterations` 轮工具调用 → 返回结果 → edict 关闭。一次拍完。

这对短任务足够，但有两类场景失配：

1. **需要多次迭代才能收敛的任务**（写一份长报告、把测试覆盖率从 30% 拉到 80%、研究一个开放问题）—— 一回合内的工具调用已经把"下一步用什么工具"耗完了，但产出本身远未达标
2. **agent 自认为完成、实际不符合预期** —— 没人审就放过去了，让一个会出错的系统直接交付，质量无下限

朴素方案"每次让人审"在长任务场景下不可行：长任务可能跑几小时甚至跨天，每轮都打扰人 = 用 AI 没省力。

本设计提出**任务级 outer loop**：在现有 agent 单回合 loop 之上增加一层"指标 + critic + 阶梯升级"的编排，让长任务能自驱迭代到收敛，仅在必要时上报到人。

## 2. 已敲定的需求决策

| 维度 | 决策 |
|------|------|
| 判定"是否符合预期" | **指标先行 + critic agent 后置** —— 廉价确定性指标（pytest/lint/rubric）挡掉明显失败；critic 独立 LLM 做语义把关 |
| 时间尺度 | **edict 创建时声明 `execution_profile`**（foreground / checkpointed / background），runtime 按声明选择执行模型 |
| 人工介入 | **默认无人值守 + 阶梯升级 L0→L1→L2→L3 + web 可主动 pull**；critic 反复打回先升级计算（更强模型/会议），最后才打扰人 |
| 启用方式 | **显式声明 `AcceptanceCriteria` 才进入 outer loop 路径**；否则保留现有单回合行为，零回归 |
| 持久化 | **审计永久 + 中间产物 30 天归档 + 终态永久**；web timeline 1.0 留口子，先把数据存对 |

## 3. 架构

```
gateway / scheduler
        │
        ▼
   executor.py  ◀── 路由分流（按 edict.acceptance 是否为 None）
        │
        ├── 无 acceptance  ──▶  agent.run()           （现有路径，不变）
        │
        └── 有 acceptance  ──▶  orchestrator.run()    （新增路径）
                                       │
                                       ├─ outer-loop 状态机（L0→L1→L2→L3）
                                       │
                                       ├─ 每一轮内：
                                       │     1. actor   ←─ agent.run()           （复用）
                                       │     2. checks  ←─ checks/runner.py      （新增）
                                       │     3. critic  ←─ critic/agent.py       （新增）
                                       │
                                       ├─ 阶梯动作：
                                       │     L1: 升模型 / 加 thinking budget
                                       │     L2: consultation/ 模块跨部协商      （复用）
                                       │     L3: notifier 推送 + approvals       （复用）
                                       │
                                       └─ 每轮结束 checkpoint                    （复用）
```

### 3.1 模块边界

新增代码集中在 `src/tianshu/executor/orchestrator/` 子目录：

| 文件 | 职责 | 大致行数 |
|------|------|---------|
| `orchestrator/loop.py` | outer-loop 编排 + 主循环 | ~250 |
| `orchestrator/state.py` | OuterLoopState frozen dataclass | ~80 |
| `orchestrator/checks.py` | checks runner（bash / lint / rubric） | ~150 |
| `orchestrator/critic.py` | critic agent（独立 LLM + 结构化输出） | ~120 |
| `orchestrator/escalation.py` | L0→L3 升级决策纯函数 | ~150 |
| `models/acceptance.py` | AcceptanceCriteria pydantic 模型 | ~80 |

**复用、不修改**：`agent.py`、`checkpoint.py`、`approvals.py`、`auditor/`、`notifier/`、`consultation/`、`dag/`、`retry.py`、`cancel.py`。

### 3.2 核心设计原则

1. **职责单向流动**：orchestrator 调 actor，actor 不知 orchestrator 存在 —— actor 行为不因被编排而变化
2. **状态不可变**：`OuterLoopState` 是 `@dataclass(frozen=True)`，每轮替换不修改（与现有 `LoopState` 一致）
3. **向后兼容**：`executor.py` 路由分流，无 `acceptance` 字段的 edict 完全走老路径
4. **critic 独立上下文**：不复用 actor 的 messages 历史，避免被 actor 的"自圆其说"污染；critic 只看 actor 终态 + acceptance criteria

## 4. 数据模型

### 4.1 `AcceptanceCriteria`（新增 `src/tianshu/models/acceptance.py`）

```python
from typing import Literal
from pydantic import BaseModel, Field

class CheckSpec(BaseModel):
    """单条可验收指标。"""
    kind: Literal["bash", "lint", "rubric"] = "bash"
    name: str                                  # 展示名："pytest" / "ruff" / "rubric:tone"
    command: str | None = None                 # kind=bash/lint 时的 shell 命令
    rubric: str | None = None                  # kind=rubric 时的 LLM-judge 提示词
    weight: float = 1.0                        # rubric 评分加权
    pass_threshold: float = 0.8                # rubric 通过阈值
    timeout_seconds: int = 60

class CriticSpec(BaseModel):
    """critic agent 配置。"""
    persona_id: str | None = None              # None = 系统默认 critic persona
    model: str | None = None                   # 推荐略强于 actor
    same_issue_threshold: int = 2              # 同类问题累计 N 轮才升级

class EscalationSpec(BaseModel):
    """阶梯升级配置。"""
    enabled_levels: list[Literal["L1", "L2", "L3"]] = ["L1", "L2", "L3"]
    l1_max_rounds: int = 2
    l2_max_rounds: int = 1
    l1_thinking_budget: int = 8000
    l1_model_upgrade: str | None = None        # None = 不切，仅加 thinking
    l2_consultation_personas: list[str] = []   # 空 = 自动选

class AcceptanceCriteria(BaseModel):
    checks: list[CheckSpec] = Field(default_factory=list)
    critic: CriticSpec = Field(default_factory=CriticSpec)
    escalation: EscalationSpec = Field(default_factory=EscalationSpec)
    max_outer_iterations: int = 5
    deadline_seconds: int | None = None
    on_exhaustion: Literal["escalate", "best_effort", "fail"] = "escalate"
    on_critic_unavailable: Literal["escalate", "skip"] = "skip"
    on_approval_timeout: Literal["fail", "best_effort"] = "best_effort"
```

### 4.2 `Edict` 扩展（修改 `src/tianshu/models/edict.py`）

```python
class Edict(BaseModel):
    ...
    acceptance: AcceptanceCriteria | None = None
    execution_profile: Literal["foreground", "checkpointed", "background"] = "foreground"
```

`execution_profile` 三档语义：

| profile | runtime 行为 | checkpoint | 通知策略 |
|---------|-------------|-----------|---------|
| `foreground` | 同进程跑到底，崩了从头来 | 关闭 | 终态通知 |
| `checkpointed` | 每轮 checkpoint；进程崩了重启续 | 开启 | L3 触发 + 终态 |
| `background` | 提交即返回，worker 后台跑；用户可关 web | 强制开启 | L3 + 每轮简报 + 终态 |

### 4.3 `OuterLoopState`（运行时不可变状态）

```python
# src/tianshu/executor/orchestrator/state.py
from dataclasses import dataclass, field
from typing import Literal
from datetime import datetime

Level = Literal["L0", "L1", "L2", "L3"]

@dataclass(frozen=True)
class CheckOutcome:
    name: str
    passed: bool
    detail: str | None = None                  # 失败时填 stderr / rubric reasoning
    score: float | None = None                 # rubric 评分；bash/lint 不填
    duration_ms: int = 0

@dataclass(frozen=True)
class ChecksResult:
    all_passed: bool
    outcomes: tuple[CheckOutcome, ...]

@dataclass(frozen=True)
class IterationRecord:
    iteration: int
    level: Level
    actor_output: str
    checks_result: ChecksResult
    critic_result: "CriticResult | None"
    started_at: datetime
    finished_at: datetime
    cost_cny: float

@dataclass(frozen=True)
class OuterLoopState:
    edict_id: str
    iteration: int = 0
    current_level: Level = "L0"
    same_issue_streak: int = 0
    last_critic_issue_class: str | None = None
    l1_rounds_used: int = 0
    l2_rounds_used: int = 0
    consultation_advice: str | None = None     # L2 协商产物，注入下一轮 actor system prompt
    history: tuple[IterationRecord, ...] = field(default_factory=tuple)
    total_cost_cny: float = 0.0

    def advance(self, record: IterationRecord) -> "OuterLoopState":
        """返回新实例 — never mutate。具体字段更新策略写在实现里。"""

    def with_level(self, level: Level) -> "OuterLoopState": ...
    def with_consultation_advice(self, advice: str) -> "OuterLoopState": ...
```

### 4.4 持久化

新增表 `outer_loop_iterations`（中间产物专用，与 `memorial` 事件流分离）：

```sql
CREATE TABLE outer_loop_iterations (
    id              TEXT PRIMARY KEY,           -- ULID
    edict_id        TEXT NOT NULL,
    iteration       INTEGER NOT NULL,
    level           TEXT NOT NULL,              -- L0/L1/L2/L3
    actor_output    TEXT,                       -- 30 天后归档置空
    checks_result   JSON,
    critic_result   JSON,
    cost_cny        REAL DEFAULT 0,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP NOT NULL,
    archived_at     TIMESTAMP,                  -- 归档标记
    FOREIGN KEY (edict_id) REFERENCES edicts(id) ON DELETE CASCADE,
    UNIQUE (edict_id, iteration)                -- 幂等性 + resume 防重写
);

CREATE INDEX idx_outer_loop_edict ON outer_loop_iterations(edict_id, iteration);
CREATE INDEX idx_outer_loop_archive ON outer_loop_iterations(finished_at) WHERE archived_at IS NULL;
```

**审计事件**（写入现有 `memorial` 表，不动 schema）：

- `outer_loop.started`
- `outer_loop.iteration.started` / `outer_loop.iteration.finished`
- `outer_loop.checks.passed` / `outer_loop.checks.failed`
- `outer_loop.critic.passed` / `outer_loop.critic.rejected`
- `outer_loop.escalated`（含 from / to / reason）
- `outer_loop.completed` / `outer_loop.exhausted`
- `outer_loop.approval.requested` / `outer_loop.approval.received`

### 4.5 归档策略

后台日级任务扫 `outer_loop_iterations`：
- `WHERE finished_at < now() - interval '30 days' AND archived_at IS NULL`
- `actor_output` 置 NULL，写 `archived_at = now()`
- checks/critic 摘要 + 成本永久保留（emperor 个性化训练的廉价信号）

## 5. 状态机 + outer loop 算法

### 5.1 阶梯升级状态机

```
                      ┌──────────────────────────────────────────┐
                      │ checks PASS && critic PASS               │
                      ▼                                          │
                ┌──────────┐                                     │
       start ──▶│   L0     │── critic FAIL ──┬─ 不同类问题 ──────┘
                │ 常规迭代 │                  │
                └──────────┘                  ▼
                      │            累计 ≥ same_issue_threshold
                      │                       │
                      ▼                       ▼
              outer_iter > max?         ┌──────────┐
                  yes / no              │   L1     │── 仍 FAIL ──┐
                      │                 │ 加 thinking│            │
                      └─ yes ─▶ exhausted│ / 升模型  │            ▼
                                        └──────────┘     ┌──────────┐
                                                         │   L2     │── 仍 FAIL ──┐
                                                         │ 跨部协商 │             │
                                                         └──────────┘             ▼
                                                                       ┌──────────┐
                                                                       │   L3     │
                                                                       │ 推送+审批│
                                                                       └──────────┘
```

### 5.2 升级触发规则

实现在 `escalation.decide_escalation()` 纯函数：

| 当前 level | 触发条件 | 下一 level |
|-----------|---------|----------|
| L0 | `same_issue_streak ≥ critic.same_issue_threshold` | L1 |
| L1 | `l1_rounds_used ≥ escalation.l1_max_rounds` 且仍 FAIL | L2 |
| L2 | `l2_rounds_used ≥ escalation.l2_max_rounds` 且仍 FAIL | L3 |
| 任意 | `iteration ≥ max_outer_iterations` | EXHAUSTED |
| 任意 | `total_cost_cny ≥ edict.runtime.cost_budget_cny` | EXHAUSTED |
| 任意 | `now - started_at ≥ deadline_seconds` | EXHAUSTED |

EXHAUSTED 时按 `on_exhaustion` 字段决定：
- `escalate` → 转 L3 上报
- `best_effort` → 当前最佳 actor_output 作为终态
- `fail` → edict 失败终止

critic PASS → 立即退出（无论当前 level）。

### 5.3 "同类问题"识别

critic 返回结构化结果时强制带 `issue_class`：

```python
class CriticResult(BaseModel):
    verdict: Literal["pass", "fail"]
    issue_class: str | None = None             # FAIL 时必填
    feedback: str
    suggested_fix: str | None = None
```

orchestrator 比较：
- `current.issue_class == previous.issue_class` → `same_issue_streak += 1`，可能触发 L1
- 不同 → `same_issue_streak = 1`（重置；actor 改对一个问题又冒出新的，是正常迭代）

`issue_class` 用项目预定义有限集合（先内置一份，后续可配置），critic 的 system prompt 强约束在该集合内选择，避免每次随机生成新标签导致永远不"同类"。

预定义集合（v1）：
```
factual_error              事实性错误
tone_mismatch              语气/风格与目标不符
incomplete_coverage        覆盖不全（漏点 / 漏分支）
structure_mismatch         结构与要求不符
formatting_violation       格式问题
checks_failed              指标层失败（不进 critic）
other                      未分类
```

### 5.4 outer loop 主循环（伪代码）

```python
async def run(edict: Edict, ctx: ExecutorContext) -> EdictResult:
    state = OuterLoopState(edict_id=edict.id)
    acceptance = edict.acceptance

    while state.iteration < acceptance.max_outer_iterations:
        # 1. 预算/超时检查
        if budget_or_deadline_exceeded(state, edict):
            return await handle_exhaustion(state, edict)

        # 2. actor: 跑一回合（复用现有 agent.run）
        actor_cfg = derive_actor_config(state.current_level, edict, acceptance)
        actor_output = await agent.run(edict, ctx, override_cfg=actor_cfg)

        # 3. checks
        checks_result = await run_checks(acceptance.checks, actor_output, ctx)
        if not checks_result.all_passed:
            state = state.advance(record=..., issue_class="checks_failed")
            new_level = decide_escalation(state, acceptance)
            state = state.with_level(new_level)
            await emit("outer_loop.checks.failed", ...)
            await checkpoint.save(state)
            continue

        # 4. critic
        critic_result = await critic.review(actor_output, edict, acceptance.critic)

        record = IterationRecord(...)
        await persist_iteration(record)

        if critic_result.verdict == "pass":
            await emit("outer_loop.completed", ...)
            return EdictResult(output=actor_output, ...)

        # 5. critic FAIL → 升级判断
        state = state.advance(record=record, critic_result=critic_result)
        new_level = decide_escalation(state, acceptance)

        if new_level == "L3":
            state = state.with_level("L3")
            await escalate_to_human(state, edict, ctx)
            decision = await approvals.wait(...)
            state = apply_human_decision(state, decision)
            if decision.action == "abort":
                return EdictResult(failed=True, ...)
            if decision.action == "accept_as_is":
                return EdictResult(output=actor_output, ...)

        elif new_level != state.current_level:
            state = state.with_level(new_level)
            await emit("outer_loop.escalated", from_=..., to=new_level)
            if new_level == "L2":
                consultation_advice = await consultation.convene(...)
                state = state.with_consultation_advice(consultation_advice)

        await checkpoint.save(state)

    return await handle_exhaustion(state, edict)
```

## 6. 错误处理 / 中断 / 恢复

### 6.1 错误分类

| 错误源 | 处理 |
|-------|------|
| **actor 异常** | 复用 `retry.py` 重试；耗尽后视为 actor_output = 错误摘要，进 checks（必失败），走升级 |
| **checks 进程错（exit ≠ 0）** | check FAIL；正常进升级流程 |
| **checks 配置错（command not found / sandbox 拒绝）** | 整个 outer loop **立即 abort**，写 audit；不重试（配置错重试无意义） |
| **critic LLM 故障** | 内部重试 2 次 + 备用模型 fallback；仍失败 = 视 critic 弃权，按 `on_critic_unavailable` 处理（默认 `skip`） |
| **consultation 异常** | 跳过 L2 直接升 L3；写 audit |
| **approval 超时** | 复用 `approvals.py` timeout；按 `on_approval_timeout`（默认 `best_effort`） |
| **预算/截止超限** | 立即停 outer loop，按 `on_exhaustion` |

**critic 故障默认 skip 的取舍**：长任务经不起噪声故障打断，这条让 critic 故障不会级联成业务故障。代价是该轮可能漏放劣质产出 —— 由 `max_outer_iterations` 兜底，下一轮 critic 恢复后仍能拦。

### 6.2 中断机制

| 中断源 | 触发 | 行为 |
|-------|------|------|
| 用户主动 cancel | web/cli `cancel(edict_id)` | 复用 `cancel.py`，下次 checkpoint 处停止；保留 state + history |
| 进程崩溃 | OOM / kill | `checkpointed`/`background` 自动从最近 checkpoint 续；`foreground` 整个失败 |
| SIGTERM | 系统关闭 | graceful shutdown：当前轮跑完 → 写 checkpoint → 退出 |

### 6.3 Resume

每轮结束写 checkpoint（复用 `checkpoint.py`，新增 payload kind）：

```python
@dataclass(frozen=True)
class OuterLoopCheckpoint:
    kind: Literal["outer_loop"] = "outer_loop"
    edict_id: str
    state: OuterLoopState
    last_iteration_id: str
    saved_at: datetime
```

worker 启动时扫所有"未完成且有 checkpoint"的 edict：
- 读 checkpoint → 重建 `OuterLoopState`
- 直接进主循环，从 `state.iteration` 继续
- 不重跑已完成轮次（`outer_loop_iterations` 已落库）

幂等性：
- 主键 ULID 单调，重建时不冲突
- `(edict_id, iteration)` 唯一约束，重复写报错被吞

### 6.4 资源 / 死循环防护

| 隐患 | 防护 |
|-----|------|
| critic 把 issue_class 报成不同标签永远不"同类" | `max_outer_iterations` + `cost_budget_cny` + `deadline_seconds` 三道兜底 |
| actor 在某轮调到死循环工具 | 复用 `agent.py` 内层 `max_iterations` |
| L2 consultation 内部又触发新 edict | 硬约束：consultation 仅返回建议文本，不创建 edict |
| 同一 edict 多 worker 并行 resume | DB 行锁 `SELECT ... FOR UPDATE` on edict 状态字段；获不到锁的 worker 让出（注：现有部署单机，单 worker 行锁足够；多机部署时需升级到 advisory lock，2.0 议题） |

### 6.5 审批分离

L3 推送时审批选项**结构化**（不让人写自由文本，便于 orchestrator 自动衔接）：

```python
class HumanDecision(BaseModel):
    action: Literal["continue", "accept_as_is", "abort", "modify_acceptance"]
    feedback: str | None = None
    new_acceptance: AcceptanceCriteria | None = None
```

四种路径：

| action | 行为 |
|--------|------|
| `continue` | feedback 注入 actor system prompt；streak 重置；回 L0 |
| `accept_as_is` | 当前 actor_output 收工 |
| `abort` | edict 失败终止 |
| `modify_acceptance` | 用户在线放宽/收紧标准（如测试覆盖率从 80→70）；回 L0 重跑 |

`modify_acceptance` 是逃生口 —— 长任务跑了几轮发现当初标准过严，用户可现场调，不用整体重启。

## 7. 测试策略

### 7.1 测试分层

| 层 | 范围 | 工具 |
|---|------|------|
| **单元** | `escalation.decide()` 纯函数、`OuterLoopState.advance()` 不可变更新、`CheckSpec` 解析、`CriticResult` schema 校验 | pytest |
| **集成 (mock LLM)** | orchestrator 完整 outer loop，mock actor/critic | pytest + fakes |
| **集成 (真 LLM)** | 端到端跑真实长任务（"写一份 500 字摘要"） | pytest `@pytest.mark.live`，CI 不跑 |
| **回归** | 不带 `acceptance` 的 edict 走老路径 | 现有测试套保持绿 |

### 7.2 关键单元测试用例

`escalation.decide_escalation()`：

```python
@pytest.mark.parametrize("state, acceptance, expected", [
    (state(level="L0", streak=1), accept(threshold=2), "L0"),
    (state(level="L0", streak=2), accept(threshold=2), "L1"),
    (state(level="L1", l1_rounds=2), accept(l1_max=2), "L2"),
    (state(level="L2", l2_rounds=1), accept(l2_max=1), "L3"),
    (state(iteration=5), accept(max_outer=5), "EXHAUSTED"),
    (state(level="L0", streak=2), accept(enabled=["L2","L3"]), "L2"),  # L1 关闭
])
def test_decide_escalation(state, acceptance, expected): ...
```

### 7.3 集成测试关键用例

```
test_outer_loop_pass_first_try         # critic 一次过
test_outer_loop_l0_to_l1_same_issue    # 同 issue_class → 升 L1
test_outer_loop_l0_streak_resets       # 不同 issue_class → streak 重置
test_outer_loop_full_ladder_to_l3      # 一路升到 L3 + approval=continue
test_outer_loop_approval_accept_as_is
test_outer_loop_approval_abort
test_outer_loop_critic_failure_skip    # critic 挂 → skip 当 PASS
test_outer_loop_checks_failed          # checks 不过 → 跳 critic 直接升级
test_outer_loop_resume_from_checkpoint
test_outer_loop_exhausted_best_effort
test_outer_loop_no_acceptance_old_path # 零回归
test_outer_loop_modify_acceptance
```

### 7.4 可观测性

**实时事件**（走现有 SSE 流）：
- `outer_loop.iteration.started` / `outer_loop.iteration.finished`
- `outer_loop.checks.{passed,failed}` + 失败 check 名
- `outer_loop.critic.{passed,rejected}` + issue_class
- `outer_loop.escalated` from→to + 原因
- `outer_loop.approval.requested` / `outer_loop.approval.received`

**汇总指标**（每个 edict 完成时挂到 `edicts.metadata` JSON 字段，v1 不开新表）：
- `final_level`, `total_iterations`, `total_cost_cny`, `wall_clock_seconds`, `escalation_count`
- 后续 emperor 个性化分析的廉价数据源；如查询负载变重再考虑独立 `outer_loop_summaries` 表（v2 议题）

**web 入口（1.0 留口子）**：
- edict 详情页加 `accepted` tag，点开 timeline
- timeline 每行 = 一次 iteration（level / verdict / 耗时 / 成本）
- 展开看 actor_output diff vs 上一轮 + critic feedback

### 7.5 性能 / 成本

- critic 用独立 LLM，每轮 +1 次推理；目标控制在 actor 单次成本的 ~20-30%（critic prompt 短，只看终态 + acceptance）
- L1 升 thinking budget 是显著加贵 → `max_outer_iterations` + `cost_budget_cny` 兜底
- 总成本上界 ≈ `(actor + critic) × max_outer + L1_premium + L2_consultation_premium`

## 8. 验收标准（本设计落地的）

- [x] 不带 `acceptance` 的 edict 行为与回归前完全一致（现有测试套全绿）
- [x] 带 `acceptance` 的 edict 能跑通 §7.3 列出的所有集成测试
- [x] 单元测试覆盖率 ≥ 80%（项目硬指标）
- [x] `outer_loop_iterations` 表有 30 天归档任务且能回放
- [x] L3 推送能到达通知通道，approval 四路径（continue / accept_as_is / abort / modify_acceptance）均可触达

## 9. 显式不做（v1 范围外）

- web timeline 可视化前端（仅留数据模型口子）
- 多机部署的分布式锁（advisory lock / Redis lock）—— 当前单机 DB 行锁够用
- consultation 内创建子 edict —— 硬约束禁止，避免无限递归
- 自动判别"任务是否需要 outer loop" —— 强制显式声明 `acceptance`
- critic 多模型集成投票 —— 单 critic 起步
- 自定义 `issue_class` 集合的运行时配置 —— v1 内置预定义集合

## 10. 演进路线（v2+）

- web timeline + diff viewer
- 多机分布式锁
- critic 集成（多 critic 投票）
- L2 consultation 后允许派发子 edict（受限沙箱）
- `issue_class` 集合可配置 + 项目级模板
- `acceptance_template` 预置（"研究报告型" / "代码实现型" / "数据分析型"）

## 11. 关联文档

- `docs/superpowers/specs/2026-04-02-agent-core-optimization-design.md` — 现有 agent loop 设计基础
- `docs/superpowers/specs/2026-04-18-persona-growth-profile-design.md` — 中间产物归档后用作 emperor 个性化训练的数据源
- `src/tianshu/executor/agent.py` — 现有 actor 实现，本设计复用
- `src/tianshu/executor/checkpoint.py` — 现有 checkpoint，本设计扩展 payload kind
- `src/tianshu/executor/approvals.py` — 现有审批机制，L3 直接复用
- `src/tianshu/consultation/` — 跨部协商，L2 直接复用
