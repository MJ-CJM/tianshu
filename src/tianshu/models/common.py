"""Shared types, enums, and base models."""

from datetime import UTC, datetime
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SCHEDULED = "scheduled"
    PLANNING = "planning"
    AUDITING = "auditing"
    NEEDS_REVIEW = "needs_review"


class EdictStatus(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


VALID_PRIORITIES = ("urgent", "normal", "low")
VALID_EXECUTION_PROFILES = ("foreground", "checkpointed", "background")

EDICT_STATUS_LABELS: dict[str, str] = {
    "open": "进行中",
    "completed": "已完成",
    "cancelled": "已取消",
}

MEMORIAL_STATUS_LABELS: dict[str, str] = {
    "submitted": "排队中",
    "scheduled": "已调度",
    "planning": "规划中",
    "running": "执行中",
    "auditing": "审计中",
    "needs_review": "待人工复核",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}


class UsageSummary(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cost_cny: float = 0.0
    # 上游网关回显的真实模型与 LiteLLM 识别的 provider，用于诊断中转网关静默改写。
    actual_model: str | None = None
    upstream_provider: str | None = None


class AuditResult(BaseModel):
    verdict: str = "pass"  # pass | flag | block
    reasons: list[str] = Field(default_factory=list)
    rules_checked: int = 0
    llm_reviewed: bool = False


class ArtifactRef(BaseModel):
    name: str
    type: str = "file"
    path: str | None = None
    url: str | None = None


class TimelineItem(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event: str
    detail: str | None = None


class ApiResponse(BaseModel, Generic[T]):  # noqa: UP046 -- pydantic 泛型模型迁移 PEP 695 语法有兼容性风险，暂缓
    success: bool
    data: T | None = None
    error: str | None = None
    metadata: dict | None = None
