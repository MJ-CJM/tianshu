"""Decree model — human review decisions on memorials."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from ulid import ULID


class Decree(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    memorial_id: str
    # guide(迭代 5):驳回+指导——驳回本次工具但给纠正意见,agent 据此换方式续跑
    action: Literal["approve", "reject", "retry", "amend", "cancel", "guide"]
    comment: str | None = None
    amended_goal: str | None = None
    actor: str = "human"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Spec Section 4: session rule 升级支持
    grant_scope: Literal["once", "edict", "always"] | None = None
    grant_reason: str | None = None
