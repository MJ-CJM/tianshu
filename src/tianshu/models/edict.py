"""Edict model — the imperial decree that initiates work."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from ulid import ULID

from tianshu.models.common import EdictStatus


class EdictSchedule(BaseModel):
    type: Literal["immediate", "once", "cron"] = "immediate"
    at: datetime | None = None
    cron: str | None = None
    timezone: str = "UTC"


class EdictDispatch(BaseModel):
    channels: list[str] = Field(default_factory=list)
    mode: Literal["broadcast", "first"] = "broadcast"
    notify_on_failure: bool = True
    target: str | None = None


class EdictRuntime(BaseModel):
    timeout_seconds: int = 300
    max_iterations: int = 20
    max_concurrency: int = 1
    retry_limit: int = 0
    token_budget: int | None = None
    cost_budget_cny: float | None = None
    approval_required_tools: list[str] = Field(default_factory=list)


class Edict(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    title: str = ""
    goal: str
    context: str | None = None
    status: EdictStatus = EdictStatus.OPEN
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Phase 1 fields
    idempotency_key: str | None = None
    source: Literal["cli", "api", "channel", "scheduler"] = "api"
    submitter: str | None = None
    constraints: list[str] = Field(default_factory=list)
    output_format: str | None = None
    priority: Literal["urgent", "normal", "low"] = "normal"
    review_policy: Literal["never", "on_failure", "on_flag", "always"] = "never"
    schedule: EdictSchedule = Field(default_factory=EdictSchedule)
    dispatch: EdictDispatch | None = None
    runtime: EdictRuntime = Field(default_factory=EdictRuntime)
    assigned_persona_id: str | None = None  # None = 内阁决策; 具体 ID = 直接指派
    planner_persona_id: str | None = None  # None = 全局配置规划; 具体 ID = 指定内阁 persona 的 LLM 配置
    plan_review: bool = False  # True = 规划需人工审批后再执行
    metadata: dict[str, Any] = Field(default_factory=dict)
