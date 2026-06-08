"""Memorial model — the execution record / report."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from ulid import ULID

from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.common import (
    ArtifactRef,
    AuditResult,
    TaskStatus,
    TimelineItem,
    UsageSummary,
)


class Memorial(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    edict_id: str
    instruction: str | None = None
    status: TaskStatus = TaskStatus.SUBMITTED
    summary: str | None = None
    result: str | None = None
    # 最终交付物：外发渠道（飞书/邮件等）单独呈现的"用户关心的产物"。
    # 与 result 分离 —— result 含规划/调研/中间步骤的全量记录用于审计，
    # final_output 只是最后一步任务的产出。None 时外发回退到 result。
    final_output: str | None = None
    usage: UsageSummary = Field(default_factory=UsageSummary)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # Phase 1 fields
    attempt: int = 1
    parent_memorial_id: str | None = None
    review_status: Literal[
        "not_required", "pending", "approved", "rejected"
    ] = "not_required"
    audit: AuditResult | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    timeline: list[TimelineItem] = Field(default_factory=list)
    # Phase 3 fields
    dag_node_id: str | None = None
    persona_id: str | None = None
    # 2026-04-28: follow-up 时本次 memorial 单独覆盖 edict 配置（不持久化到 edict）。
    # runtime_override：dict，仅含用户实际填写的字段，executor 合并时 edict.runtime.model_copy(update=...)
    # acceptance_override：完整 AcceptanceCriteria，整体替换 edict.acceptance（None = 沿用）
    runtime_override: dict[str, Any] | None = None
    acceptance_override: AcceptanceCriteria | None = None
    # DeepSeek reasoner / 新版 thinking-mode 模型要求多轮对话回传上一轮 reasoning_content；
    # 这里持久化最终 final response 的 reasoning，供 follow_up 重建 history 时回传。
    reasoning_content: str | None = None
    # 平行位面归因（2026-06-07）
    universe_id: str | None = None
    # 显式反馈分（2026-06-08）：+1 赞 / -1 踩 / 0 无
    feedback_score: int = 0
