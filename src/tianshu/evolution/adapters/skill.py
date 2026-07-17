"""Skill candidate source validation."""

from collections.abc import Mapping

import frontmatter

from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.skills.validator import SkillValidator


class SkillCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.SKILL

    def _validate_domain(self, payload: Mapping[str, object]) -> None:
        name = payload.get("name")
        content = payload.get("content")
        trust_source = payload.get("trust_source", "community")
        if (
            not isinstance(name, str)
            or not isinstance(content, str)
            or not isinstance(trust_source, str)
        ):
            raise AdapterError("skill source requires string name, content, and trust_source")
        result = SkillValidator().validate(name, content, trust_source)
        if not result.valid:
            checks = ", ".join(
                finding.check for finding in result.findings if finding.level == "error"
            )
            raise AdapterError(f"skill source validation failed: {checks}")
        declared_name = (frontmatter.loads(content).metadata or {}).get("name")
        if declared_name != name:
            raise AdapterError("skill source name does not match frontmatter")


__all__ = ["SkillCandidateAdapter"]
