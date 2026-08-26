"""Immutable runtime release and generation lifecycle contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import JsonValue, canonical_sha256

_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_GENERATION_ID_PATTERN = r"^rg-[0-9a-f]{32}$"
PROCESS_GENERATION_SCOPE = "process"


class RuntimeGenerationState(StrEnum):
    """Durable states for one materialized runtime generation."""

    STAGED = "staged"
    WARMING = "warming"
    READY = "ready"
    ACTIVE = "active"
    DRAINING = "draining"
    DISPOSED = "disposed"
    FAILED = "failed"


type CliVersionSource = Literal["package_json", "pinned", "unverified"]

_REGULAR_TRANSITIONS: dict[RuntimeGenerationState, frozenset[RuntimeGenerationState]] = {
    RuntimeGenerationState.STAGED: frozenset(
        {RuntimeGenerationState.WARMING, RuntimeGenerationState.FAILED}
    ),
    RuntimeGenerationState.WARMING: frozenset(
        {RuntimeGenerationState.READY, RuntimeGenerationState.FAILED}
    ),
    RuntimeGenerationState.READY: frozenset(
        {RuntimeGenerationState.ACTIVE, RuntimeGenerationState.FAILED}
    ),
    RuntimeGenerationState.ACTIVE: frozenset({RuntimeGenerationState.DRAINING}),
    RuntimeGenerationState.DRAINING: frozenset({RuntimeGenerationState.DISPOSED}),
    RuntimeGenerationState.DISPOSED: frozenset(),
    RuntimeGenerationState.FAILED: frozenset(),
}


class _FrozenJsonDict(dict[str, JsonValue]):
    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("RuntimeReleaseV1 manifest is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable  # type: ignore[assignment]
    clear = _immutable
    pop = _immutable
    popitem = _immutable  # type: ignore[assignment]
    setdefault = _immutable
    update = _immutable

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> Self:
        return self


class _FrozenJsonList(list[JsonValue]):
    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("RuntimeReleaseV1 manifest is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __iadd__ = _immutable  # type: ignore[assignment]
    __imul__ = _immutable  # type: ignore[assignment]
    append = _immutable
    clear = _immutable
    extend = _immutable
    insert = _immutable
    pop = _immutable
    remove = _immutable
    reverse = _immutable
    sort = _immutable

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> Self:
        return self


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return _FrozenJsonDict({key: _freeze_json(nested) for key, nested in value.items()})
    if isinstance(value, list):
        return _FrozenJsonList([_freeze_json(nested) for nested in value])
    return value


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


class RuntimeReleaseV1(_StrictFrozenModel):
    """Content-addressed material sufficient to rebuild one executor release."""

    schema_version: Literal[1] = 1
    scope: str = Field(min_length=1, max_length=256)
    manifest: dict[str, JsonValue] = Field(min_length=1)
    manifest_hash: str = Field(pattern=_DIGEST_PATTERN)
    cli_version: str
    cli_version_source: CliVersionSource
    binary_path: str
    binary_digest: str = Field(pattern=_DIGEST_PATTERN)
    package_name: str
    package_entrypoint: str
    package_digest: str = Field(pattern=_DIGEST_PATTERN)
    single_argv_shape: str
    session_argv_shape: str
    pi_wire_version: int = Field(ge=1)
    materializer_id: str
    materializer_version: str
    release_digest: str = Field(pattern=_DIGEST_PATTERN)

    _validate_scope = field_validator("scope")(_scope)
    _validate_text = field_validator(
        "cli_version",
        "package_name",
        "single_argv_shape",
        "session_argv_shape",
        "materializer_id",
        "materializer_version",
    )(_non_blank)

    @field_validator("binary_path")
    @classmethod
    def validate_binary_path(cls, value: str) -> str:
        _non_blank(value)
        if not Path(value).is_absolute():
            raise ValueError("binary_path must be absolute")
        return value

    @field_validator("package_entrypoint")
    @classmethod
    def validate_package_entrypoint(cls, value: str) -> str:
        _non_blank(value)
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("package_entrypoint must be a normalized relative POSIX path")
        return value

    @model_validator(mode="after")
    def validate_hashes_and_freeze_manifest(self) -> Self:
        if self.manifest_hash != canonical_sha256(self.manifest):
            raise ValueError("manifest_hash does not match manifest")
        material = self.model_dump(mode="json", exclude={"release_digest"})
        if self.release_digest != canonical_sha256(material):
            raise ValueError("release_digest does not match release material")
        object.__setattr__(self, "manifest", _freeze_json(self.manifest))
        return self


class RuntimeGenerationV1(_StrictFrozenModel):
    """One durable runtime instance pinned to an immutable release."""

    schema_version: Literal[1] = 1
    generation_id: str = Field(pattern=_GENERATION_ID_PATTERN)
    scope: str = Field(min_length=1, max_length=256)
    release_digest: str = Field(pattern=_DIGEST_PATTERN)
    state: RuntimeGenerationState
    version: int = Field(ge=1)
    created_at: datetime
    activated_at: datetime | None = None
    updated_at: datetime

    _validate_scope = field_validator("scope")(_scope)
    _normalize_times = field_validator("created_at", "updated_at")(_utc)
    _normalize_activated_at = field_validator("activated_at")(_optional_utc)

    @model_validator(mode="after")
    def validate_lifecycle_times(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        was_activated = self.state in {
            RuntimeGenerationState.ACTIVE,
            RuntimeGenerationState.DRAINING,
            RuntimeGenerationState.DISPOSED,
        }
        if was_activated != (self.activated_at is not None):
            raise ValueError("activated_at must be present exactly for an activated generation")
        if self.activated_at is not None:
            if self.activated_at < self.created_at:
                raise ValueError("activated_at must not precede created_at")
            if self.updated_at < self.activated_at:
                raise ValueError("updated_at must not precede activated_at")
        return self


class GenerationPointerV1(_StrictFrozenModel):
    """CAS-protected active and last-good roots for one executor scope."""

    schema_version: Literal[1] = 1
    scope: str = Field(min_length=1, max_length=256)
    active_generation_id: str = Field(pattern=_GENERATION_ID_PATTERN)
    last_good_generation_id: str = Field(pattern=_GENERATION_ID_PATTERN)
    version: int = Field(ge=1)
    updated_at: datetime

    _validate_scope = field_validator("scope")(_scope)
    _normalize_updated_at = field_validator("updated_at")(_utc)


def validate_regular_generation_transition(
    source: RuntimeGenerationState,
    target: RuntimeGenerationState,
) -> None:
    """Reject every edge outside the ordinary generation state graph."""

    if target not in _REGULAR_TRANSITIONS[source]:
        raise ValueError(f"invalid generation transition: {source.value} -> {target.value}")


def validate_last_good_generation_transition(
    source: RuntimeGenerationState,
    target: RuntimeGenerationState,
) -> None:
    """Accept only the repository-owned last-good rollback edge."""

    if source is not RuntimeGenerationState.DRAINING or target is not RuntimeGenerationState.ACTIVE:
        raise ValueError(f"invalid last-good transition: {source.value} -> {target.value}")


__all__ = [
    "CliVersionSource",
    "GenerationPointerV1",
    "PROCESS_GENERATION_SCOPE",
    "RuntimeGenerationState",
    "RuntimeGenerationV1",
    "RuntimeReleaseV1",
    "validate_last_good_generation_transition",
    "validate_regular_generation_transition",
]
