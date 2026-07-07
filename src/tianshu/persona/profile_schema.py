"""PROFILE.md schema — frontmatter + 4 sections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import yaml

AUTO_SECTION_MARKER = "<!-- Auto-generated section ends. Manual notes below preserved. -->"


@dataclass
class ProfileFrontmatter:
    persona_id: str
    persona_name: str
    version: int = 1
    last_synthesized: str = ""
    synthesizer_model: str = ""
    data_window: str = "14d"
    data_sources: dict[str, int] = field(default_factory=dict)
    manually_edited: bool = False
    degraded: bool = False

    def to_yaml(self) -> str:
        d = {
            "persona_id": self.persona_id,
            "persona_name": self.persona_name,
            "version": self.version,
            "last_synthesized": self.last_synthesized
            or datetime.now(UTC).isoformat(timespec="seconds"),
            "synthesizer_model": self.synthesizer_model,
            "data_window": self.data_window,
            "data_sources": self.data_sources,
            "manually_edited": self.manually_edited,
            "degraded": self.degraded,
        }
        return yaml.safe_dump(d, allow_unicode=True, sort_keys=False).strip()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProfileFrontmatter:
        return cls(
            persona_id=str(d.get("persona_id", "")),
            persona_name=str(d.get("persona_name", "")),
            version=int(d.get("version", 1)),
            last_synthesized=str(d.get("last_synthesized", "")),
            synthesizer_model=str(d.get("synthesizer_model", "")),
            data_window=str(d.get("data_window", "14d")),
            data_sources=dict(d.get("data_sources", {})),
            manually_edited=bool(d.get("manually_edited", False)),
            degraded=bool(d.get("degraded", False)),
        )


@dataclass
class ProfileSections:
    """Four rendered sections of PROFILE.md."""

    specialties_md: str = ""
    task_distribution_md: str = ""
    health_md: str = ""
    degradations_md: str = ""


def parse_profile(markdown: str) -> tuple[ProfileFrontmatter | None, str, str]:
    """Parse PROFILE.md → (frontmatter, auto_section, manual_section).

    Returns frontmatter=None if not parseable. Manual section is content after
    AUTO_SECTION_MARKER (empty string if marker missing).
    """
    fm: ProfileFrontmatter | None = None
    body = markdown
    if markdown.startswith("---\n"):
        end = markdown.find("\n---\n", 4)
        if end > 0:
            yaml_text = markdown[4:end]
            try:
                raw = yaml.safe_load(yaml_text) or {}
                fm = ProfileFrontmatter.from_dict(raw)
            except yaml.YAMLError:
                fm = None
            body = markdown[end + 5 :]
    if AUTO_SECTION_MARKER in body:
        auto, _, manual = body.partition(AUTO_SECTION_MARKER)
        return fm, auto.strip(), manual.strip()
    return fm, body.strip(), ""
