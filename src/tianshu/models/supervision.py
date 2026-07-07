"""SupervisionReport — 长任务终态后由监督官 (critic persona) 产出的总评报告。

4 个结构化章节：观察到的问题 / 做得好 / 做得不够 / 建议。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field

from tianshu.models.common import TaskStatus


class SupervisionReport(BaseModel):
    edict_id: str
    memorial_id: str
    persona_id: str
    persona_name: str
    final_status: TaskStatus
    iterations_count: int
    total_cost_cny: float
    issues_observed: list[str] = Field(default_factory=list)
    well_done: list[str] = Field(default_factory=list)
    poorly_done: list[str] = Field(default_factory=list)
    recommendation: str | None = None
    raw_feedback: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
