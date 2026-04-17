"""Memorial model — the execution record / report."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from ulid import ULID

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
