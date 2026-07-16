"""Strict immutable contracts for durable execution attempts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import RedactedError


class AttemptStatus(StrEnum):
    CLAIMABLE = "claimable"
    CLAIMED = "claimed"
    SUSPENDED = "suspended"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class AttemptDisposition(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY = "retry"
    SUSPENDED = "suspended"


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class AttemptLeaseV1(_StrictModel):
    attempt_id: str
    schema_version: Literal[1] = 1
    memorial_id: str
    attempt_no: int = Field(ge=1)
    status: AttemptStatus
    owner_id: str | None
    fencing_token: int = Field(ge=0)
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    available_at: datetime
    max_attempts: int = Field(ge=1)
    failure: RedactedError | None
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime

    _validate_non_blank = field_validator("attempt_id", "memorial_id")(_non_blank)
    _normalize_times = field_validator(
        "lease_expires_at",
        "heartbeat_at",
        "available_at",
        "created_at",
        "updated_at",
    )(_normalize_utc)

    @field_validator("owner_id")
    @classmethod
    def validate_optional_owner(cls, value: str | None) -> str | None:
        return _non_blank(value) if value is not None else None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.attempt_no > self.max_attempts:
            raise ValueError("attempt_no must not exceed max_attempts")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.status is AttemptStatus.CLAIMED:
            if (
                self.owner_id is None
                or self.lease_expires_at is None
                or self.heartbeat_at is None
                or self.fencing_token <= 0
            ):
                raise ValueError("claimed attempt requires owner, lease, heartbeat, and fencing")
            if self.lease_expires_at <= self.heartbeat_at:
                raise ValueError("claimed attempt lease must follow heartbeat")
        elif self.owner_id is not None or self.lease_expires_at is not None:
            raise ValueError("non-claimed attempt must not retain owner or lease")
        failed = self.status in {AttemptStatus.FAILED, AttemptStatus.DEAD_LETTER}
        if failed and self.failure is None:
            raise ValueError("failed attempt requires failure")
        if not failed and self.failure is not None:
            raise ValueError("non-failed attempt must not contain failure")
        return self


class AttemptOutcomeV1(_StrictModel):
    disposition: AttemptDisposition
    completed_at: datetime
    failure: RedactedError | None = None
    retry_at: datetime | None = None

    _normalize_times = field_validator("completed_at", "retry_at")(_normalize_utc)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.disposition is AttemptDisposition.RETRY:
            if self.failure is None or self.retry_at is None:
                raise ValueError("retry outcome requires failure and retry_at")
            if self.retry_at < self.completed_at:
                raise ValueError("retry_at must not precede completed_at")
        elif self.disposition is AttemptDisposition.FAILED:
            if self.failure is None:
                raise ValueError("failed outcome requires failure")
            if self.retry_at is not None:
                raise ValueError("failed outcome must not contain retry_at")
        elif self.failure is not None or self.retry_at is not None:
            raise ValueError("successful or suspended outcome must not contain failure or retry_at")
        return self


__all__ = [
    "AttemptDisposition",
    "AttemptLeaseV1",
    "AttemptOutcomeV1",
    "AttemptStatus",
]
