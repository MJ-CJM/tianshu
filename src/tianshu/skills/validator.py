"""Skill content validation — security scanning before write.

Delegates content security scanning to SkillsGuard (guard.py) which provides
50+ patterns across 13 threat categories. This module retains structural
validation (name, size, frontmatter).
"""

from __future__ import annotations

from dataclasses import dataclass

import frontmatter as fm

from tianshu.skills.guard import SkillsGuard
from tianshu.skills.loader import validate_skill_name

_MAX_CONTENT_SIZE = 256 * 1024

# agentskills.io 开放标准(2025-12-18)认可的顶层键。平台特有字段应收敛进
# ``metadata`` 命名空间,顶层出现其它键仅告警(warning),不阻断安装以保持兼容。
_STANDARD_KEYS = frozenset(
    {"name", "description", "license", "allowed-tools", "metadata", "version"}
)

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
            findings.append(
                ValidationFinding(
                    level=level,
                    check=f"guard:{gf.category.value}",
                    message=gf.message,
                )
            )

        has_errors = any(f.level == "error" for f in findings)
        return ValidationResult(valid=not has_errors, findings=tuple(findings))

    @staticmethod
    def _check_name_format(name: str) -> list[ValidationFinding]:
        try:
            validate_skill_name(name)
        except ValueError as exc:
            return [
                ValidationFinding(
                    level="error",
                    check="name_format",
                    message=str(exc),
                )
            ]
        return []

    @staticmethod
    def _check_size(content: str) -> list[ValidationFinding]:
        if len(content.encode("utf-8")) > _MAX_CONTENT_SIZE:
            return [
                ValidationFinding(
                    level="error",
                    check="size",
                    message=f"Content exceeds {_MAX_CONTENT_SIZE} bytes limit",
                )
            ]
        return []

    @staticmethod
    def _check_frontmatter(content: str) -> list[ValidationFinding]:
        try:
            post = fm.loads(content)
        except Exception as e:
            return [
                ValidationFinding(
                    level="error",
                    check="frontmatter",
                    message=f"Invalid frontmatter: {e}",
                )
            ]

        meta = post.metadata or {}
        findings: list[ValidationFinding] = []
        if not meta.get("name"):
            findings.append(
                ValidationFinding(
                    level="error",
                    check="frontmatter",
                    message="Frontmatter missing required field 'name'",
                )
            )
        if not meta.get("description"):
            findings.append(
                ValidationFinding(
                    level="error",
                    check="frontmatter",
                    message="Frontmatter missing required field 'description'",
                )
            )

        # agentskills.io 开放标准对齐:非标准顶层键建议收敛进 metadata 命名空间。
        for key in meta:
            if key not in _STANDARD_KEYS:
                findings.append(
                    ValidationFinding(
                        level="warning",
                        check="open_standard",
                        message=f"非标准字段 '{key}' 建议移入 metadata 命名空间",
                    )
                )

        # allowed-tools 按实验性字段对待:若存在须为 list[str],否则告警。
        tools = meta.get("allowed-tools")
        if tools is not None and not (
            isinstance(tools, list) and all(isinstance(t, str) for t in tools)
        ):
            findings.append(
                ValidationFinding(
                    level="warning",
                    check="allowed_tools",
                    message="'allowed-tools' 应为字符串列表(list[str])",
                )
            )
        return findings
