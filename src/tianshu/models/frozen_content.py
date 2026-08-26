"""Immutable declarative content views bound to one managed run."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.canonical import JsonValue, canonical_sha256

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.ASCII)


class _FrozenDict[K, V](dict[K, V]):
    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("frozen content views are immutable")

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


class _FrozenList(list[JsonValue]):
    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("frozen content views are immutable")

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
        return _FrozenDict({key: _freeze_json(nested) for key, nested in value.items()})
    if isinstance(value, list):
        return _FrozenList([_freeze_json(nested) for nested in value])
    return value


def frozen_skill_digest(
    *,
    content: str,
    metadata: Mapping[str, JsonValue],
) -> str:
    """Return the canonical identity of one effective skill entry."""

    return canonical_sha256(
        {
            "content": content,
            "metadata": dict(metadata),
        }
    )


def frozen_skills_view_digest(
    *,
    skills: Mapping[str, FrozenSkillV1],
    load_all_entries: tuple[tuple[str, str], ...],
    staging_blocked_names: tuple[str, ...] = (),
) -> str:
    """Return the ordered identity of every frozen Skills read projection."""

    return canonical_sha256(
        {
            "skills": {name: skill.digest for name, skill in skills.items()},
            "load_all_entries": [
                {"name": name, "content": content} for name, content in load_all_entries
            ],
            "staging_blocked_names": list(staging_blocked_names),
        }
    )


class FrozenSkillV1(BaseModel):
    """One immutable effective skill as seen by a bound run."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    digest: str
    content: str
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("frozen skill digest must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _validate_and_freeze(self) -> FrozenSkillV1:
        metadata = _FrozenDict(
            {
                key: _freeze_json(cast(JsonValue, value))
                for key, value in sorted(self.metadata.items())
            }
        )
        expected = frozen_skill_digest(
            content=self.content,
            metadata=metadata,
        )
        if self.digest != expected:
            raise ValueError("frozen skill digest does not match its content")
        object.__setattr__(self, "metadata", metadata)
        return self


class FrozenSkillsViewV1(BaseModel):
    """Immutable effective Skills view plus its underlying source identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_digest: str
    effective_digest: str
    skills: Mapping[str, FrozenSkillV1] = Field(default_factory=dict)
    load_all_entries: tuple[tuple[str, str], ...] = ()
    staging_blocked_names: tuple[str, ...] = ()

    @field_validator("source_digest", "effective_digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("frozen view digest must be lowercase SHA-256")
        return value

    @model_validator(mode="after")
    def _validate_and_freeze(self) -> FrozenSkillsViewV1:
        skills = _FrozenDict(dict(self.skills))
        for name, skill in skills.items():
            if _SKILL_NAME_RE.fullmatch(name) is None:
                raise ValueError("frozen skill name is invalid")
            if skill.metadata.get("name") != name:
                raise ValueError("frozen skill key must match metadata name")
        seen: set[str] = set()
        for entry in self.load_all_entries:
            if len(entry) != 2 or not all(isinstance(value, str) for value in entry):
                raise ValueError("frozen load-all entry must be a name/content pair")
            name, _content = entry
            if _SKILL_NAME_RE.fullmatch(name) is None:
                raise ValueError("frozen load-all skill name is invalid")
            if name in seen:
                raise ValueError("frozen load-all skill names must be unique")
            seen.add(name)
        if self.staging_blocked_names != tuple(sorted(set(self.staging_blocked_names))):
            raise ValueError("frozen staging-blocked skill names must be sorted and unique")
        for name in self.staging_blocked_names:
            if _SKILL_NAME_RE.fullmatch(name) is None:
                raise ValueError("frozen staging-blocked skill name is invalid")
        expected = frozen_skills_view_digest(
            skills=skills,
            load_all_entries=self.load_all_entries,
            staging_blocked_names=self.staging_blocked_names,
        )
        if self.effective_digest != expected:
            raise ValueError("frozen skills effective digest does not match its entries")
        object.__setattr__(self, "skills", skills)
        return self


class FrozenContentViewsV1(BaseModel):
    """Extensible per-run container for declarative content views."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    skills: FrozenSkillsViewV1


__all__ = [
    "FrozenContentViewsV1",
    "FrozenSkillV1",
    "FrozenSkillsViewV1",
    "frozen_skill_digest",
    "frozen_skills_view_digest",
]
