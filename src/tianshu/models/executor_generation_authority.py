"""Immutable authority contracts binding executor candidates to generations."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import canonical_sha256

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_GENERATION_ID_PATTERN = r"^rg-[0-9a-f]{32}$"


class ExecutorGenerationAuthorityStatus(StrEnum):
    """Lifecycle of one candidate's authority over an exact executor generation."""

    PENDING = "pending"
    AUTHORIZED = "authorized"
    REVOKING = "revoking"
    REVOKED = "revoked"


_SAME_EPOCH_TRANSITIONS: dict[
    ExecutorGenerationAuthorityStatus,
    frozenset[ExecutorGenerationAuthorityStatus],
] = {
    ExecutorGenerationAuthorityStatus.PENDING: frozenset(
        {
            ExecutorGenerationAuthorityStatus.AUTHORIZED,
            ExecutorGenerationAuthorityStatus.REVOKED,
        }
    ),
    ExecutorGenerationAuthorityStatus.AUTHORIZED: frozenset(
        {
            ExecutorGenerationAuthorityStatus.REVOKING,
            ExecutorGenerationAuthorityStatus.REVOKED,
        }
    ),
    ExecutorGenerationAuthorityStatus.REVOKING: frozenset(
        {ExecutorGenerationAuthorityStatus.REVOKED}
    ),
    ExecutorGenerationAuthorityStatus.REVOKED: frozenset(),
}


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _scope(value: str) -> str:
    if not value.strip() or len(value.strip()) > 256:
        raise ValueError("scope must contain between 1 and 256 non-whitespace characters")
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _optional_utc(value: datetime | None) -> datetime | None:
    return _utc(value) if value is not None else None


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def executor_generation_authority_id(
    *,
    candidate_id: str,
    epoch: int,
    candidate_version: int,
    candidate_artifact_digest: str,
    candidate_canonical_digest: str,
    release_digest: str,
    scope: str,
    generation_id: str,
    base_generation_id: str,
    base_release_digest: str,
    promotion_journal_id: str,
    start_command_key: str,
) -> str:
    """Return the content-addressed identity for one authority epoch."""

    return canonical_sha256(
        {
            "base_generation_id": base_generation_id,
            "base_release_digest": base_release_digest,
            "candidate_artifact_digest": candidate_artifact_digest,
            "candidate_canonical_digest": candidate_canonical_digest,
            "candidate_id": candidate_id,
            "candidate_version": candidate_version,
            "epoch": epoch,
            "generation_id": generation_id,
            "promotion_journal_id": promotion_journal_id,
            "release_digest": release_digest,
            "schema_version": 1,
            "scope": scope,
            "start_command_key": start_command_key,
        }
    )


class ExecutorGenerationAuthorityV1(_StrictFrozenModel):
    """Current CAS-protected authority for one executor evolution candidate."""

    schema_version: Literal[1] = 1
    authority_id: str = Field(pattern=_DIGEST_PATTERN)
    candidate_id: str
    epoch: int = Field(ge=1)
    candidate_version: int = Field(ge=1)
    candidate_artifact_digest: str = Field(pattern=_DIGEST_PATTERN)
    candidate_canonical_digest: str = Field(pattern=_DIGEST_PATTERN)
    release_digest: str = Field(pattern=_DIGEST_PATTERN)
    scope: str = Field(min_length=1, max_length=256)
    generation_id: str = Field(pattern=_GENERATION_ID_PATTERN)
    base_generation_id: str = Field(pattern=_GENERATION_ID_PATTERN)
    base_release_digest: str = Field(pattern=_DIGEST_PATTERN)
    promotion_journal_id: str
    start_command_key: str
    status: ExecutorGenerationAuthorityStatus
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    _validate_text = field_validator("candidate_id", "promotion_journal_id", "start_command_key")(
        _non_blank
    )
    _validate_scope = field_validator("scope")(_scope)
    _normalize_times = field_validator("created_at", "updated_at")(_utc)
    _normalize_revoked_at = field_validator("revoked_at")(_optional_utc)

    @field_validator("revocation_reason")
    @classmethod
    def validate_optional_reason(cls, value: str | None) -> str | None:
        return _non_blank(value) if value is not None else None

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        expected_id = executor_generation_authority_id(
            candidate_id=self.candidate_id,
            epoch=self.epoch,
            candidate_version=self.candidate_version,
            candidate_artifact_digest=self.candidate_artifact_digest,
            candidate_canonical_digest=self.candidate_canonical_digest,
            release_digest=self.release_digest,
            scope=self.scope,
            generation_id=self.generation_id,
            base_generation_id=self.base_generation_id,
            base_release_digest=self.base_release_digest,
            promotion_journal_id=self.promotion_journal_id,
            start_command_key=self.start_command_key,
        )
        if self.authority_id != expected_id:
            raise ValueError("authority_id does not match the authority identity")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        revoked = self.status in {
            ExecutorGenerationAuthorityStatus.REVOKING,
            ExecutorGenerationAuthorityStatus.REVOKED,
        }
        if revoked != (self.revoked_at is not None and self.revocation_reason is not None):
            raise ValueError(
                "revocation metadata must be present exactly while revoking or revoked"
            )
        if self.revoked_at is not None and not (
            self.created_at <= self.revoked_at <= self.updated_at
        ):
            raise ValueError("revoked_at must fall within the authority lifetime")
        return self


class ExecutorGenerationAuthorityJournalEntryV1(_StrictFrozenModel):
    """Canonical append-only record for one authority version."""

    schema_version: Literal[1] = 1
    authority_id: str = Field(pattern=_DIGEST_PATTERN)
    candidate_id: str
    authority_version: int = Field(ge=1)
    epoch: int = Field(ge=1)
    transition: ExecutorGenerationAuthorityStatus
    candidate_version: int = Field(ge=1)
    candidate_artifact_digest: str = Field(pattern=_DIGEST_PATTERN)
    candidate_canonical_digest: str = Field(pattern=_DIGEST_PATTERN)
    release_digest: str = Field(pattern=_DIGEST_PATTERN)
    scope: str = Field(min_length=1, max_length=256)
    generation_id: str = Field(pattern=_GENERATION_ID_PATTERN)
    base_generation_id: str = Field(pattern=_GENERATION_ID_PATTERN)
    base_release_digest: str = Field(pattern=_DIGEST_PATTERN)
    promotion_journal_id: str
    start_command_key: str
    reason_code: str
    authority_created_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None
    revocation_reason: str | None = None

    _validate_text = field_validator(
        "candidate_id", "promotion_journal_id", "start_command_key", "reason_code"
    )(_non_blank)
    _validate_scope = field_validator("scope")(_scope)
    _normalize_times = field_validator("authority_created_at", "created_at")(_utc)
    _normalize_revoked_at = field_validator("revoked_at")(_optional_utc)

    @field_validator("revocation_reason")
    @classmethod
    def validate_optional_reason(cls, value: str | None) -> str | None:
        return _non_blank(value) if value is not None else None

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected_id = executor_generation_authority_id(
            candidate_id=self.candidate_id,
            epoch=self.epoch,
            candidate_version=self.candidate_version,
            candidate_artifact_digest=self.candidate_artifact_digest,
            candidate_canonical_digest=self.candidate_canonical_digest,
            release_digest=self.release_digest,
            scope=self.scope,
            generation_id=self.generation_id,
            base_generation_id=self.base_generation_id,
            base_release_digest=self.base_release_digest,
            promotion_journal_id=self.promotion_journal_id,
            start_command_key=self.start_command_key,
        )
        if self.authority_id != expected_id:
            raise ValueError("authority_id does not match the journal identity")
        executor_generation_authority_from_journal(self)
        return self


def executor_generation_authority_from_journal(
    entry: ExecutorGenerationAuthorityJournalEntryV1,
) -> ExecutorGenerationAuthorityV1:
    """Reconstruct the complete authority row anchored by one journal entry."""

    return ExecutorGenerationAuthorityV1(
        authority_id=entry.authority_id,
        candidate_id=entry.candidate_id,
        epoch=entry.epoch,
        candidate_version=entry.candidate_version,
        candidate_artifact_digest=entry.candidate_artifact_digest,
        candidate_canonical_digest=entry.candidate_canonical_digest,
        release_digest=entry.release_digest,
        scope=entry.scope,
        generation_id=entry.generation_id,
        base_generation_id=entry.base_generation_id,
        base_release_digest=entry.base_release_digest,
        promotion_journal_id=entry.promotion_journal_id,
        start_command_key=entry.start_command_key,
        status=entry.transition,
        version=entry.authority_version,
        created_at=entry.authority_created_at,
        updated_at=entry.created_at,
        revoked_at=entry.revoked_at,
        revocation_reason=entry.revocation_reason,
    )


def new_pending_executor_generation_authority(
    *,
    candidate_id: str,
    candidate_version: int,
    candidate_artifact_digest: str,
    candidate_canonical_digest: str,
    release_digest: str,
    scope: str,
    generation_id: str,
    base_generation_id: str,
    base_release_digest: str,
    promotion_journal_id: str,
    start_command_key: str,
    now: datetime,
    previous: ExecutorGenerationAuthorityV1 | None = None,
) -> ExecutorGenerationAuthorityV1:
    """Build the first pending authority or the next pending epoch after revocation."""

    normalized_now = _utc(now)
    if previous is None:
        epoch = 1
        version = 1
    else:
        if previous.candidate_id != candidate_id:
            raise ValueError("a new authority epoch must keep the candidate identity")
        if previous.status is not ExecutorGenerationAuthorityStatus.REVOKED:
            raise ValueError("a new authority epoch requires a revoked predecessor")
        if normalized_now < previous.updated_at:
            raise ValueError("a new authority epoch cannot precede its predecessor")
        epoch = previous.epoch + 1
        version = previous.version + 1
    authority_id = executor_generation_authority_id(
        candidate_id=candidate_id,
        epoch=epoch,
        candidate_version=candidate_version,
        candidate_artifact_digest=candidate_artifact_digest,
        candidate_canonical_digest=candidate_canonical_digest,
        release_digest=release_digest,
        scope=scope,
        generation_id=generation_id,
        base_generation_id=base_generation_id,
        base_release_digest=base_release_digest,
        promotion_journal_id=promotion_journal_id,
        start_command_key=start_command_key,
    )
    return ExecutorGenerationAuthorityV1(
        authority_id=authority_id,
        candidate_id=candidate_id,
        epoch=epoch,
        candidate_version=candidate_version,
        candidate_artifact_digest=candidate_artifact_digest,
        candidate_canonical_digest=candidate_canonical_digest,
        release_digest=release_digest,
        scope=scope,
        generation_id=generation_id,
        base_generation_id=base_generation_id,
        base_release_digest=base_release_digest,
        promotion_journal_id=promotion_journal_id,
        start_command_key=start_command_key,
        status=ExecutorGenerationAuthorityStatus.PENDING,
        version=version,
        created_at=normalized_now,
        updated_at=normalized_now,
    )


def transition_executor_generation_authority(
    current: ExecutorGenerationAuthorityV1,
    target: ExecutorGenerationAuthorityStatus,
    *,
    now: datetime,
    revocation_reason: str | None = None,
) -> ExecutorGenerationAuthorityV1:
    """Build one legal same-epoch authority transition."""

    if target not in _SAME_EPOCH_TRANSITIONS[current.status]:
        raise ValueError(
            f"illegal executor authority transition: {current.status.value} -> {target.value}"
        )
    normalized_now = _utc(now)
    if normalized_now < current.updated_at:
        raise ValueError("authority transition cannot move time backwards")
    if target in {
        ExecutorGenerationAuthorityStatus.REVOKING,
        ExecutorGenerationAuthorityStatus.REVOKED,
    }:
        if current.status is ExecutorGenerationAuthorityStatus.REVOKING:
            if revocation_reason is not None and revocation_reason != current.revocation_reason:
                raise ValueError("revocation reason is immutable once revocation begins")
            revoked_at = current.revoked_at
            reason = current.revocation_reason
        else:
            revoked_at = normalized_now
            reason = _non_blank(revocation_reason or "")
    else:
        if revocation_reason is not None:
            raise ValueError("non-revocation transition cannot carry a revocation reason")
        revoked_at = None
        reason = None
    payload = current.model_dump(mode="python")
    payload.update(
        status=target,
        version=current.version + 1,
        updated_at=normalized_now,
        revoked_at=revoked_at,
        revocation_reason=reason,
    )
    transitioned = ExecutorGenerationAuthorityV1.model_validate(payload)
    validate_executor_generation_authority_transition(current, transitioned)
    return transitioned


def validate_executor_generation_authority_transition(
    current: ExecutorGenerationAuthorityV1 | None,
    target: ExecutorGenerationAuthorityV1,
) -> None:
    """Validate initial creation, same-epoch edges, and revoked-to-pending rebinding."""

    if current is None:
        if (
            target.status is not ExecutorGenerationAuthorityStatus.PENDING
            or target.epoch != 1
            or target.version != 1
            or target.created_at != target.updated_at
        ):
            raise ValueError("initial executor authority must be pending at epoch/version one")
        return
    if (
        target.candidate_id != current.candidate_id
        or target.schema_version != current.schema_version
    ):
        raise ValueError("candidate identity and authority schema are immutable")
    if target.version != current.version + 1:
        raise ValueError("authority version must advance by exactly one")
    if target.updated_at < current.updated_at:
        raise ValueError("authority transition cannot move time backwards")
    if current.status is ExecutorGenerationAuthorityStatus.REVOKED:
        if (
            target.status is not ExecutorGenerationAuthorityStatus.PENDING
            or target.epoch != current.epoch + 1
            or target.created_at != target.updated_at
            or target.created_at < current.updated_at
        ):
            raise ValueError("revoked authority may only start the next pending epoch")
        return
    if target.status not in _SAME_EPOCH_TRANSITIONS[current.status]:
        raise ValueError(
            f"illegal executor authority transition: {current.status.value} -> "
            f"{target.status.value}"
        )
    immutable = (
        "authority_id",
        "epoch",
        "candidate_version",
        "candidate_artifact_digest",
        "candidate_canonical_digest",
        "release_digest",
        "scope",
        "generation_id",
        "base_generation_id",
        "base_release_digest",
        "promotion_journal_id",
        "start_command_key",
        "created_at",
    )
    if any(getattr(target, field) != getattr(current, field) for field in immutable):
        raise ValueError("same-epoch authority identity is immutable")
    if current.status is ExecutorGenerationAuthorityStatus.REVOKING and (
        target.revoked_at != current.revoked_at
        or target.revocation_reason != current.revocation_reason
    ):
        raise ValueError("revocation metadata is immutable once revocation begins")


__all__ = [
    "ExecutorGenerationAuthorityJournalEntryV1",
    "ExecutorGenerationAuthorityStatus",
    "ExecutorGenerationAuthorityV1",
    "executor_generation_authority_from_journal",
    "executor_generation_authority_id",
    "new_pending_executor_generation_authority",
    "transition_executor_generation_authority",
    "validate_executor_generation_authority_transition",
]
