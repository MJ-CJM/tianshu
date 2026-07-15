"""Strict immutable models for durable run continuations."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import JsonValue, canonical_sha256


class RunPhase(StrEnum):
    SUBMITTED = "submitted"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_DECISION = "waiting_decision"
    PAUSED = "paused"
    AUDITING = "auditing"
    COMPLETED = "completed"
    FAILED = "failed"


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _non_blank_optional(value: str | None) -> str | None:
    return _non_blank(value) if value is not None else None


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class PersistedUsageSummaryV1(_StrictModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    cost_cny: float = Field(ge=0)
    actual_model: str | None
    upstream_provider: str | None


class PersistedChatMessageV1(_StrictModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | tuple[dict[str, JsonValue], ...]
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[dict[str, JsonValue], ...] | None = None
    reasoning_content: str | None = None


class ToolProposalV1(_StrictModel):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, JsonValue]
    arguments_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_tier: str
    policy_rule_id: str | None
    proposed_at: datetime

    _validate_non_blank = field_validator("tool_call_id", "tool_name", "tool_tier")(_non_blank)
    _validate_optional_rule = field_validator("policy_rule_id")(_non_blank_optional)
    _normalize_proposed_at = field_validator("proposed_at")(_normalize_utc)

    @model_validator(mode="after")
    def validate_arguments_hash(self) -> Self:
        if self.arguments_hash != canonical_sha256(self.arguments):
            raise ValueError("arguments_hash does not match arguments")
        return self


class IterationSummaryV1(_StrictModel):
    iteration: int = Field(ge=0)
    level: Literal["L0", "L1", "L2", "L3"]
    output_artifact_ref: str | None
    critic_verdict: str | None
    critic_issue_class: str | None
    feedback: str | None
    usage: PersistedUsageSummaryV1
    completed_at: datetime

    _validate_verdict = field_validator("critic_verdict")(_non_blank_optional)
    _normalize_completed_at = field_validator("completed_at")(_normalize_utc)


class AgentContinuationV1(_StrictModel):
    kind: Literal["agent"] = "agent"
    messages: tuple[PersistedChatMessageV1, ...]
    pending_tool: ToolProposalV1 | None
    iteration: int = Field(ge=0)
    usage: PersistedUsageSummaryV1
    checkpoint_ref: str | None
    pending_decision_id: str | None = None
    resolved_decision_id: str | None
    side_effect_cursor: int = Field(ge=0)
    plan_ref: str | None = None
    plan_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_plan_binding(self) -> Self:
        if (self.plan_ref is None) != (self.plan_hash is None):
            raise ValueError("plan_ref and plan_hash must be provided together")
        if self.plan_ref is not None:
            _non_blank(self.plan_ref)
        return self


class OuterLoopContinuationV1(_StrictModel):
    kind: Literal["outer_loop"] = "outer_loop"
    level: Literal["L0", "L1", "L2", "L3"]
    iteration: int = Field(ge=0)
    best_output: str | None
    feedback: str | None
    steer: str | None
    history: tuple[IterationSummaryV1, ...]
    same_issue_streak: int = Field(ge=0)
    last_critic_issue_class: str | None
    l1_rounds_used: int = Field(ge=0)
    l2_rounds_used: int = Field(ge=0)
    consultation_advice: str | None
    usage: PersistedUsageSummaryV1
    total_cost_cny: Decimal = Field(ge=0)
    checkpoint_ref: str | None
    pending_decision_id: str | None = None
    resolved_decision_id: str | None
    side_effect_cursor: int = Field(ge=0)


ContinuationV1 = Annotated[
    AgentContinuationV1 | OuterLoopContinuationV1,
    Field(discriminator="kind"),
]


class RunStateV1(_StrictModel):
    memorial_id: str
    edict_id: str
    schema_version: Literal[1] = 1
    phase: RunPhase
    continuation: ContinuationV1
    checkpoint_ref: str | None
    side_effect_cursor: int = Field(ge=0)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    _validate_non_blank = field_validator("memorial_id", "edict_id")(_non_blank)
    _normalize_times = field_validator("created_at", "updated_at")(_normalize_utc)

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.side_effect_cursor != self.continuation.side_effect_cursor:
            raise ValueError("side_effect_cursor must match continuation")
        if self.checkpoint_ref != self.continuation.checkpoint_ref:
            raise ValueError("checkpoint_ref must match continuation")
        return self


__all__ = [
    "AgentContinuationV1",
    "ContinuationV1",
    "IterationSummaryV1",
    "OuterLoopContinuationV1",
    "PersistedChatMessageV1",
    "PersistedUsageSummaryV1",
    "RunPhase",
    "RunStateV1",
    "ToolProposalV1",
]
