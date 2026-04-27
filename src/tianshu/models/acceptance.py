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
    # 监督官 IDs（多个并发监督，按 acceptance 配置的聚合规则汇总）
    persona_ids: list[str] = Field(default_factory=list)
    # 兼容旧版单 persona 字段；resolve 时若 persona_ids 为空则回退为 [persona_id]
    persona_id: str | None = None
    model: str | None = None
    same_issue_threshold: int = 2
    # 严苛度：lenient=合格即 pass / balanced=高标准 / strict=优秀才 pass
    strictness: Literal["lenient", "balanced", "strict"] = "lenient"

    def effective_persona_ids(self) -> list[str]:
        """返回实际生效的 persona_ids 列表（兼容旧版单 persona_id）。"""
        if self.persona_ids:
            return self.persona_ids
        if self.persona_id:
            return [self.persona_id]
        return []


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
    # 最少迭代轮数（≥2 时强制持续优化模式 — 即使 critic pass 也继续，把 critic 改进建议反哺 actor）
    min_outer_iterations: int = 1
    max_outer_iterations: int = 5
    deadline_seconds: int | None = None
    on_exhaustion: Literal["escalate", "best_effort", "fail"] = "escalate"
    on_critic_unavailable: Literal["escalate", "skip"] = "skip"
    on_approval_timeout: Literal["fail", "best_effort"] = "best_effort"
