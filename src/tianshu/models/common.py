"""Shared types, enums, and base models."""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field
from ulid import ULID

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


class UsageSummary(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


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


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None
    metadata: dict | None = None
