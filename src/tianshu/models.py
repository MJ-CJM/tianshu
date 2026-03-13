"""Core data models - Edict and Memorial."""

from datetime import UTC, datetime
from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field
from ulid import ULID

T = TypeVar("T")


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UsageSummary(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Edict(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    goal: str
    context: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Memorial(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    edict_id: str
    status: TaskStatus = TaskStatus.SUBMITTED
    summary: str | None = None
    result: str | None = None
    usage: UsageSummary = Field(default_factory=UsageSummary)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None


class EdictCreateRequest(BaseModel):
    goal: str = Field(min_length=1)
    context: str | None = None


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None
    metadata: dict | None = None
