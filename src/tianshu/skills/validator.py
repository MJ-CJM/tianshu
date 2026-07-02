"""Skill content validation — security scanning before write.

Delegates content security scanning to SkillsGuard (guard.py) which provides
50+ patterns across 13 threat categories. This module retains structural
validation (name, size, frontmatter).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import frontmatter as fm

from tianshu.skills.guard import SkillsGuard

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_CONTENT_SIZE = 256 * 1024

_guard = SkillsGuard()


@dataclass(frozen=True)
class ValidationFinding:
    level: str  # "error" | "warning"
    check: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    findings: tuple[ValidationFinding, ...] = ()


class SkillValidator:
    """Validate skill content before writing."""

    def validate(
        self,
        name: str,
        content: str,
        source: str = "community",
    ) -> ValidationResult:
        findings: list[ValidationFinding] = []

        # Structural checks (name, size, frontmatter)
        findings.extend(self._check_name_format(name))
        findings.extend(self._check_size(content))
        findings.extend(self._check_frontmatter(content))

        # Security scan via SkillsGuard
        trust_level = SkillsGuard.resolve_trust_level(source)
        guard_result = _guard.scan_content(content, trust_level)
        for gf in guard_result.findings:
            level = "error" if gf.severity.value in ("critical", "high") else "warning"
            findings.append(ValidationFinding(
                level=level,
                check=f"guard:{gf.category.value}",
                message=gf.message,
            ))

        has_errors = any(f.level == "error" for f in findings)
        return ValidationResult(valid=not has_errors, findings=tuple(findings))

    @staticmethod
    def _check_name_format(name: str) -> list[ValidationFinding]:
        if not _NAME_RE.match(name):
            return [ValidationFinding(
                level="error",
                check="name_format",
                message=f"Name '{name}' must match: ^[a-z0-9][a-z0-9._-]{{0,63}}$",
            )]
        return []

    @staticmethod
    def _check_size(content: str) -> list[ValidationFinding]:
        if len(content.encode("utf-8")) > _MAX_CONTENT_SIZE:
            return [ValidationFinding(
                level="error",
                check="size",
                message=f"Content exceeds {_MAX_CONTENT_SIZE} bytes limit",
            )]
        return []

    @staticmethod
    def _check_frontmatter(content: str) -> list[ValidationFinding]:
        try:
            post = fm.loads(content)
        except Exception as e:
            return [ValidationFinding(
                level="error",
                check="frontmatter",
                message=f"Invalid frontmatter: {e}",
            )]

        meta = post.metadata or {}
        findings: list[ValidationFinding] = []
        if not meta.get("name"):
            findings.append(ValidationFinding(
                level="error", check="frontmatter", message="Frontmatter missing required field 'name'",
            ))
        if not meta.get("description"):
            findings.append(ValidationFinding(
                level="error", check="frontmatter", message="Frontmatter missing required field 'description'",
            ))
        return findings

