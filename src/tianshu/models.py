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


class EdictStatus(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class UsageSummary(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class Edict(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    title: str = ""
    goal: str
    context: str | None = None
    status: EdictStatus = EdictStatus.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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


class EdictCreateRequest(BaseModel):
    goal: str = Field(min_length=1)
    context: str | None = None


class FollowUpRequest(BaseModel):
    instruction: str = Field(min_length=1)
    context: str | None = None


class EdictUpdateRequest(BaseModel):
    title: str | None = None
    goal: str | None = None
    context: str | None = None


class EdictStatusUpdateRequest(BaseModel):
    status: EdictStatus


class LLMConfig(BaseModel):
    model: str
    api_key_masked: str
    api_base: str
    max_retries: int
    temperature: float
    top_p: float
    max_tokens: int
    enabled: bool


class LLMConfigUpdateRequest(BaseModel):
    model: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    max_retries: int | None = Field(default=None, ge=0, le=10)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    max_tokens: int | None = Field(default=None, ge=1, le=128000)
    enabled: bool | None = None


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None
    metadata: dict | None = None
