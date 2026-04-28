"""Outer loop state — frozen dataclasses, never mutated."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

from tianshu.models.common import UsageSummary

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
    cost_cny: float = 0.0                  # 多 critic 时为聚合后总成本
    improvement_hints: str | None = None   # PASS 时也可给改进建议（持续优化模式注入下一轮 actor）
    usage: UsageSummary | None = None      # critic LLM 完整用量；多 critic 时为聚合值；用于回写 memorial.usage


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
