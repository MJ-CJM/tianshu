"""Skill candidate source validation."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from tianshu.evidence.service import ArtifactStore
from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.models.canonical import JsonValue
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.skills.installer import (
    SkillInstaller,
    SkillPackageMember,
    canonical_skill_package_member_path,
)


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

    @model_validator(mode="after")
    def canonicalize_members(self) -> _SkillPackageV1:
        seen: set[str] = set()
        members: list[_SkillMemberV1] = []
        for member in self.members:
            canonical_path = canonical_skill_package_member_path(member.path)
            if canonical_path in seen:
                raise ValueError("skill package contains duplicate targets")
            seen.add(canonical_path)
            if canonical_path != member.path:
                raise ValueError("skill package member path is not canonical")
            members.append(member.model_copy(update={"path": canonical_path}))
        canonical = tuple(sorted(members, key=lambda member: member.path))
        return self.model_copy(update={"members": canonical})


class SkillCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.SKILL

    def __init__(self, artifacts: ArtifactStore, *, live_root: Path | None = None) -> None:
        if live_root is None:
            raise ValueError("skill adapter requires an injected installer target")
        super().__init__(artifacts, live_root=live_root)
        self._installer = SkillInstaller(live_root)

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
            result = self._installer.validate_package(
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
