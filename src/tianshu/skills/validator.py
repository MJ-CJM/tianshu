"""Skill content validation — security scanning before write."""

from __future__ import annotations

import re
from dataclasses import dataclass

import frontmatter as fm

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_MAX_CONTENT_SIZE = 256 * 1024

# Patterns that indicate potential secrets
_SECRET_PATTERNS = [
    re.compile(r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}", re.IGNORECASE),
    re.compile(r"(?:secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style keys
    re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),  # GitHub tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access keys
]

# Patterns that indicate dangerous commands (warn, not block)
_DANGER_PATTERNS = [
    re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
    re.compile(r"sudo\s+", re.IGNORECASE),
    re.compile(r"chmod\s+777", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\|\s*:\s*&\s*\}\s*;", re.IGNORECASE),  # fork bomb
]


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

    def validate(self, name: str, content: str) -> ValidationResult:
        findings: list[ValidationFinding] = []

        findings.extend(self._check_name_format(name))
        findings.extend(self._check_size(content))
        findings.extend(self._check_frontmatter(content))
        findings.extend(self._check_no_secrets(content))
        findings.extend(self._check_no_dangerous_commands(content))

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

    @staticmethod
    def _check_no_secrets(content: str) -> list[ValidationFinding]:
        for pattern in _SECRET_PATTERNS:
            match = pattern.search(content)
            if match:
                start = max(0, match.start() - 10)
                end = min(len(content), match.end() + 10)
                snippet = content[start:end]
                return [ValidationFinding(
                    level="error",
                    check="secrets",
                    message=f"Possible secret detected near: ...{snippet}...",
                )]
        return []

    @staticmethod
    def _check_no_dangerous_commands(content: str) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        for pattern in _DANGER_PATTERNS:
            match = pattern.search(content)
            if match:
                findings.append(ValidationFinding(
                    level="warning",
                    check="dangerous_command",
                    message=f"Dangerous command pattern detected: {match.group()!r}",
                ))
        return findings
