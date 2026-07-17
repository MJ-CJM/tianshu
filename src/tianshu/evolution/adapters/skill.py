"""Skill candidate source validation."""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.models.canonical import JsonValue
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.skills.installer import SkillInstaller, SkillPackageMember


class _SkillMemberV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    kind: Literal["file", "directory", "symlink_file", "symlink_directory"]
    content: str | None = None


class _SkillPackageV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    trust_source: str = "community"
    members: tuple[_SkillMemberV1, ...] = Field(min_length=1)

    @field_validator("members", mode="after")
    @classmethod
    def canonical_members(cls, members: tuple[_SkillMemberV1, ...]) -> tuple[_SkillMemberV1, ...]:
        return tuple(sorted(members, key=lambda member: member.path))


class SkillCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.SKILL

    def _normalize_domain(self, payload: Mapping[str, object]) -> dict[str, JsonValue]:
        try:
            package = _SkillPackageV1.model_validate(payload)
            members = tuple(
                SkillPackageMember(
                    path=member.path,
                    kind=member.kind,
                    content=member.content,
                )
                for member in package.members
            )
            result = SkillInstaller(Path(".")).validate_package(
                members,
                declared_name=package.name,
                source_trust=package.trust_source,
            )
            if not result.valid:
                raise ValueError("package validation failed")
        except (ValidationError, TypeError, ValueError):
            raise AdapterError("skill source validation failed") from None
        return package.model_dump(mode="json")


__all__ = ["SkillCandidateAdapter"]
