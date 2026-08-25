"""Immutable content identity for one effective Tianshu system configuration."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import canonical_sha256

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_EXECUTOR_COMPONENT = re.compile(r"^executor:[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_FIXED_COMPONENTS = frozenset(
    {
        "kernel",
        "skills",
        "personas",
        "policy_rules",
        "provider_profiles",
        "evolution_overlay",
        "prompts",
    }
)


class _FrozenComponents(dict[str, str]):
    """A JSON-serializable dict whose contents cannot be mutated after creation."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("SystemSnapshotV1 components are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable  # type: ignore[assignment]
    clear = _immutable
    pop = _immutable  # type: ignore[assignment]
    popitem = _immutable  # type: ignore[assignment]
    setdefault = _immutable  # type: ignore[assignment]
    update = _immutable

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> Self:
        return self


class SystemSnapshotV1(BaseModel):
    """A deeply immutable, content-addressed system component manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    components: dict[str, str] = Field(max_length=64)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("components")
    @classmethod
    def validate_components(cls, components: dict[str, str]) -> dict[str, str]:
        for key, value in components.items():
            if key not in _FIXED_COMPONENTS and _EXECUTOR_COMPONENT.fullmatch(key) is None:
                raise ValueError(f"unsupported system snapshot component: {key!r}")
            if _DIGEST.fullmatch(value) is None:
                raise ValueError(f"component {key!r} must be a lowercase SHA-256 digest")
        return components

    @model_validator(mode="after")
    def validate_digest_and_freeze_components(self) -> Self:
        if self.digest != canonical_sha256(self.components):
            raise ValueError("system snapshot digest does not match components")
        object.__setattr__(self, "components", _FrozenComponents(dict(self.components)))
        return self


__all__ = ["SystemSnapshotV1"]
