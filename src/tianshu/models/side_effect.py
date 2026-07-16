"""Strict immutable contracts for managed side-effect journal evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.security.sensitive_payload import contains_raw_sensitive_payload


class SideEffectSemantics(StrEnum):
    PROVIDER_IDEMPOTENT = "provider_idempotent"
    RECEIPT_LOOKUP = "receipt_lookup"
    WORKSPACE_ONLY = "workspace_only"
    UNTRACKED_EXTERNAL = "untracked_external"
    OPAQUE_CLI = "opaque_cli"


class SideEffectStatus(StrEnum):
    INTENDED = "intended"
    RECEIPTED = "receipted"
    UNCERTAIN = "uncertain"


_SUPPORTED_PROVIDER_SEMANTICS = frozenset(
    {
        SideEffectSemantics.PROVIDER_IDEMPOTENT,
        SideEffectSemantics.RECEIPT_LOOKUP,
    }
)
_MAX_METADATA_DEPTH = 4
_MAX_METADATA_BYTES = 16384
_MAX_METADATA_KEYS = 32
_MAX_METADATA_ITEMS = 64
_MAX_METADATA_TEXT_LENGTH = 4096


def _normalize_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("value must not be blank")
    return value


def _optional_non_blank(value: str | None) -> str | None:
    return _non_blank(value) if value is not None else None


def _require_safe_metadata(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if contains_raw_sensitive_payload(value):
        raise ValueError("raw secret is not allowed in side-effect metadata")
    if len(canonical_json_bytes(value)) > _MAX_METADATA_BYTES:
        raise ValueError("side-effect metadata is too large")
    _validate_metadata_shape(value, depth=0)
    return value


def _validate_metadata_shape(value: JsonValue, *, depth: int) -> None:
    if depth > _MAX_METADATA_DEPTH:
        raise ValueError("side-effect metadata exceeds maximum depth")
    if isinstance(value, str):
        if len(value) > _MAX_METADATA_TEXT_LENGTH:
            raise ValueError("side-effect metadata text is too large")
        return
    if isinstance(value, list):
        if len(value) > _MAX_METADATA_ITEMS:
            raise ValueError("side-effect metadata list is too large")
        for item in value:
            _validate_metadata_shape(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > _MAX_METADATA_KEYS:
            raise ValueError("side-effect metadata has too many keys")
        for key, item in value.items():
            if len(key) > _MAX_METADATA_TEXT_LENGTH:
                raise ValueError("side-effect metadata key is too large")
            _validate_metadata_shape(item, depth=depth + 1)


def _identity_payload(
    *,
    effect_id: str,
    edict_id: str,
    memorial_id: str,
    sequence_no: int,
    boundary: str,
    operation: str,
    semantics: SideEffectSemantics,
    request_hash: str,
) -> dict[str, JsonValue]:
    return {
        "effect_id": effect_id,
        "edict_id": edict_id,
        "memorial_id": memorial_id,
        "sequence_no": sequence_no,
        "boundary": boundary,
        "operation": operation,
        "semantics": semantics.value,
        "request_hash": request_hash,
    }


def _intent_payload(
    *,
    intent_id: str,
    effect_id: str,
    edict_id: str,
    memorial_id: str,
    attempt_id: str,
    owner_id: str,
    fencing_token: int,
    sequence_no: int,
    boundary: str,
    operation: str,
    semantics: SideEffectSemantics,
    request_hash: str,
    provider_idempotency_key: str | None,
) -> dict[str, JsonValue]:
    return {
        "intent_id": intent_id,
        "effect_id": effect_id,
        "edict_id": edict_id,
        "memorial_id": memorial_id,
        "attempt_id": attempt_id,
        "owner_id": owner_id,
        "fencing_token": fencing_token,
        "sequence_no": sequence_no,
        "boundary": boundary,
        "operation": operation,
        "semantics": semantics.value,
        "request_hash": request_hash,
        "provider_idempotency_key": provider_idempotency_key,
    }


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SideEffectIntentV1(_StrictModel):
    intent_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_id: str
    schema_version: Literal[1] = 1
    edict_id: str
    memorial_id: str
    attempt_id: str
    owner_id: str
    fencing_token: int = Field(ge=1)
    sequence_no: int = Field(ge=0)
    boundary: str
    operation: str
    semantics: SideEffectSemantics
    provider_idempotency_key: str | None
    request_metadata: dict[str, JsonValue]
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    intent_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: SideEffectStatus = SideEffectStatus.INTENDED
    reason_code: str | None = None
    version: int = Field(default=1, ge=1)
    created_at: datetime
    updated_at: datetime

    _validate_non_blank = field_validator(
        "effect_id",
        "edict_id",
        "memorial_id",
        "attempt_id",
        "owner_id",
        "boundary",
        "operation",
    )(_non_blank)
    _validate_optional_text = field_validator("provider_idempotency_key", "reason_code")(
        _optional_non_blank
    )
    _validate_metadata = field_validator("request_metadata")(_require_safe_metadata)
    _normalize_times = field_validator("created_at", "updated_at")(_normalize_utc)

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        expected_request_hash = canonical_sha256(self.request_metadata)
        if self.request_hash != expected_request_hash:
            raise ValueError("request_hash does not match request_metadata")
        expected_intent_id = canonical_sha256(
            _identity_payload(
                effect_id=self.effect_id,
                edict_id=self.edict_id,
                memorial_id=self.memorial_id,
                sequence_no=self.sequence_no,
                boundary=self.boundary,
                operation=self.operation,
                semantics=self.semantics,
                request_hash=self.request_hash,
            )
        )
        if self.intent_id != expected_intent_id:
            raise ValueError("intent_id does not match the stable effect identity")
        if self.semantics in _SUPPORTED_PROVIDER_SEMANTICS:
            if self.provider_idempotency_key != self.intent_id:
                raise ValueError("supported provider intent requires its stable idempotency key")
        elif self.provider_idempotency_key is not None:
            raise ValueError("unsupported effect must not claim provider idempotency")
        expected_intent_hash = canonical_sha256(
            _intent_payload(
                intent_id=self.intent_id,
                effect_id=self.effect_id,
                edict_id=self.edict_id,
                memorial_id=self.memorial_id,
                attempt_id=self.attempt_id,
                owner_id=self.owner_id,
                fencing_token=self.fencing_token,
                sequence_no=self.sequence_no,
                boundary=self.boundary,
                operation=self.operation,
                semantics=self.semantics,
                request_hash=self.request_hash,
                provider_idempotency_key=self.provider_idempotency_key,
            )
        )
        if self.intent_hash != expected_intent_hash:
            raise ValueError("intent_hash does not match the canonical intent")
        if self.status is SideEffectStatus.UNCERTAIN:
            if self.reason_code is None:
                raise ValueError("uncertain intent requires a reason_code")
        elif self.reason_code is not None:
            raise ValueError("non-uncertain intent must not contain a reason_code")
        return self


class SideEffectReceiptV1(_StrictModel):
    intent_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_id: str
    schema_version: Literal[1] = 1
    edict_id: str
    memorial_id: str
    attempt_id: str
    owner_id: str
    fencing_token: int = Field(ge=1)
    provider_receipt_id: str
    result_metadata: dict[str, JsonValue]
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal[SideEffectStatus.RECEIPTED] = SideEffectStatus.RECEIPTED
    reason_code: None = None
    version: int = Field(ge=2)
    effective_at: datetime
    recorded_at: datetime

    _validate_non_blank = field_validator(
        "effect_id",
        "edict_id",
        "memorial_id",
        "attempt_id",
        "owner_id",
        "provider_receipt_id",
    )(_non_blank)
    _validate_metadata = field_validator("result_metadata")(_require_safe_metadata)
    _normalize_times = field_validator("effective_at", "recorded_at")(_normalize_utc)

    @model_validator(mode="after")
    def validate_integrity(self) -> Self:
        if self.result_hash != canonical_sha256(self.result_metadata):
            raise ValueError("result_hash does not match result_metadata")
        if self.recorded_at < self.effective_at:
            raise ValueError("recorded_at must not precede effective_at")
        return self


def build_side_effect_intent(
    *,
    effect_id: str,
    edict_id: str,
    memorial_id: str,
    attempt_id: str,
    owner_id: str,
    fencing_token: int,
    sequence_no: int,
    boundary: str,
    operation: str,
    semantics: SideEffectSemantics,
    request_metadata: dict[str, JsonValue],
    created_at: datetime,
    provider_idempotency_key: str | None = None,
) -> SideEffectIntentV1:
    """Build the stable v1 identity from redacted canonical request metadata."""

    request_hash = canonical_sha256(request_metadata)
    intent_id = canonical_sha256(
        _identity_payload(
            effect_id=effect_id,
            edict_id=edict_id,
            memorial_id=memorial_id,
            sequence_no=sequence_no,
            boundary=boundary,
            operation=operation,
            semantics=semantics,
            request_hash=request_hash,
        )
    )
    if semantics in _SUPPORTED_PROVIDER_SEMANTICS:
        if provider_idempotency_key is not None and provider_idempotency_key != intent_id:
            raise ValueError("provider idempotency key must equal the stable intent identity")
        provider_idempotency_key = intent_id
    intent_hash = canonical_sha256(
        _intent_payload(
            intent_id=intent_id,
            effect_id=effect_id,
            edict_id=edict_id,
            memorial_id=memorial_id,
            attempt_id=attempt_id,
            owner_id=owner_id,
            fencing_token=fencing_token,
            sequence_no=sequence_no,
            boundary=boundary,
            operation=operation,
            semantics=semantics,
            request_hash=request_hash,
            provider_idempotency_key=provider_idempotency_key,
        )
    )
    now = _normalize_utc(created_at)
    return SideEffectIntentV1(
        intent_id=intent_id,
        effect_id=effect_id,
        edict_id=edict_id,
        memorial_id=memorial_id,
        attempt_id=attempt_id,
        owner_id=owner_id,
        fencing_token=fencing_token,
        sequence_no=sequence_no,
        boundary=boundary,
        operation=operation,
        semantics=semantics,
        provider_idempotency_key=provider_idempotency_key,
        request_metadata=request_metadata,
        request_hash=request_hash,
        intent_hash=intent_hash,
        status=SideEffectStatus.INTENDED,
        reason_code=None,
        version=1,
        created_at=now,
        updated_at=now,
    )


__all__ = [
    "SideEffectIntentV1",
    "SideEffectReceiptV1",
    "SideEffectSemantics",
    "SideEffectStatus",
    "build_side_effect_intent",
]
