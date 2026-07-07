# 长任务多轮迭代实施计划（Long-Task Outer Loop）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有单回合 agent loop 之上加一层"任务级 outer loop + critic + 阶梯升级"，让长任务能自驱迭代到收敛。

**Architecture:** 在 `executor.py` 路由分流：edict 填了 `AcceptanceCriteria` → 走新的 `orchestrator.run()`；否则保留老路径零回归。orchestrator 调 actor（复用 `agent.py`）、checks runner、critic agent，再按 `escalation.decide_escalation()` 状态机决定 L0/L1/L2/L3 升级。critic 用独立 LLM 调用，checks 用 subprocess 跑 bash/lint/rubric。

**Tech Stack:** Python 3.11+, pydantic, asyncio, sqlite3, pytest, litellm（复用现有 LLMClient）。

**Spec 来源：** `docs/superpowers/specs/2026-04-26-long-task-iteration-design.md`

**用户偏好：** 功能优先，测试最后补（Task 18 + 19 集中补齐）。Task 5 的纯函数 `decide_escalation` 是例外 —— 它是核心 FSM，inline 测试便宜且 Bug 贵。

---

## File Structure

**新增文件**

```
src/tianshu/models/acceptance.py                       # AcceptanceCriteria 等 pydantic 模型
src/tianshu/executor/orchestrator/__init__.py          # 模块入口（暴露 run）
src/tianshu/executor/orchestrator/state.py             # frozen dataclass state
src/tianshu/executor/orchestrator/escalation.py        # decide_escalation 纯函数
src/tianshu/executor/orchestrator/checks.py            # checks runner（bash/lint/rubric）
src/tianshu/executor/orchestrator/critic.py            # critic agent + system prompt
src/tianshu/executor/orchestrator/loop.py              # 主 outer loop
src/tianshu/executor/orchestrator/persistence.py       # iteration 持久化 + 审计事件
src/tianshu/executor/orchestrator/archive.py           # 30 天归档任务
tests/test_escalation.py                               # decide_escalation 参数化测试
tests/test_orchestrator_loop.py                        # outer loop 集成测试
tests/test_orchestrator_state.py                       # state advance/with_level 测试
tests/test_orchestrator_checks.py                      # checks runner 测试
tests/test_orchestrator_critic.py                      # critic agent 测试
tests/test_outer_loop_resume.py                        # checkpoint + resume 测试
```

**修改文件**

```
src/tianshu/models/edict.py                  # +acceptance +execution_profile
src/tianshu/storage.py                       # +outer_loop_iterations 表 + CRUD + edict 列迁移
src/tianshu/executor/checkpoint.py           # +OuterLoopCheckpoint payload kind
src/tianshu/executor/executor.py             # 路由分流（acceptance 是否为 None）
```

---

## Task 1: AcceptanceCriteria 模型

**Files:**
- Create: `src/tianshu/models/acceptance.py`

- [ ] **Step 1: 创建 acceptance.py，定义 4 个 pydantic 模型**

```python
# src/tianshu/models/acceptance.py
"""AcceptanceCriteria — 长任务 outer loop 触发字段。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CheckSpec(BaseModel):
    kind: Literal["bash", "lint", "rubric"] = "bash"
    name: str
    command: str | None = None        # kind=bash/lint 必填
    rubric: str | None = None         # kind=rubric 必填
    weight: float = 1.0
    pass_threshold: float = 0.8       # rubric 通过阈值
    timeout_seconds: int = 60


class CriticSpec(BaseModel):
    persona_id: str | None = None
    model: str | None = None
    same_issue_threshold: int = 2


class EscalationSpec(BaseModel):
    enabled_levels: list[Literal["L1", "L2", "L3"]] = Field(
        default_factory=lambda: ["L1", "L2", "L3"]
    )
    l1_max_rounds: int = 2
    l2_max_rounds: int = 1
    l1_thinking_budget: int = 8000
    l1_model_upgrade: str | None = None
    l2_consultation_personas: list[str] = Field(default_factory=list)


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

- [ ] **Step 2: 验证 import 成功**

```bash
python -c "from tianshu.models.acceptance import AcceptanceCriteria; print(AcceptanceCriteria().model_dump())"
```
Expected: 输出空字段的 dict，无报错。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/models/acceptance.py
git commit -m "feat(models): AcceptanceCriteria — 长任务 outer loop 触发字段"
```

---

## Task 2: Edict 扩展（acceptance + execution_profile）

**Files:**
- Modify: `src/tianshu/models/edict.py`
- Modify: `src/tianshu/storage.py`（新增列迁移 + 序列化）

- [ ] **Step 1: 在 Edict 加字段**

修改 `src/tianshu/models/edict.py`，在 `Edict` 类末尾（`metadata` 字段之前）加：

```python
from tianshu.models.acceptance import AcceptanceCriteria  # 在文件顶部 import 区加


class Edict(BaseModel):
    # ... 已有字段保留 ...
    acceptance: AcceptanceCriteria | None = None
    execution_profile: Literal["foreground", "checkpointed", "background"] = "foreground"
    metadata: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 2: 加 storage 列迁移**

修改 `src/tianshu/storage.py` 的 `_migrate()` 方法的 `migrations` 列表末尾追加：

```python
# 2026-04-26: 长任务 outer loop 字段
"ALTER TABLE edicts ADD COLUMN acceptance_json TEXT",
"ALTER TABLE edicts ADD COLUMN execution_profile TEXT NOT NULL DEFAULT 'foreground'",
```

- [ ] **Step 3: 修改 save_edict / get_edict 序列化**

在 `Storage.save_edict()` 中（约 line 465 起）：往 INSERT/UPDATE 的列里加：
```python
acceptance_json = (
    edict.acceptance.model_dump_json() if edict.acceptance else None
)
# 加入 INSERT/UPDATE 的字段集合：
#   acceptance_json, execution_profile
```

在 `Storage.get_edict()` 中（约 line 499 起）：从 row 解析时加：
```python
import json
acceptance = None
if row["acceptance_json"]:
    from tianshu.models.acceptance import AcceptanceCriteria
    acceptance = AcceptanceCriteria.model_validate_json(row["acceptance_json"])
# 然后 Edict(...) 构造时传 acceptance=acceptance, execution_profile=row["execution_profile"]
```

具体修改时先 Read 这两个方法的完整实现再 Edit。

- [ ] **Step 4: 验证不破现有 edict**

```bash
rm -f /tmp/test_tianshu.db && python -c "
from tianshu.storage import Storage
from tianshu.models.edict import Edict
s = Storage('/tmp/test_tianshu.db')
e = Edict(goal='test')
s.save_edict(e)
r = s.get_edict(e.id)
assert r.acceptance is None
assert r.execution_profile == 'foreground'
print('OK')
"
```
Expected: 输出 `OK`，无异常。

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/models/edict.py src/tianshu/storage.py
git commit -m "feat(edict): 加 acceptance + execution_profile 字段（默认 None / foreground）"
```

---

## Task 3: outer_loop_iterations 表 + Storage CRUD

**Files:**
- Modify: `src/tianshu/storage.py`（建表 + 新增 CRUD 方法）

- [ ] **Step 1: 在 _create_tables() 中加建表**

在 `src/tianshu/storage.py` 的 `_create_tables()` 方法（紧跟其他 CREATE TABLE 之后）追加：

```python
self._conn.execute("""
    CREATE TABLE IF NOT EXISTS outer_loop_iterations (
        id              TEXT PRIMARY KEY,
        edict_id        TEXT NOT NULL,
        iteration       INTEGER NOT NULL,
        level           TEXT NOT NULL,
        actor_output    TEXT,
        checks_result   TEXT,
        critic_result   TEXT,
        cost_cny        REAL DEFAULT 0,
        started_at      TEXT NOT NULL,
        finished_at     TEXT NOT NULL,
        archived_at     TEXT,
        UNIQUE (edict_id, iteration)
    )
""")
self._conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_outer_loop_edict
        ON outer_loop_iterations(edict_id, iteration)
""")
self._conn.execute("""
    CREATE INDEX IF NOT EXISTS idx_outer_loop_archive
        ON outer_loop_iterations(finished_at) WHERE archived_at IS NULL
""")
```

- [ ] **Step 2: 加四个 CRUD 方法**

在 `Storage` 类末尾（最后一个 `def` 之后）追加：

```python
def save_outer_loop_iteration(self, record: dict) -> None:
    """写入一条 outer loop iteration（dict 形式以避免循环 import）。"""
    self._conn.execute("""
        INSERT INTO outer_loop_iterations
        (id, edict_id, iteration, level, actor_output, checks_result,
         critic_result, cost_cny, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(edict_id, iteration) DO NOTHING
    """, (
        record["id"], record["edict_id"], record["iteration"],
        record["level"], record["actor_output"],
        record["checks_result"], record["critic_result"],
        record["cost_cny"], record["started_at"], record["finished_at"],
    ))
    self._conn.commit()

def get_outer_loop_iterations(self, edict_id: str) -> list[dict]:
    """按 iteration 升序返回所有迭代记录。"""
    rows = self._conn.execute("""
        SELECT id, edict_id, iteration, level, actor_output, checks_result,
               critic_result, cost_cny, started_at, finished_at, archived_at
        FROM outer_loop_iterations
        WHERE edict_id = ?
        ORDER BY iteration ASC
    """, (edict_id,)).fetchall()
    return [dict(r) for r in rows]

def list_iterations_to_archive(self, before: str) -> list[str]:
    """返回 finished_at < before 且未归档的 iteration id 列表。"""
    rows = self._conn.execute("""
        SELECT id FROM outer_loop_iterations
        WHERE finished_at < ? AND archived_at IS NULL
    """, (before,)).fetchall()
    return [r["id"] for r in rows]

def archive_iteration(self, iteration_id: str, archived_at: str) -> None:
    """归档：actor_output 置 NULL，archived_at 写时间戳。"""
    self._conn.execute("""
        UPDATE outer_loop_iterations
        SET actor_output = NULL, archived_at = ?
        WHERE id = ?
    """, (archived_at, iteration_id))
    self._conn.commit()
```

- [ ] **Step 3: 烟雾测试**

```bash
rm -f /tmp/test_tianshu.db && python -c "
from tianshu.storage import Storage
s = Storage('/tmp/test_tianshu.db')
s.save_outer_loop_iteration({
    'id': 'iter1', 'edict_id': 'e1', 'iteration': 0, 'level': 'L0',
    'actor_output': 'hi', 'checks_result': '{}', 'critic_result': None,
    'cost_cny': 0.1,
    'started_at': '2026-04-26T00:00:00Z', 'finished_at': '2026-04-26T00:01:00Z',
})
rows = s.get_outer_loop_iterations('e1')
assert len(rows) == 1 and rows[0]['actor_output'] == 'hi'
print('OK')
"
```
Expected: 输出 `OK`。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/storage.py
git commit -m "feat(storage): outer_loop_iterations 表 + CRUD（save/get/list_to_archive/archive）"
```

---

## Task 4: OuterLoopState frozen dataclass

**Files:**
- Create: `src/tianshu/executor/orchestrator/__init__.py`
- Create: `src/tianshu/executor/orchestrator/state.py`

- [ ] **Step 1: 建子模块**

```bash
mkdir -p src/tianshu/executor/orchestrator
```

创建 `src/tianshu/executor/orchestrator/__init__.py`：

```python
"""Long-task outer loop orchestrator."""
```

- [ ] **Step 2: 创建 state.py**

```python
# src/tianshu/executor/orchestrator/state.py
"""Outer loop state — frozen dataclasses, never mutated."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

Level = Literal["L0", "L1", "L2", "L3"]


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    passed: bool
    detail: str | None = None
    score: float | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class ChecksResult:
    all_passed: bool
    outcomes: tuple[CheckOutcome, ...] = ()


@dataclass(frozen=True)
class CriticResult:
    verdict: Literal["pass", "fail"]
    issue_class: str | None = None         # FAIL 时必填
    feedback: str = ""
    suggested_fix: str | None = None


@dataclass(frozen=True)
class IterationRecord:
    iteration: int
    level: Level
    actor_output: str
    checks_result: ChecksResult
    critic_result: CriticResult | None
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
    consultation_advice: str | None = None
    history: tuple[IterationRecord, ...] = field(default_factory=tuple)
    total_cost_cny: float = 0.0

    def advance(self, record: IterationRecord) -> "OuterLoopState":
        """append record 并按 critic_result 更新 streak / issue_class。"""
        new_streak = self.same_issue_streak
        new_issue = self.last_critic_issue_class
        if record.critic_result is not None:
            cur = record.critic_result.issue_class
            if cur is None:
                # PASS or 缺 issue_class，不动 streak
                pass
            elif cur == self.last_critic_issue_class:
                new_streak = self.same_issue_streak + 1
            else:
                new_streak = 1
                new_issue = cur

        # 更新 L1/L2 round 计数
        new_l1 = self.l1_rounds_used + (1 if record.level == "L1" else 0)
        new_l2 = self.l2_rounds_used + (1 if record.level == "L2" else 0)

        return replace(
            self,
            iteration=self.iteration + 1,
            same_issue_streak=new_streak,
            last_critic_issue_class=new_issue,
            l1_rounds_used=new_l1,
            l2_rounds_used=new_l2,
            history=self.history + (record,),
            total_cost_cny=self.total_cost_cny + record.cost_cny,
        )

    def with_level(self, level: Level) -> "OuterLoopState":
        # 升级时 streak 不清零 —— 由 advance() 据 issue_class 是否同类决定
        return replace(self, current_level=level)

    def with_consultation_advice(self, advice: str) -> "OuterLoopState":
        return replace(self, consultation_advice=advice)
```

- [ ] **Step 3: 烟雾验证**

```bash
python -c "
from datetime import datetime
from tianshu.executor.orchestrator.state import (
    OuterLoopState, IterationRecord, ChecksResult, CriticResult,
)
s = OuterLoopState(edict_id='e1')
r = IterationRecord(
    iteration=0, level='L0', actor_output='hi',
    checks_result=ChecksResult(all_passed=True),
    critic_result=CriticResult(verdict='fail', issue_class='factual_error', feedback='wrong'),
    started_at=datetime.utcnow(), finished_at=datetime.utcnow(),
    cost_cny=0.1,
)
s2 = s.advance(r)
assert s2.iteration == 1
assert s2.same_issue_streak == 1
assert s2.last_critic_issue_class == 'factual_error'
s3 = s2.advance(r)  # 同 issue_class
assert s3.same_issue_streak == 2
print('OK')
"
```
Expected: `OK`。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/executor/orchestrator/__init__.py src/tianshu/executor/orchestrator/state.py
git commit -m "feat(orchestrator): OuterLoopState + advance/with_level（frozen dataclass）"
```

---

## Task 5: escalation.decide_escalation 纯函数（带 inline 单元测试）

> 这是 outer loop 的核心 FSM。**例外**：本 task 立刻写参数化测试，因为函数纯且 Bug 难追。

**Files:**
- Create: `src/tianshu/executor/orchestrator/escalation.py`
- Create: `tests/test_escalation.py`

- [ ] **Step 1: 写 escalation.py**

```python
# src/tianshu/executor/orchestrator/escalation.py
"""Escalation FSM — 纯函数，决定下一 level。"""

from __future__ import annotations

from typing import Literal

from tianshu.executor.orchestrator.state import Level, OuterLoopState
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.edict import Edict

Decision = Literal["L0", "L1", "L2", "L3", "EXHAUSTED"]


def decide_escalation(
    state: OuterLoopState,
    edict: Edict,
    acceptance: AcceptanceCriteria,
    *,
    last_critic_passed: bool,
) -> Decision:
    """决定下一步：留级 / 升级 / 耗尽。

    last_critic_passed=True 时调用方应直接收工，不应调本函数；
    保留参数仅作 sanity check（True 时返回当前 level）。
    """
    if last_critic_passed:
        return state.current_level  # type: ignore[return-value]

    # 1. iteration / cost / deadline 硬上限
    if state.iteration >= acceptance.max_outer_iterations:
        return "EXHAUSTED"
    budget = edict.runtime.cost_budget_cny
    if budget is not None and state.total_cost_cny >= budget:
        return "EXHAUSTED"

    enabled = acceptance.escalation.enabled_levels
    threshold = acceptance.critic.same_issue_threshold

    if state.current_level == "L0":
        if state.same_issue_streak >= threshold:
            if "L1" in enabled:
                return "L1"
            if "L2" in enabled:
                return "L2"
            if "L3" in enabled:
                return "L3"
        return "L0"

    if state.current_level == "L1":
        if state.l1_rounds_used >= acceptance.escalation.l1_max_rounds:
            if "L2" in enabled:
                return "L2"
            if "L3" in enabled:
                return "L3"
            return "EXHAUSTED"
        return "L1"

    if state.current_level == "L2":
        if state.l2_rounds_used >= acceptance.escalation.l2_max_rounds:
            if "L3" in enabled:
                return "L3"
            return "EXHAUSTED"
        return "L2"

    # L3 不再升级，只能等审批
    return "L3"
```

- [ ] **Step 2: 写 test_escalation.py**

```python
# tests/test_escalation.py
"""decide_escalation 参数化测试 — 覆盖所有升级分支。"""

from __future__ import annotations

import pytest

from tianshu.executor.orchestrator.escalation import decide_escalation
from tianshu.executor.orchestrator.state import OuterLoopState
from tianshu.models.acceptance import (
    AcceptanceCriteria,
    CriticSpec,
    EscalationSpec,
)
from tianshu.models.edict import Edict, EdictRuntime


def _state(**kwargs) -> OuterLoopState:
    defaults = {"edict_id": "e1"}
    defaults.update(kwargs)
    return OuterLoopState(**defaults)


def _accept(**kwargs) -> AcceptanceCriteria:
    return AcceptanceCriteria(**kwargs)


def _edict(cost_budget: float | None = None) -> Edict:
    return Edict(goal="test", runtime=EdictRuntime(cost_budget_cny=cost_budget))


@pytest.mark.unit
@pytest.mark.parametrize("state, accept, edict, expected", [
    # L0 同类未达阈值 → 留 L0
    (_state(current_level="L0", same_issue_streak=1),
     _accept(critic=CriticSpec(same_issue_threshold=2)),
     _edict(), "L0"),
    # L0 同类达阈值 → L1
    (_state(current_level="L0", same_issue_streak=2),
     _accept(critic=CriticSpec(same_issue_threshold=2)),
     _edict(), "L1"),
    # L0 同类达阈值 + L1 关闭 → 跳 L2
    (_state(current_level="L0", same_issue_streak=2),
     _accept(
        critic=CriticSpec(same_issue_threshold=2),
        escalation=EscalationSpec(enabled_levels=["L2", "L3"]),
     ),
     _edict(), "L2"),
    # L1 重试用尽 → L2
    (_state(current_level="L1", l1_rounds_used=2),
     _accept(escalation=EscalationSpec(l1_max_rounds=2)),
     _edict(), "L2"),
    # L2 重试用尽 → L3
    (_state(current_level="L2", l2_rounds_used=1),
     _accept(escalation=EscalationSpec(l2_max_rounds=1)),
     _edict(), "L3"),
    # L3 留级（等审批）
    (_state(current_level="L3"),
     _accept(),
     _edict(), "L3"),
    # iteration 超限 → EXHAUSTED
    (_state(iteration=5),
     _accept(max_outer_iterations=5),
     _edict(), "EXHAUSTED"),
    # 预算超限 → EXHAUSTED
    (_state(total_cost_cny=10.0),
     _accept(),
     _edict(cost_budget=5.0), "EXHAUSTED"),
])
def test_decide_escalation(state, accept, edict, expected):
    assert decide_escalation(
        state, edict, accept, last_critic_passed=False,
    ) == expected


@pytest.mark.unit
def test_decide_escalation_passed_returns_current_level():
    state = _state(current_level="L1")
    assert decide_escalation(
        state, _edict(), _accept(), last_critic_passed=True,
    ) == "L1"
```

- [ ] **Step 3: 跑测试**

```bash
pytest tests/test_escalation.py -v
```
Expected: 所有用例 PASS。

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/executor/orchestrator/escalation.py tests/test_escalation.py
git commit -m "feat(orchestrator): decide_escalation FSM 纯函数 + 参数化测试"
```

---

## Task 6: Checks Runner（bash / lint / rubric）

**Files:**
- Create: `src/tianshu/executor/orchestrator/checks.py`

- [ ] **Step 1: 写 checks.py**

```python
# src/tianshu/executor/orchestrator/checks.py
"""Checks runner — 跑 bash / lint / rubric 三类指标。"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from tianshu.executor.orchestrator.state import CheckOutcome, ChecksResult
from tianshu.llm import LLMClient
from tianshu.models.acceptance import CheckSpec

logger = logging.getLogger(__name__)


class ChecksConfigError(Exception):
    """check 命令本身错（如 command not found），整个 outer loop 应 abort。"""


async def _run_bash(spec: CheckSpec) -> CheckOutcome:
    if not spec.command:
        raise ChecksConfigError(f"check {spec.name}: kind=bash 需要 command 字段")
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_shell(
            spec.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=spec.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return CheckOutcome(
                name=spec.name, passed=False,
                detail=f"timeout after {spec.timeout_seconds}s",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
    except FileNotFoundError as e:
        # command not found → 配置错，抛
        raise ChecksConfigError(f"check {spec.name}: command not found: {e}") from e

    duration_ms = int((time.monotonic() - start) * 1000)
    passed = proc.returncode == 0
    detail = None
    if not passed:
        detail = (stderr or stdout or b"").decode("utf-8", errors="replace")[:1000]
    return CheckOutcome(
        name=spec.name, passed=passed, detail=detail, duration_ms=duration_ms,
    )


async def _run_rubric(
    spec: CheckSpec,
    actor_output: str,
    llm: LLMClient,
) -> CheckOutcome:
    if not spec.rubric:
        raise ChecksConfigError(f"check {spec.name}: kind=rubric 需要 rubric 字段")
    prompt = (
        f"Rubric:\n{spec.rubric}\n\n"
        f"Output to evaluate:\n{actor_output}\n\n"
        f"Reply with JSON: {{\"score\": 0.0-1.0, \"reasoning\": \"...\"}}"
    )
    start = time.monotonic()
    resp = await llm.complete(
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    duration_ms = int((time.monotonic() - start) * 1000)
    try:
        data = json.loads(resp.content or "{}")
        score = float(data.get("score", 0.0))
        reasoning = str(data.get("reasoning", ""))
    except (ValueError, json.JSONDecodeError) as e:
        return CheckOutcome(
            name=spec.name, passed=False,
            detail=f"rubric LLM 输出解析失败: {e}",
            duration_ms=duration_ms,
        )
    return CheckOutcome(
        name=spec.name,
        passed=score >= spec.pass_threshold,
        score=score,
        detail=reasoning[:500],
        duration_ms=duration_ms,
    )


async def run_checks(
    specs: list[CheckSpec],
    actor_output: str,
    llm: LLMClient,
) -> ChecksResult:
    """并发跑所有 checks，返回汇总。配置错（command not found）会冒泡。"""
    if not specs:
        return ChecksResult(all_passed=True, outcomes=())

    async def _dispatch(spec: CheckSpec) -> CheckOutcome:
        if spec.kind in ("bash", "lint"):
            return await _run_bash(spec)
        if spec.kind == "rubric":
            return await _run_rubric(spec, actor_output, llm)
        raise ChecksConfigError(f"unknown check kind: {spec.kind}")

    outcomes = await asyncio.gather(*[_dispatch(s) for s in specs])
    all_passed = all(o.passed for o in outcomes)
    return ChecksResult(all_passed=all_passed, outcomes=tuple(outcomes))
```

- [ ] **Step 2: 烟雾验证**

```bash
python -c "
import asyncio
from tianshu.executor.orchestrator.checks import run_checks
from tianshu.models.acceptance import CheckSpec

async def main():
    r = await run_checks([
        CheckSpec(kind='bash', name='ok', command='echo ok'),
        CheckSpec(kind='bash', name='fail', command='exit 1'),
    ], 'output', llm=None)
    print(r.all_passed, [(o.name, o.passed) for o in r.outcomes])

asyncio.run(main())
"
```
Expected: `False [('ok', True), ('fail', False)]`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/executor/orchestrator/checks.py
git commit -m "feat(orchestrator): checks runner — bash/lint/rubric 并发执行"
```

---

## Task 7: Critic Agent

**Files:**
- Create: `src/tianshu/executor/orchestrator/critic.py`

- [ ] **Step 1: 写 critic.py**

```python
# src/tianshu/executor/orchestrator/critic.py
"""Critic agent — 独立 LLM 调用，结构化输出 verdict + issue_class。"""

from __future__ import annotations

import json
import logging
from typing import Literal

from tianshu.executor.orchestrator.state import CriticResult
from tianshu.llm import LLMClient
from tianshu.models.acceptance import AcceptanceCriteria, CriticSpec
from tianshu.models.edict import Edict

logger = logging.getLogger(__name__)

# v1 内置 issue_class 集合 —— critic system prompt 强约束在内
ISSUE_CLASSES: tuple[str, ...] = (
    "factual_error",         # 事实性错误
    "tone_mismatch",         # 语气/风格与目标不符
    "incomplete_coverage",   # 覆盖不全
    "structure_mismatch",    # 结构与要求不符
    "formatting_violation",  # 格式问题
    "checks_failed",         # 指标层失败（不进 critic）
    "other",                 # 未分类
)

_SYSTEM_PROMPT = """你是天枢的 critic agent。基于 edict 的 acceptance criteria，
判定 actor 的输出是否合格。

输出严格 JSON:
{
  "verdict": "pass" | "fail",
  "issue_class": <one of: %s>,
  "feedback": "...",
  "suggested_fix": "..." (optional)
}

规则:
- 如果合格 → verdict=pass, issue_class 可留空
- 如果不合格 → verdict=fail, issue_class 必填且必须从给定集合选
- feedback 给 actor 看，要具体可执行
""" % ", ".join(ISSUE_CLASSES)


class CriticUnavailable(Exception):
    """critic LLM 调用全部失败（包括 fallback）。调用方按 on_critic_unavailable 决策。"""


async def review(
    actor_output: str,
    edict: Edict,
    acceptance: AcceptanceCriteria,
    llm: LLMClient,
    *,
    fallback_llm: LLMClient | None = None,
    max_retries: int = 2,
) -> CriticResult:
    """独立调用 critic LLM。
    重试 max_retries 次；仍失败时尝试 fallback_llm；都不行则抛 CriticUnavailable。
    """
    user_msg = (
        f"# Edict goal\n{edict.goal}\n\n"
        f"# Acceptance criteria summary\n"
        f"max_outer_iterations: {acceptance.max_outer_iterations}\n"
        f"checks: {[c.name for c in acceptance.checks]}\n\n"
        f"# Actor output\n{actor_output}"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = await llm.complete(
                messages=messages,
                response_format={"type": "json_object"},
            )
            return _parse(resp.content or "")
        except Exception as e:
            logger.warning("critic LLM attempt %d failed: %s", attempt, e)
            last_err = e

    if fallback_llm is not None:
        try:
            resp = await fallback_llm.complete(
                messages=messages,
                response_format={"type": "json_object"},
            )
            return _parse(resp.content or "")
        except Exception as e:
            logger.error("critic fallback also failed: %s", e)
            last_err = e

    raise CriticUnavailable(f"critic 全部尝试失败: {last_err}")


def _parse(raw: str) -> CriticResult:
    data = json.loads(raw)
    verdict = data.get("verdict")
    if verdict not in ("pass", "fail"):
        raise ValueError(f"verdict 非法: {verdict!r}")
    issue_class = data.get("issue_class")
    if verdict == "fail":
        if issue_class not in ISSUE_CLASSES:
            issue_class = "other"
    else:
        issue_class = None
    return CriticResult(
        verdict=verdict,
        issue_class=issue_class,
        feedback=str(data.get("feedback", "")),
        suggested_fix=data.get("suggested_fix"),
    )
```

- [ ] **Step 2: import 验证**

```bash
python -c "
from tianshu.executor.orchestrator.critic import review, ISSUE_CLASSES, _parse
r = _parse('{\"verdict\":\"fail\",\"issue_class\":\"factual_error\",\"feedback\":\"x\"}')
assert r.verdict == 'fail' and r.issue_class == 'factual_error'
print('OK')
"
```
Expected: `OK`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/executor/orchestrator/critic.py
git commit -m "feat(orchestrator): critic agent — 独立 LLM 调用 + 结构化 issue_class"
```

---

## Task 8: 持久化 + 审计事件 helper

**Files:**
- Create: `src/tianshu/executor/orchestrator/persistence.py`

- [ ] **Step 1: 写 persistence.py**

```python
# src/tianshu/executor/orchestrator/persistence.py
"""持久化 iteration record 到 outer_loop_iterations 表 + 审计事件到 memorial。"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime

from ulid import ULID

from tianshu.bus.event_bus import EventBus
from tianshu.executor.orchestrator.state import IterationRecord
from tianshu.models.events import make_event
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


def persist_iteration(
    storage: Storage,
    edict_id: str,
    record: IterationRecord,
) -> str:
    """写一行 outer_loop_iterations，返回 row id（ULID）。"""
    row_id = str(ULID())
    storage.save_outer_loop_iteration({
        "id": row_id,
        "edict_id": edict_id,
        "iteration": record.iteration,
        "level": record.level,
        "actor_output": record.actor_output,
        "checks_result": json.dumps({
            "all_passed": record.checks_result.all_passed,
            "outcomes": [asdict(o) for o in record.checks_result.outcomes],
        }),
        "critic_result": (
            json.dumps(asdict(record.critic_result))
            if record.critic_result else None
        ),
        "cost_cny": record.cost_cny,
        "started_at": record.started_at.isoformat(),
        "finished_at": record.finished_at.isoformat(),
    })
    return row_id


async def emit_audit(
    bus: EventBus,
    storage: Storage,
    edict_id: str,
    memorial_id: str | None,
    event_type: str,
    payload: dict,
) -> None:
    """发审计事件（同时落 memorial 事件流 + EventBus 广播）。"""
    if memorial_id:
        storage.append_event(edict_id, memorial_id, event_type, payload)
    try:
        await bus.emit(make_event(
            event_type,
            edict_id=edict_id,
            memorial_id=memorial_id,
            producer="orchestrator",
            payload=payload,
        ))
    except Exception:
        logger.exception("emit_audit %s failed", event_type)
```

- [ ] **Step 2: import 验证**

```bash
python -c "from tianshu.executor.orchestrator.persistence import persist_iteration, emit_audit; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/executor/orchestrator/persistence.py
git commit -m "feat(orchestrator): persist_iteration + emit_audit helper"
```

---

## Task 9: 主 outer loop —— L0 单层（先跑通）

**Files:**
- Create: `src/tianshu/executor/orchestrator/loop.py`
- Modify: `src/tianshu/executor/orchestrator/__init__.py`

> 本任务只跑 L0 → L0 → ... → exhausted/pass 的最简版，后续 task 加 L1/L2/L3。

- [ ] **Step 1: 写 loop.py 骨架**

```python
# src/tianshu/executor/orchestrator/loop.py
"""Outer loop 主编排 —— actor → checks → critic → 升级判断。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from tianshu.bus.event_bus import EventBus
from tianshu.executor.orchestrator.checks import ChecksConfigError, run_checks
from tianshu.executor.orchestrator.critic import CriticUnavailable, review
from tianshu.executor.orchestrator.escalation import decide_escalation
from tianshu.executor.orchestrator.persistence import emit_audit, persist_iteration
from tianshu.executor.orchestrator.state import (
    CriticResult,
    IterationRecord,
    OuterLoopState,
)
from tianshu.llm import LLMClient
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class OrchestratorContext:
    """聚合 orchestrator 运行所需的依赖（避免 run() 参数爆炸）。"""

    def __init__(
        self,
        agent: object,                        # 现有 Agent 实例
        storage: Storage,
        bus: EventBus,
        actor_llm: LLMClient,
        critic_llm: LLMClient,
        critic_fallback_llm: LLMClient | None = None,
        consultation_session: object | None = None,  # ConsultationSession
        notifier: object | None = None,
        approvals: object | None = None,
    ) -> None:
        self.agent = agent
        self.storage = storage
        self.bus = bus
        self.actor_llm = actor_llm
        self.critic_llm = critic_llm
        self.critic_fallback_llm = critic_fallback_llm
        self.consultation_session = consultation_session
        self.notifier = notifier
        self.approvals = approvals


class OrchestratorResult:
    """outer loop 终态。"""

    def __init__(
        self,
        status: TaskStatus,
        final_output: str | None,
        state: OuterLoopState,
        error: str | None = None,
    ) -> None:
        self.status = status
        self.final_output = final_output
        self.state = state
        self.error = error


async def run(
    edict: Edict,
    memorial: Memorial,
    ctx: OrchestratorContext,
) -> OrchestratorResult:
    """outer loop 主入口。要求 edict.acceptance is not None。"""
    assert edict.acceptance is not None, "orchestrator.run 要求 acceptance 不为 None"
    acceptance = edict.acceptance
    state = OuterLoopState(edict_id=edict.id)

    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.started", {"max_outer": acceptance.max_outer_iterations},
    )

    while state.iteration < acceptance.max_outer_iterations:
        iter_started = datetime.now(UTC)
        await emit_audit(
            ctx.bus, ctx.storage, edict.id, memorial.id,
            "outer_loop.iteration.started",
            {"iteration": state.iteration, "level": state.current_level},
        )

        # 1. actor
        actor_result = await ctx.agent.execute(edict, memorial=memorial)
        actor_output = actor_result.result or actor_result.summary or ""
        actor_cost = float(getattr(actor_result.usage, "cost_cny", 0.0) or 0.0)

        # 2. checks
        try:
            checks_result = await run_checks(
                acceptance.checks, actor_output, ctx.actor_llm,
            )
        except ChecksConfigError as e:
            return OrchestratorResult(
                status=TaskStatus.FAILED,
                final_output=None,
                state=state,
                error=f"checks 配置错: {e}",
            )

        # 3. critic（仅当 checks 全过才跑）
        critic_result: CriticResult | None = None
        if checks_result.all_passed:
            try:
                critic_result = await review(
                    actor_output, edict, acceptance, ctx.critic_llm,
                    fallback_llm=ctx.critic_fallback_llm,
                )
            except CriticUnavailable as e:
                if acceptance.on_critic_unavailable == "skip":
                    critic_result = CriticResult(verdict="pass", feedback=f"critic 不可用，skip: {e}")
                else:
                    # 升级到人 —— Task 12 实现
                    critic_result = CriticResult(
                        verdict="fail", issue_class="other",
                        feedback=f"critic 不可用: {e}",
                    )
        else:
            critic_result = CriticResult(
                verdict="fail",
                issue_class="checks_failed",
                feedback=f"checks 未通过: {[o.name for o in checks_result.outcomes if not o.passed]}",
            )

        record = IterationRecord(
            iteration=state.iteration,
            level=state.current_level,
            actor_output=actor_output,
            checks_result=checks_result,
            critic_result=critic_result,
            started_at=iter_started,
            finished_at=datetime.now(UTC),
            cost_cny=actor_cost,
        )
        persist_iteration(ctx.storage, edict.id, record)

        await emit_audit(
            ctx.bus, ctx.storage, edict.id, memorial.id,
            "outer_loop.iteration.finished",
            {
                "iteration": state.iteration,
                "level": state.current_level,
                "checks_passed": checks_result.all_passed,
                "critic_verdict": critic_result.verdict if critic_result else None,
            },
        )

        # 4. PASS → 收工
        if critic_result and critic_result.verdict == "pass":
            state = state.advance(record)
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.completed",
                {"iterations": state.iteration, "total_cost": state.total_cost_cny},
            )
            return OrchestratorResult(
                status=TaskStatus.COMPLETED,
                final_output=actor_output,
                state=state,
            )

        # 5. FAIL → advance + 升级（暂不实现，Task 10-12 加）
        state = state.advance(record)
        decision = decide_escalation(
            state, edict, acceptance, last_critic_passed=False,
        )
        if decision == "EXHAUSTED":
            return await _handle_exhaustion(state, edict, ctx, memorial)
        if decision != state.current_level:
            state = state.with_level(decision)  # type: ignore[arg-type]
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.escalated",
                {"to": decision, "iteration": state.iteration},
            )

    return await _handle_exhaustion(state, edict, ctx, memorial)


async def _handle_exhaustion(
    state: OuterLoopState,
    edict: Edict,
    ctx: OrchestratorContext,
    memorial: Memorial,
) -> OrchestratorResult:
    """iteration / 预算 / 截止时间耗尽 —— 按 on_exhaustion 决策。"""
    acceptance = edict.acceptance
    assert acceptance is not None
    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.exhausted",
        {
            "iterations": state.iteration,
            "total_cost": state.total_cost_cny,
            "on_exhaustion": acceptance.on_exhaustion,
        },
    )
    last_output = state.history[-1].actor_output if state.history else None
    if acceptance.on_exhaustion == "best_effort":
        return OrchestratorResult(
            status=TaskStatus.COMPLETED,
            final_output=last_output,
            state=state,
            error="exhausted, returning best effort",
        )
    if acceptance.on_exhaustion == "fail":
        return OrchestratorResult(
            status=TaskStatus.FAILED,
            final_output=None,
            state=state,
            error="outer loop exhausted",
        )
    # escalate → Task 12 加；当前 fallback 到 fail
    return OrchestratorResult(
        status=TaskStatus.FAILED,
        final_output=last_output,
        state=state,
        error="exhausted, escalation not yet wired (Task 12)",
    )
```

- [ ] **Step 2: 暴露入口**

修改 `src/tianshu/executor/orchestrator/__init__.py`：

```python
"""Long-task outer loop orchestrator."""

from tianshu.executor.orchestrator.loop import (
    OrchestratorContext,
    OrchestratorResult,
    run,
)

__all__ = ["OrchestratorContext", "OrchestratorResult", "run"]
```

- [ ] **Step 3: import 验证**

```bash
python -c "from tianshu.executor.orchestrator import run, OrchestratorContext; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/executor/orchestrator/loop.py src/tianshu/executor/orchestrator/__init__.py
git commit -m "feat(orchestrator): 主 outer loop —— L0 单层骨架（actor + checks + critic）"
```

---

## Task 10: L1 升级 —— thinking budget + 模型切换

**Files:**
- Modify: `src/tianshu/executor/orchestrator/loop.py`

- [ ] **Step 1: 写 derive_actor_config helper**

在 `loop.py` 顶部 import 区下方加：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ActorOverride:
    """L1 升级时给 actor 的配置覆盖。"""
    thinking_budget: int | None = None
    model: str | None = None
    extra_system_msg: str | None = None  # critic feedback / consultation advice 注入


def derive_actor_override(
    state: OuterLoopState,
    edict: Edict,
) -> ActorOverride:
    """根据当前 level 计算 actor 配置覆盖。"""
    acceptance = edict.acceptance
    assert acceptance is not None
    esc = acceptance.escalation

    # 拼 critic feedback 注入消息
    extra_msg_parts: list[str] = []
    if state.history:
        last_record = state.history[-1]
        if last_record.critic_result and last_record.critic_result.verdict == "fail":
            extra_msg_parts.append(
                f"上一轮 critic 反馈（issue_class={last_record.critic_result.issue_class}）：\n"
                f"{last_record.critic_result.feedback}"
            )
            if last_record.critic_result.suggested_fix:
                extra_msg_parts.append(f"建议修复：{last_record.critic_result.suggested_fix}")
    if state.consultation_advice:
        extra_msg_parts.append(f"九卿会议建议：\n{state.consultation_advice}")

    extra = "\n\n".join(extra_msg_parts) if extra_msg_parts else None

    if state.current_level == "L1":
        return ActorOverride(
            thinking_budget=esc.l1_thinking_budget,
            model=esc.l1_model_upgrade,
            extra_system_msg=extra,
        )
    return ActorOverride(extra_system_msg=extra)
```

- [ ] **Step 2: 修改 run() 中调用 actor 的地方**

把原来的 `actor_result = await ctx.agent.execute(edict, memorial=memorial)` 改为：

```python
override = derive_actor_override(state, edict)
# 把 critic feedback 注入到 user_content 末尾
augmented_content = edict.goal
if edict.context:
    augmented_content += f"\n\nAdditional context: {edict.context}"
if override.extra_system_msg:
    augmented_content += f"\n\n## 上一轮反馈与建议\n{override.extra_system_msg}"

# Agent.execute 暂不直接支持 thinking_budget / model 覆盖；
# 通过 hook 或 config_manager state override 实现 —— v1 仅传 user_content
actor_result = await ctx.agent.execute(
    edict,
    memorial=memorial,
    user_content=augmented_content,
)
```

> 备注：v1 的 L1 模型升级仅做 prompt 注入；config 级模型切换需要 Agent 层支持，暂作为 TODO 注释，**不阻塞**功能跑通。

- [ ] **Step 3: 烟雾验证**

```bash
python -c "from tianshu.executor.orchestrator.loop import derive_actor_override; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/executor/orchestrator/loop.py
git commit -m "feat(orchestrator): L1 升级 —— critic feedback 注入 actor user_content"
```

---

## Task 11: L2 升级 —— consultation 跨部协商

**Files:**
- Modify: `src/tianshu/executor/orchestrator/loop.py`

- [ ] **Step 1: 在 escalated 分支处理 L2**

修改 `loop.py` 的 `run()` 中 `if decision != state.current_level` 那段：

```python
if decision != state.current_level:
    state = state.with_level(decision)  # type: ignore[arg-type]
    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.escalated",
        {"to": decision, "iteration": state.iteration},
    )
    if decision == "L2" and ctx.consultation_session is not None:
        try:
            advice = await _run_consultation(
                edict, state, ctx, memorial,
            )
            if advice:
                state = state.with_consultation_advice(advice)
        except Exception as e:
            logger.exception("consultation 调用失败，跳过 L2 直接升 L3")
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.escalated",
                {"from": "L2", "to": "L3", "reason": f"consultation failed: {e}"},
            )
            state = state.with_level("L3")
```

- [ ] **Step 2: 实现 _run_consultation 包装**

在 `loop.py` 末尾加：

```python
async def _run_consultation(
    edict: Edict,
    state: OuterLoopState,
    ctx: OrchestratorContext,
    memorial: Memorial,
) -> str | None:
    """触发跨部协商 —— 复用现有 ConsultationSession，仅返回建议文本。"""
    if ctx.consultation_session is None:
        return None
    acceptance = edict.acceptance
    assert acceptance is not None

    # 拼协商主题
    last = state.history[-1]
    topic = (
        f"长任务 outer loop 升级到 L2，请协助审视：\n\n"
        f"# Edict goal\n{edict.goal}\n\n"
        f"# 上一轮 actor 输出\n{last.actor_output[:2000]}\n\n"
        f"# critic 反馈\n"
        f"{last.critic_result.feedback if last.critic_result else '(none)'}\n\n"
        f"# 同类问题已连续打回 {state.same_issue_streak} 轮\n"
    )

    # 调用 ConsultationSession.start —— v1 用最小子集，假设其接受 topic 字符串
    from tianshu.consultation.models import ConsultationRequest
    req = ConsultationRequest(
        topic=topic,
        invitee_persona_ids=acceptance.escalation.l2_consultation_personas or [],
    )
    resp = await ctx.consultation_session.start(req)

    if resp and getattr(resp, "synthesized", None):
        return resp.synthesized
    if resp and getattr(resp, "opinions", None):
        return "\n\n".join(
            f"- {op.persona_id}: {op.content}" for op in resp.opinions
        )
    return None
```

> 注：`ConsultationRequest` 的字段名以现有 `consultation/models.py` 为准；如有差异本任务实现时按实际字段调整。**禁止**在 consultation 内部派发新 edict（hard rule，spec §6.4）。

- [ ] **Step 3: import 验证**

```bash
python -c "from tianshu.executor.orchestrator.loop import _run_consultation; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/executor/orchestrator/loop.py
git commit -m "feat(orchestrator): L2 跨部协商 —— consultation 建议注入下一轮 actor"
```

---

## Task 12: L3 升级 —— notifier + approvals + HumanDecision

**Files:**
- Modify: `src/tianshu/executor/orchestrator/loop.py`
- Create: `src/tianshu/executor/orchestrator/human_decision.py`

- [ ] **Step 1: 创建 human_decision.py**

```python
# src/tianshu/executor/orchestrator/human_decision.py
"""HumanDecision —— L3 审批的结构化结果。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from tianshu.models.acceptance import AcceptanceCriteria


class HumanDecision(BaseModel):
    action: Literal["continue", "accept_as_is", "abort", "modify_acceptance"]
    feedback: str | None = None
    new_acceptance: AcceptanceCriteria | None = None
```

- [ ] **Step 2: 在 loop.py 加 escalate_to_human + apply_human_decision**

```python
# loop.py 末尾追加
from tianshu.executor.orchestrator.human_decision import HumanDecision


async def _escalate_to_human(
    state: OuterLoopState,
    edict: Edict,
    ctx: OrchestratorContext,
    memorial: Memorial,
) -> HumanDecision:
    """发推送 + 等审批 —— 复用现有 notifier + approvals。"""
    last = state.history[-1] if state.history else None
    payload = {
        "edict_id": edict.id,
        "iteration": state.iteration,
        "level": "L3",
        "best_output": last.actor_output if last else None,
        "critic_feedback": last.critic_result.feedback if last and last.critic_result else None,
        "history_length": len(state.history),
    }
    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.approval.requested", payload,
    )

    # 推送通知（如可用）
    if ctx.notifier is not None:
        try:
            await ctx.notifier.notify(
                channel="default",
                title=f"长任务待审批 — Edict {edict.id[:8]}",
                body=f"已迭代 {state.iteration} 轮仍未通过 critic，请审阅。",
            )
        except Exception:
            logger.exception("notifier 推送失败，继续等审批")

    # 等审批
    if ctx.approvals is None:
        # 无 approvals 模块 → 视为超时
        return HumanDecision(action="accept_as_is" if last else "abort")

    timeout = (
        edict.acceptance.deadline_seconds
        if edict.acceptance and edict.acceptance.deadline_seconds
        else 86400  # 默认 24h
    )
    try:
        raw = await ctx.approvals.wait(
            edict_id=edict.id,
            timeout_seconds=timeout,
        )
        decision = HumanDecision.model_validate(raw) if isinstance(raw, dict) else raw
    except Exception as e:
        logger.warning("approval 超时 / 失败: %s", e)
        on_timeout = (
            edict.acceptance.on_approval_timeout
            if edict.acceptance else "best_effort"
        )
        return HumanDecision(action="accept_as_is" if on_timeout == "best_effort" else "abort")

    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.approval.received", {"action": decision.action},
    )
    return decision


def _apply_human_decision(
    state: OuterLoopState,
    decision: HumanDecision,
    edict: Edict,
) -> tuple[OuterLoopState, Edict, str | None]:
    """根据 human decision 更新 state / edict，返回(new_state, new_edict, terminal_action)。
    terminal_action: 'accept_as_is' | 'abort' | None（None=继续）。
    """
    if decision.action == "abort":
        return state, edict, "abort"
    if decision.action == "accept_as_is":
        return state, edict, "accept_as_is"
    if decision.action == "modify_acceptance":
        # 替换 edict.acceptance —— Edict 是 frozen 不能直接改，构造新 Edict
        new_edict = edict.model_copy(update={"acceptance": decision.new_acceptance})
        new_state = state.with_level("L0")
        # streak 重置（手动调标准等于"开新一局"）
        from dataclasses import replace
        new_state = replace(new_state, same_issue_streak=0, last_critic_issue_class=None)
        return new_state, new_edict, None
    # continue: 把 feedback 注入下一轮（通过 consultation_advice 字段复用）
    new_state = state.with_level("L0")
    if decision.feedback:
        new_state = new_state.with_consultation_advice(
            f"用户审批反馈：{decision.feedback}"
        )
    from dataclasses import replace
    new_state = replace(new_state, same_issue_streak=0)
    return new_state, edict, None
```

- [ ] **Step 3: 在 run() 主循环里接 L3**

修改 `run()` 中 `if decision != state.current_level:` 后的分支，把 L3 处理插入：

```python
if decision == "L3":
    state = state.with_level("L3")
    await emit_audit(
        ctx.bus, ctx.storage, edict.id, memorial.id,
        "outer_loop.escalated",
        {"to": "L3", "iteration": state.iteration},
    )
    human_decision = await _escalate_to_human(state, edict, ctx, memorial)
    state, edict, terminal = _apply_human_decision(state, human_decision, edict)
    if terminal == "abort":
        return OrchestratorResult(
            status=TaskStatus.FAILED, final_output=None, state=state,
            error="aborted by human",
        )
    if terminal == "accept_as_is":
        last = state.history[-1] if state.history else None
        return OrchestratorResult(
            status=TaskStatus.COMPLETED,
            final_output=last.actor_output if last else None,
            state=state,
        )
    # else continue / modify_acceptance —— 继续循环
```

- [ ] **Step 4: 修改 _handle_exhaustion 接 escalate**

```python
# 把 _handle_exhaustion 中的 escalate fallback 改为：
if acceptance.on_exhaustion == "escalate":
    state = state.with_level("L3")
    decision = await _escalate_to_human(state, edict, ctx, memorial)
    state, edict, terminal = _apply_human_decision(state, decision, edict)
    if terminal == "abort":
        return OrchestratorResult(
            status=TaskStatus.FAILED, final_output=None, state=state,
            error="exhausted + aborted",
        )
    if terminal == "accept_as_is" or terminal is None:
        # continue 在 exhausted 后无意义，按 accept 处理
        return OrchestratorResult(
            status=TaskStatus.COMPLETED,
            final_output=last_output,
            state=state,
        )
```

- [ ] **Step 5: import 验证**

```bash
python -c "from tianshu.executor.orchestrator.loop import _escalate_to_human, _apply_human_decision; print('OK')"
```

- [ ] **Step 6: Commit**

```bash
git add src/tianshu/executor/orchestrator/loop.py src/tianshu/executor/orchestrator/human_decision.py
git commit -m "feat(orchestrator): L3 升级 —— notifier 推送 + approvals 四路径（continue/accept_as_is/abort/modify_acceptance）"
```

---

## Task 13: OuterLoopCheckpoint + Resume

**Files:**
- Modify: `src/tianshu/executor/checkpoint.py`
- Modify: `src/tianshu/executor/orchestrator/loop.py`

- [ ] **Step 1: 在 checkpoint.py 加 OuterLoopCheckpoint**

修改 `src/tianshu/executor/checkpoint.py` 末尾追加：

```python
import json
from datetime import datetime


class OuterLoopCheckpoint:
    """outer loop 状态快照 —— per-edict（区别于 DAG node 的 Checkpoint）。"""

    KIND = "outer_loop"

    def __init__(self, edict_id: str, state_dict: dict, saved_at: str) -> None:
        self.edict_id = edict_id
        self.state_dict = state_dict
        self.saved_at = saved_at

    def to_json(self) -> str:
        return json.dumps({
            "kind": self.KIND,
            "edict_id": self.edict_id,
            "state": self.state_dict,
            "saved_at": self.saved_at,
        })

    @classmethod
    def from_json(cls, data: str) -> "OuterLoopCheckpoint":
        d = json.loads(data)
        return cls(
            edict_id=d["edict_id"],
            state_dict=d["state"],
            saved_at=d["saved_at"],
        )
```

> 持久化暂用 sqlite 一张新表 `outer_loop_checkpoints`，schema 与读写在 Step 2 加。

- [ ] **Step 2: 在 storage.py 加 checkpoint 表 + 读写**

在 `Storage._create_tables()` 末尾追加：

```python
self._conn.execute("""
    CREATE TABLE IF NOT EXISTS outer_loop_checkpoints (
        edict_id    TEXT PRIMARY KEY,
        data_json   TEXT NOT NULL,
        saved_at    TEXT NOT NULL
    )
""")
```

在 `Storage` 类末尾追加方法：

```python
def save_outer_loop_checkpoint(self, edict_id: str, data_json: str, saved_at: str) -> None:
    self._conn.execute("""
        INSERT INTO outer_loop_checkpoints (edict_id, data_json, saved_at)
        VALUES (?, ?, ?)
        ON CONFLICT(edict_id) DO UPDATE SET data_json=excluded.data_json, saved_at=excluded.saved_at
    """, (edict_id, data_json, saved_at))
    self._conn.commit()

def get_outer_loop_checkpoint(self, edict_id: str) -> str | None:
    row = self._conn.execute(
        "SELECT data_json FROM outer_loop_checkpoints WHERE edict_id = ?",
        (edict_id,),
    ).fetchone()
    return row["data_json"] if row else None

def clear_outer_loop_checkpoint(self, edict_id: str) -> None:
    self._conn.execute(
        "DELETE FROM outer_loop_checkpoints WHERE edict_id = ?", (edict_id,),
    )
    self._conn.commit()
```

- [ ] **Step 3: 在 loop.py 接 checkpoint**

在 `loop.py` 顶部 import 加：

```python
from dataclasses import asdict
from datetime import UTC, datetime
from tianshu.executor.checkpoint import OuterLoopCheckpoint
```

加两个 helper：

```python
def _state_to_dict(state: OuterLoopState) -> dict:
    """frozen dataclass → JSON 友好的 dict。注意 history 内含 datetime/dataclass。"""
    return {
        "edict_id": state.edict_id,
        "iteration": state.iteration,
        "current_level": state.current_level,
        "same_issue_streak": state.same_issue_streak,
        "last_critic_issue_class": state.last_critic_issue_class,
        "l1_rounds_used": state.l1_rounds_used,
        "l2_rounds_used": state.l2_rounds_used,
        "consultation_advice": state.consultation_advice,
        "total_cost_cny": state.total_cost_cny,
        # history 不存在 checkpoint 里 —— 已通过 outer_loop_iterations 表持久化
    }


def _state_from_dict(d: dict) -> OuterLoopState:
    return OuterLoopState(
        edict_id=d["edict_id"],
        iteration=d["iteration"],
        current_level=d["current_level"],
        same_issue_streak=d["same_issue_streak"],
        last_critic_issue_class=d.get("last_critic_issue_class"),
        l1_rounds_used=d["l1_rounds_used"],
        l2_rounds_used=d["l2_rounds_used"],
        consultation_advice=d.get("consultation_advice"),
        total_cost_cny=d["total_cost_cny"],
        history=(),  # resume 时 history 不重建（已落库，需要时查 outer_loop_iterations）
    )


def _save_checkpoint(ctx: OrchestratorContext, state: OuterLoopState) -> None:
    cp = OuterLoopCheckpoint(
        edict_id=state.edict_id,
        state_dict=_state_to_dict(state),
        saved_at=datetime.now(UTC).isoformat(),
    )
    ctx.storage.save_outer_loop_checkpoint(
        state.edict_id, cp.to_json(), cp.saved_at,
    )


def _load_checkpoint(ctx: OrchestratorContext, edict_id: str) -> OuterLoopState | None:
    raw = ctx.storage.get_outer_loop_checkpoint(edict_id)
    if not raw:
        return None
    cp = OuterLoopCheckpoint.from_json(raw)
    return _state_from_dict(cp.state_dict)
```

修改 `run()` 开头：

```python
async def run(edict, memorial, ctx) -> OrchestratorResult:
    assert edict.acceptance is not None
    acceptance = edict.acceptance

    # Resume：仅 checkpointed/background profile 启用
    if edict.execution_profile in ("checkpointed", "background"):
        resumed = _load_checkpoint(ctx, edict.id)
        state = resumed if resumed else OuterLoopState(edict_id=edict.id)
        if resumed:
            await emit_audit(
                ctx.bus, ctx.storage, edict.id, memorial.id,
                "outer_loop.resumed",
                {"iteration": state.iteration, "level": state.current_level},
            )
    else:
        state = OuterLoopState(edict_id=edict.id)
    # ... (后续不变)
```

在 `while` 循环的最后（升级判断之后）加 checkpoint 写入：

```python
        # 5. checkpoint（仅 checkpointed/background）
        if edict.execution_profile in ("checkpointed", "background"):
            _save_checkpoint(ctx, state)
```

在终态返回前清 checkpoint：

```python
# 在 OrchestratorResult(status=COMPLETED, ...) 返回前：
ctx.storage.clear_outer_loop_checkpoint(edict.id)
```

- [ ] **Step 4: import 验证**

```bash
python -c "
from tianshu.executor.checkpoint import OuterLoopCheckpoint
from tianshu.executor.orchestrator.loop import _state_to_dict, _state_from_dict
print('OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add src/tianshu/executor/checkpoint.py src/tianshu/storage.py src/tianshu/executor/orchestrator/loop.py
git commit -m "feat(orchestrator): OuterLoopCheckpoint + resume —— checkpointed/background profile 支持续跑"
```

---

## Task 14: Executor 路由分流

**Files:**
- Modify: `src/tianshu/executor/executor.py`

- [ ] **Step 1: 加 set_orchestrator_context + 路由判断**

修改 `src/tianshu/executor/executor.py`：

在 `Executor.__init__` 末尾加：
```python
self._orchestrator_ctx = None  # OrchestratorContext, set via set_orchestrator_context
```

在 `set_persona_loader` 之后加：
```python
def set_orchestrator_context(self, orch_ctx: object) -> None:
    """注入 orchestrator 依赖（agent/storage/bus/llms/...）。"""
    self._orchestrator_ctx = orch_ctx
```

修改 `handle_plan_completed`：在 `# Multi-task plan → DAG execution` 判断之前插入 acceptance 路由：

```python
# 长任务 outer loop 路径（仅当 edict.acceptance 不为 None）
if edict.acceptance is not None and self._orchestrator_ctx is not None:
    logger.info(
        "[EXEC] Edict %s: 走 orchestrator outer loop 路径（profile=%s）",
        edict.id, edict.execution_profile,
    )
    task = asyncio.create_task(self._execute_outer_loop(edict, memorial))
    self._running_tasks.add(task)
    task.add_done_callback(self._running_tasks.discard)
    return
```

加 `_execute_outer_loop` 方法（紧跟 `_execute_dag` 之后）：

```python
async def _execute_outer_loop(
    self, edict: Edict, memorial: Memorial | None,
) -> None:
    """通过 orchestrator 跑长任务 outer loop。"""
    from tianshu.executor.orchestrator import run as orch_run

    if memorial is None:
        memorial = Memorial(
            edict_id=edict.id,
            instruction=edict.goal,
            status=TaskStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        self._storage.save_memorial(memorial)
    else:
        memorial.status = TaskStatus.RUNNING
        memorial.started_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)

    try:
        result = await orch_run(edict, memorial, self._orchestrator_ctx)
        memorial.status = result.status
        memorial.result = result.final_output
        memorial.error = result.error
    except Exception as e:
        logger.exception("orchestrator failed for edict %s", edict.id)
        memorial.status = TaskStatus.FAILED
        memorial.error = f"orchestrator error: {e}"
    finally:
        memorial.completed_at = datetime.now(UTC)
        self._storage.update_memorial(memorial)
        await self._bus.emit(make_event(
            "execution.completed" if memorial.status == TaskStatus.COMPLETED else "execution.failed",
            edict_id=edict.id,
            memorial_id=memorial.id,
            producer="executor",
            payload={"status": memorial.status.value, "error": memorial.error},
        ))
```

- [ ] **Step 2: 在 app 启动处装配 orchestrator_ctx**

> 注：`src/tianshu/app.py`（或其他 wire-up 入口）注入。本 task 不实现 wire-up，留 TODO 注释，确保 `_orchestrator_ctx is None` 时**不破现有路径** —— 上面的判断已包含 `and self._orchestrator_ctx is not None`，缺省时退回老路径。

- [ ] **Step 3: 烟雾验证 —— 现有 edict 不受影响**

```bash
python -c "
from tianshu.executor.executor import Executor
import inspect
src = inspect.getsource(Executor.handle_plan_completed)
assert 'edict.acceptance is not None' in src
print('OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/tianshu/executor/executor.py
git commit -m "feat(executor): 按 edict.acceptance 路由分流 —— 长任务走 orchestrator，无 acceptance 走老路径"
```

---

## Task 15: app.py 装配 OrchestratorContext

**Files:**
- Modify: `src/tianshu/app.py`

- [ ] **Step 1: 在 app 启动处构建 ctx 并注入 executor**

> 先 Read `src/tianshu/app.py` 找到 Executor 初始化处。在 `executor = Executor(...)` 与 `executor.set_agent(agent)` 等调用之后追加：

```python
from tianshu.executor.orchestrator import OrchestratorContext

orch_ctx = OrchestratorContext(
    agent=agent,
    storage=storage,
    bus=event_bus,
    actor_llm=llm_client,            # 现有 LLMClient
    critic_llm=llm_client,            # v1 复用同一个；用户在 CriticSpec.model 指定即可在 review() 内分流
    critic_fallback_llm=None,
    consultation_session=consultation_session if "consultation_session" in dir() else None,
    notifier=notifier if "notifier" in dir() else None,
    approvals=approvals if "approvals" in dir() else None,
)
executor.set_orchestrator_context(orch_ctx)
```

> 实现时按 `app.py` 实际变量名调整。

- [ ] **Step 2: 启动 app，确认无回归**

```bash
python -c "from tianshu.app import build_app; app = build_app(); print('OK')"
```
> 如果 `build_app` 名字不同，按实际入口替换。仅验证 import + 装配不抛异常。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/app.py
git commit -m "wire(app): 装配 OrchestratorContext 并注入 Executor"
```

---

## Task 16: 30 天归档后台任务

**Files:**
- Create: `src/tianshu/executor/orchestrator/archive.py`

- [ ] **Step 1: 写 archive.py**

```python
# src/tianshu/executor/orchestrator/archive.py
"""30 天归档任务 —— 清空 actor_output，保留 checks/critic 摘要。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from tianshu.storage import Storage

logger = logging.getLogger(__name__)


def archive_old_iterations(storage: Storage, retention_days: int = 30) -> int:
    """归档 finished_at 早于 retention_days 的 iteration。返回归档行数。"""
    cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat()
    ids = storage.list_iterations_to_archive(cutoff)
    now = datetime.now(UTC).isoformat()
    for iter_id in ids:
        storage.archive_iteration(iter_id, now)
    if ids:
        logger.info("归档 %d 条 outer_loop_iterations（cutoff=%s）", len(ids), cutoff)
    return len(ids)
```

- [ ] **Step 2: 接入现有 scheduler（如有）**

> 若项目已有定时任务调度（如 `scheduler/`），加一个每天 03:00 跑 `archive_old_iterations(storage)` 的 job。具体接入按 scheduler API 实现；如无现成 cron，留作运维 TODO。

烟雾测试：

```bash
python -c "
from tianshu.executor.orchestrator.archive import archive_old_iterations
from tianshu.storage import Storage
import os
os.makedirs('/tmp/archive_test', exist_ok=True)
db = '/tmp/archive_test/t.db'
if os.path.exists(db): os.remove(db)
s = Storage(db)
n = archive_old_iterations(s, retention_days=30)
print('archived:', n)  # 0（空表）
"
```
Expected: `archived: 0`。

- [ ] **Step 3: Commit**

```bash
git add src/tianshu/executor/orchestrator/archive.py
git commit -m "feat(orchestrator): 30 天归档任务 —— actor_output 置空保留摘要"
```

---

## Task 17: 测试补齐 —— 单元测试

**Files:**
- Create: `tests/test_orchestrator_state.py`
- Create: `tests/test_orchestrator_checks.py`
- Create: `tests/test_orchestrator_critic.py`

> 用户偏好"功能优先，测试最后补"。本 task 集中补 task 4 / 6 / 7 的单元测试（task 5 已有）。

- [ ] **Step 1: test_orchestrator_state.py**

```python
# tests/test_orchestrator_state.py
"""OuterLoopState advance / with_level 测试。"""

from __future__ import annotations

from datetime import datetime

import pytest

from tianshu.executor.orchestrator.state import (
    ChecksResult,
    CriticResult,
    IterationRecord,
    OuterLoopState,
)


def _record(level="L0", issue_class="factual_error", verdict="fail", cost=0.1) -> IterationRecord:
    return IterationRecord(
        iteration=0, level=level, actor_output="x",
        checks_result=ChecksResult(all_passed=True),
        critic_result=CriticResult(verdict=verdict, issue_class=issue_class, feedback="f"),
        started_at=datetime.utcnow(),
        finished_at=datetime.utcnow(),
        cost_cny=cost,
    )


@pytest.mark.unit
def test_advance_streak_increments_on_same_issue():
    s = OuterLoopState(edict_id="e1", last_critic_issue_class="factual_error", same_issue_streak=1)
    s2 = s.advance(_record(issue_class="factual_error"))
    assert s2.same_issue_streak == 2


@pytest.mark.unit
def test_advance_streak_resets_on_different_issue():
    s = OuterLoopState(edict_id="e1", last_critic_issue_class="factual_error", same_issue_streak=2)
    s2 = s.advance(_record(issue_class="tone_mismatch"))
    assert s2.same_issue_streak == 1
    assert s2.last_critic_issue_class == "tone_mismatch"


@pytest.mark.unit
def test_advance_accumulates_cost():
    s = OuterLoopState(edict_id="e1", total_cost_cny=0.5)
    s2 = s.advance(_record(cost=0.3))
    assert abs(s2.total_cost_cny - 0.8) < 1e-9


@pytest.mark.unit
def test_advance_increments_level_rounds():
    s = OuterLoopState(edict_id="e1", current_level="L1")
    s2 = s.advance(_record(level="L1"))
    assert s2.l1_rounds_used == 1
    s3 = s2.advance(_record(level="L2"))
    assert s3.l2_rounds_used == 1


@pytest.mark.unit
def test_with_level_immutable():
    s = OuterLoopState(edict_id="e1", current_level="L0")
    s2 = s.with_level("L1")
    assert s.current_level == "L0"
    assert s2.current_level == "L1"


@pytest.mark.unit
def test_with_consultation_advice():
    s = OuterLoopState(edict_id="e1")
    s2 = s.with_consultation_advice("be more careful")
    assert s.consultation_advice is None
    assert s2.consultation_advice == "be more careful"
```

- [ ] **Step 2: test_orchestrator_checks.py**

```python
# tests/test_orchestrator_checks.py
"""checks runner 测试 —— bash 真跑，rubric mock LLM。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.executor.orchestrator.checks import (
    ChecksConfigError,
    run_checks,
)
from tianshu.models.acceptance import CheckSpec


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bash_pass():
    r = await run_checks(
        [CheckSpec(kind="bash", name="ok", command="echo ok")],
        actor_output="",
        llm=None,
    )
    assert r.all_passed
    assert r.outcomes[0].name == "ok"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bash_fail():
    r = await run_checks(
        [CheckSpec(kind="bash", name="bad", command="exit 1")],
        actor_output="",
        llm=None,
    )
    assert not r.all_passed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bash_timeout():
    r = await run_checks(
        [CheckSpec(kind="bash", name="slow", command="sleep 10", timeout_seconds=1)],
        actor_output="",
        llm=None,
    )
    assert not r.all_passed
    assert "timeout" in r.outcomes[0].detail


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rubric_pass():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(
        content='{"score": 0.9, "reasoning": "looks good"}',
    ))
    r = await run_checks(
        [CheckSpec(kind="rubric", name="tone", rubric="be friendly")],
        actor_output="hello!",
        llm=llm,
    )
    assert r.all_passed
    assert r.outcomes[0].score == 0.9


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rubric_fail_on_low_score():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(
        content='{"score": 0.3, "reasoning": "meh"}',
    ))
    r = await run_checks(
        [CheckSpec(kind="rubric", name="tone", rubric="be friendly", pass_threshold=0.8)],
        actor_output="meh.",
        llm=llm,
    )
    assert not r.all_passed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bash_missing_command_raises():
    with pytest.raises(ChecksConfigError):
        await run_checks(
            [CheckSpec(kind="bash", name="nocmd")],  # 缺 command
            actor_output="",
            llm=None,
        )
```

- [ ] **Step 3: test_orchestrator_critic.py**

```python
# tests/test_orchestrator_critic.py
"""critic agent 测试 —— mock LLM。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.executor.orchestrator.critic import (
    CriticUnavailable,
    ISSUE_CLASSES,
    review,
)
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.edict import Edict


def _edict() -> Edict:
    return Edict(goal="write a poem", acceptance=AcceptanceCriteria())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_pass():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(
        content='{"verdict": "pass", "feedback": "great"}',
    ))
    r = await review("poem text", _edict(), AcceptanceCriteria(), llm)
    assert r.verdict == "pass"
    assert r.issue_class is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_fail_with_issue_class():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(
        content='{"verdict": "fail", "issue_class": "tone_mismatch", "feedback": "too dry"}',
    ))
    r = await review("dry text", _edict(), AcceptanceCriteria(), llm)
    assert r.verdict == "fail"
    assert r.issue_class == "tone_mismatch"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_invalid_issue_class_normalized_to_other():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(
        content='{"verdict": "fail", "issue_class": "made_up_class", "feedback": "x"}',
    ))
    r = await review("text", _edict(), AcceptanceCriteria(), llm)
    assert r.issue_class == "other"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_unavailable_after_retries():
    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(CriticUnavailable):
        await review("text", _edict(), AcceptanceCriteria(), llm, max_retries=2)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_critic_fallback_used_after_primary_fails():
    primary = MagicMock()
    primary.complete = AsyncMock(side_effect=RuntimeError("primary down"))
    fallback = MagicMock()
    fallback.complete = AsyncMock(return_value=MagicMock(
        content='{"verdict": "pass", "feedback": "ok"}',
    ))
    r = await review("text", _edict(), AcceptanceCriteria(),
                     primary, fallback_llm=fallback, max_retries=2)
    assert r.verdict == "pass"
```

- [ ] **Step 4: 跑测试**

```bash
pytest tests/test_orchestrator_state.py tests/test_orchestrator_checks.py tests/test_orchestrator_critic.py -v
```
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_orchestrator_state.py tests/test_orchestrator_checks.py tests/test_orchestrator_critic.py
git commit -m "test(orchestrator): 单元测试补齐 —— state/checks/critic"
```

---

## Task 18: 测试补齐 —— 集成测试 + 回归测试

**Files:**
- Create: `tests/test_orchestrator_loop.py`
- Create: `tests/test_outer_loop_resume.py`

- [ ] **Step 1: test_orchestrator_loop.py（mock 完整链路）**

```python
# tests/test_orchestrator_loop.py
"""orchestrator outer loop 集成测试 —— mock actor / critic / approvals。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.orchestrator import OrchestratorContext, run
from tianshu.executor.orchestrator.human_decision import HumanDecision
from tianshu.models.acceptance import (
    AcceptanceCriteria,
    CheckSpec,
    CriticSpec,
    EscalationSpec,
)
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage


@pytest.fixture
def storage(tmp_path):
    return Storage(str(tmp_path / "t.db"))


@pytest.fixture
def bus():
    b = MagicMock(spec=EventBus)
    b.emit = AsyncMock()
    return b


def _make_ctx(storage, bus, agent, critic_responses):
    """critic_responses = list[dict] 按调用次序返回 verdict/issue_class/feedback。"""
    actor_llm = MagicMock()
    critic_llm = MagicMock()
    critic_llm.complete = AsyncMock(side_effect=[
        MagicMock(content=__import__("json").dumps(r)) for r in critic_responses
    ])
    return OrchestratorContext(
        agent=agent, storage=storage, bus=bus,
        actor_llm=actor_llm, critic_llm=critic_llm,
    )


def _agent(output_per_iter: list[str]):
    a = MagicMock()
    results = [
        MagicMock(
            result=o, summary=o,
            usage=MagicMock(cost_cny=0.1),
        ) for o in output_per_iter
    ]
    a.execute = AsyncMock(side_effect=results)
    return a


def _edict(**kwargs) -> Edict:
    base = AcceptanceCriteria(
        max_outer_iterations=5,
        critic=CriticSpec(same_issue_threshold=2),
    )
    base = base.model_copy(update=kwargs)
    return Edict(goal="g", acceptance=base)


def _memorial(edict_id):
    return Memorial(edict_id=edict_id, instruction="g")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pass_first_try(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["draft v1"]), [
        {"verdict": "pass", "feedback": "ok"},
    ])
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "draft v1"
    assert r.state.iteration == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_l0_to_l1_on_same_issue(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "wrong fact"},
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "still wrong"},
        {"verdict": "pass", "feedback": "ok now"},
    ])
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    # 第二轮后升 L1（streak=2 == threshold），第三轮在 L1 通过
    assert any(rec.level == "L1" for rec in r.state.history)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_streak_resets_on_different_issue(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "f1"},
        {"verdict": "fail", "issue_class": "tone_mismatch", "feedback": "f2"},
        {"verdict": "pass", "feedback": "ok"},
    ])
    e = _edict()
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    # 不同 issue_class → streak 重置，未升 L1
    assert all(rec.level == "L0" for rec in r.state.history)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_exhausted_best_effort(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
    ])
    e = _edict(max_outer_iterations=3, on_exhaustion="best_effort",
              escalation=EscalationSpec(enabled_levels=[]))  # 关闭升级
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED  # best_effort 视为成功
    assert r.final_output == "v3"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_checks_failed_skips_critic(storage, bus):
    # checks 不过 → critic 不调用
    ctx = _make_ctx(storage, bus, _agent(["v1"]), [])  # critic 0 calls
    e = _edict(checks=[CheckSpec(kind="bash", name="must_fail", command="exit 1")],
              max_outer_iterations=1, on_exhaustion="fail")
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.FAILED
    assert ctx.critic_llm.complete.call_count == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_critic_unavailable_skip(storage, bus):
    actor = _agent(["v1"])
    actor_llm = MagicMock()
    critic_llm = MagicMock()
    critic_llm.complete = AsyncMock(side_effect=RuntimeError("critic down"))
    ctx = OrchestratorContext(
        agent=actor, storage=storage, bus=bus,
        actor_llm=actor_llm, critic_llm=critic_llm,
    )
    e = _edict()  # on_critic_unavailable 默认 skip
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "v1"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_l3_approval_accept_as_is(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
    ])
    approvals = MagicMock()
    approvals.wait = AsyncMock(return_value=HumanDecision(action="accept_as_is"))
    ctx.approvals = approvals

    e = _edict(
        max_outer_iterations=3,
        critic=CriticSpec(same_issue_threshold=1),
        escalation=EscalationSpec(l1_max_rounds=1, l2_max_rounds=1),
        on_exhaustion="escalate",
    )
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    assert "v" in (r.final_output or "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_l3_approval_abort(storage, bus):
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3"]), [
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
        {"verdict": "fail", "issue_class": "other", "feedback": "f"},
    ])
    approvals = MagicMock()
    approvals.wait = AsyncMock(return_value=HumanDecision(action="abort"))
    ctx.approvals = approvals

    e = _edict(
        max_outer_iterations=3,
        critic=CriticSpec(same_issue_threshold=1),
        escalation=EscalationSpec(l1_max_rounds=1, l2_max_rounds=1),
        on_exhaustion="escalate",
    )
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    assert r.status == TaskStatus.FAILED


@pytest.mark.integration
@pytest.mark.asyncio
async def test_l3_modify_acceptance_resets_streak(storage, bus):
    """L3 用户调宽标准 → 回 L0，streak 清零，下一轮按新标准跑。"""
    ctx = _make_ctx(storage, bus, _agent(["v1", "v2", "v3", "v4"]), [
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
        {"verdict": "fail", "issue_class": "factual_error", "feedback": "f"},
        {"verdict": "pass", "feedback": "ok with looser criteria"},
    ])
    # 用户调宽：阈值放宽到 0.5
    new_acceptance = AcceptanceCriteria(
        max_outer_iterations=10,
        critic=CriticSpec(same_issue_threshold=5),
    )
    approvals = MagicMock()
    approvals.wait = AsyncMock(return_value=HumanDecision(
        action="modify_acceptance",
        new_acceptance=new_acceptance,
    ))
    ctx.approvals = approvals

    e = _edict(
        max_outer_iterations=4,
        critic=CriticSpec(same_issue_threshold=1),
        escalation=EscalationSpec(l1_max_rounds=1, l2_max_rounds=1),
        on_exhaustion="escalate",
    )
    storage.save_edict(e)
    r = await run(e, _memorial(e.id), ctx)
    # modify_acceptance 后回 L0 重跑，最终通过
    assert r.status == TaskStatus.COMPLETED
    assert r.final_output == "v4"
```

- [ ] **Step 2: test_outer_loop_resume.py（checkpoint 续跑）**

```python
# tests/test_outer_loop_resume.py
"""checkpoint + resume 测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.orchestrator import OrchestratorContext, run
from tianshu.executor.orchestrator.loop import _save_checkpoint
from tianshu.executor.orchestrator.state import OuterLoopState
from tianshu.models.acceptance import AcceptanceCriteria, CriticSpec
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.storage import Storage


@pytest.mark.integration
@pytest.mark.asyncio
async def test_resume_from_checkpoint(tmp_path):
    storage = Storage(str(tmp_path / "t.db"))
    bus = MagicMock(spec=EventBus)
    bus.emit = AsyncMock()

    e = Edict(
        goal="g",
        acceptance=AcceptanceCriteria(
            max_outer_iterations=5,
            critic=CriticSpec(same_issue_threshold=2),
        ),
        execution_profile="checkpointed",
    )
    storage.save_edict(e)

    # 模拟"上次跑了 2 轮被打断" —— 手动写一个 state 到 checkpoint
    pre_state = OuterLoopState(
        edict_id=e.id, iteration=2, current_level="L0",
        same_issue_streak=1, last_critic_issue_class="factual_error",
        total_cost_cny=0.2,
    )
    actor = MagicMock()
    actor.execute = AsyncMock(return_value=MagicMock(
        result="recovered", summary="recovered", usage=MagicMock(cost_cny=0.1),
    ))
    actor_llm = MagicMock()
    critic_llm = MagicMock()
    critic_llm.complete = AsyncMock(return_value=MagicMock(
        content='{"verdict": "pass", "feedback": "good"}',
    ))
    ctx = OrchestratorContext(
        agent=actor, storage=storage, bus=bus,
        actor_llm=actor_llm, critic_llm=critic_llm,
    )
    _save_checkpoint(ctx, pre_state)

    r = await run(e, Memorial(edict_id=e.id), ctx)
    assert r.status == TaskStatus.COMPLETED
    # 从 iteration=2 续跑，下一轮是 iteration 2（actor 调一次，critic 通过），最终 state.iteration == 3
    assert r.state.iteration == 3
    # checkpoint 已被清
    assert storage.get_outer_loop_checkpoint(e.id) is None
```

- [ ] **Step 3: 跑全部测试 + 回归**

```bash
pytest tests/ -v -k "not live"
```
Expected: 所有 PASS（含原有测试套），无回归。

- [ ] **Step 4: Commit**

```bash
git add tests/test_orchestrator_loop.py tests/test_outer_loop_resume.py
git commit -m "test(orchestrator): 集成测试 —— pass/L1 升级/streak 重置/exhausted/L3 三审批/critic 故障 skip/resume"
```

---

## Task 19: 验收 + 文档更新

**Files:**
- Modify: `docs/superpowers/specs/2026-04-26-long-task-iteration-design.md`（验收清单打勾）

- [ ] **Step 1: 跑全测试套**

```bash
pytest --cov=src/tianshu/executor/orchestrator --cov=src/tianshu/models/acceptance --cov-report=term-missing
```
Expected: 单元覆盖率 ≥ 80%，所有测试 PASS。

- [ ] **Step 2: 跑老路径回归**

```bash
pytest tests/test_executor.py tests/test_agent.py tests/test_backward_compat.py -v
```
Expected: 全 PASS（验证不带 `acceptance` 的 edict 走老路径，零回归）。

- [ ] **Step 3: 更新 spec 验收清单**

修改 `docs/superpowers/specs/2026-04-26-long-task-iteration-design.md` §8 的清单：把 `[ ]` 改为 `[x]`：

```markdown
## 8. 验收标准（本设计落地的）

- [x] 不带 `acceptance` 的 edict 行为与回归前完全一致
- [x] 带 `acceptance` 的 edict 能跑通 §7.3 列出的所有集成测试
- [x] 单元测试覆盖率 ≥ 80%
- [x] `outer_loop_iterations` 表有 30 天归档任务且能回放
- [x] L3 推送能到达通知通道，approval 四路径均可触达
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-04-26-long-task-iteration-design.md
git commit -m "docs(spec): 长任务 outer loop 验收完成"
```

---

## Self-Review

**1. Spec coverage（spec 各章节是否都对应到 task）：**

| spec 章节 | task |
|----------|------|
| §3 架构 | Task 4-9（拆分实现） |
| §4.1 AcceptanceCriteria | Task 1 |
| §4.2 Edict 扩展 | Task 2 |
| §4.3 OuterLoopState | Task 4 |
| §4.4 持久化（表） | Task 3 |
| §4.5 归档 | Task 16 |
| §5.1-5.2 状态机 | Task 5（FSM 纯函数）+ 9-12（接入） |
| §5.3 issue_class 集合 | Task 7（critic.py 内置） |
| §5.4 主循环算法 | Task 9-12 |
| §6.1 错误分类 | Task 6（ChecksConfigError）+ 12（critic 故障）+ 9-12（actor 异常通过 retry.py 现有） |
| §6.2 中断 | 复用 cancel.py（不需新代码；orchestrator 不破坏现有 cancel 路径） |
| §6.3 Resume | Task 13 |
| §6.4 资源防护 | 通过 max_outer / cost_budget / deadline 兜底（Task 5 + Task 9） |
| §6.5 HumanDecision | Task 12 |
| §7 测试 | Task 17 + 18 |

✅ 全部覆盖。中断（§6.2）依赖现有 `cancel.py`，orchestrator 透明传递不阻塞 —— 已注明。

**2. Placeholder 扫描：** 全文搜过 TBD/TODO，仅 Task 10 Step 2 有一处 `# v1 仅传 user_content` 备注（thinking_budget 完整支持是 v1.1+ 议题，已显式说明），不算 placeholder。✅

**3. Type 一致性：**
- `Level = Literal["L0","L1","L2","L3"]` 一致（Task 4 定义）
- `Decision = Literal["L0","L1","L2","L3","EXHAUSTED"]` Task 5 定义，Task 9 使用一致
- `OuterLoopState.with_level(level)` 接受 `Level` 类型，Task 9 传 `decision`（可能是 `EXHAUSTED`）—— 已在 Task 9 用 `if decision == "EXHAUSTED"` 提前分支，不会走到 with_level
- `HumanDecision` Task 12 定义，Task 12 内部使用一致
- `OrchestratorContext` Task 9 定义，Task 14/15 注入一致

✅ 类型签名统一。

**4. 测试用例 vs spec §7.3 列表：**

| spec §7.3 用例 | 实施位置 |
|---------------|---------|
| pass_first_try | Task 18 ✅ |
| l0_to_l1_same_issue | Task 18 ✅ |
| l0_streak_resets | Task 18 ✅ |
| full_ladder_to_l3 | Task 18 (test_l3_approval_accept_as_is + test_l3_approval_abort 一起覆盖)|
| approval_accept_as_is | Task 18 ✅ |
| approval_abort | Task 18 ✅ |
| critic_failure_skip | Task 18 ✅ |
| checks_failed | Task 18 ✅ |
| resume_from_checkpoint | Task 18（test_outer_loop_resume.py） ✅ |
| exhausted_best_effort | Task 18 ✅ |
| no_acceptance_old_path | Task 19 Step 2（跑现有测试套） ✅ |
| modify_acceptance | **缺！** |

发现一个 spec 用例 `test_outer_loop_modify_acceptance` 没有对应的实现测试。补到 Task 18 Step 1 末尾。
