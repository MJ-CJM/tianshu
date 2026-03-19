"""Persona loader — discover and load persona definitions from disk."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from tianshu.persona.model import AgentPersona

logger = logging.getLogger(__name__)


class PersonaLoader:
    """Loads persona definitions from the personas/ directory."""

    def __init__(self, personas_dir: Path) -> None:
        self._dir = personas_dir
        self._personas: dict[str, AgentPersona] = {}

    def load_all(self) -> dict[str, AgentPersona]:
        """Discover and load all persona definitions."""
        if not self._dir.is_dir():
            logger.warning("Personas directory not found: %s", self._dir)
            return {}

        self._personas.clear()
        for entry in sorted(self._dir.iterdir()):
            if not entry.is_dir() or entry.name == "court":
                continue
            persona = self._load_persona(entry)
            if persona:
                self._personas[persona.id] = persona

        logger.info("Loaded %d personas", len(self._personas))
        return dict(self._personas)

    def get(self, persona_id: str) -> AgentPersona | None:
        return self._personas.get(persona_id)

    def _load_persona(self, persona_dir: Path) -> AgentPersona | None:
        soul_path = persona_dir / "SOUL.md"
        role_path = persona_dir / "ROLE.md"
        memory_path = persona_dir / "MEMORY.md"

        if not soul_path.exists() or not role_path.exists():
            logger.warning(
                "Persona %s missing SOUL.md or ROLE.md, skipping",
                persona_dir.name,
            )
            return None

        # Read metadata from SOUL.md frontmatter if present
        meta = self._read_frontmatter(soul_path)
        name = meta.get("name", persona_dir.name)
        department = meta.get("department", persona_dir.name)

        return AgentPersona(
            id=persona_dir.name,
            name=name,
            department=department,
            soul_path=soul_path,
            role_path=role_path,
            memory_path=memory_path,
            skills_dir=persona_dir / "skills" if (persona_dir / "skills").is_dir() else None,
            tools_allowed=meta.get("tools_allowed", []),
            tools_denied=meta.get("tools_denied", []),
            tool_tier_max=meta.get("tool_tier_max", 0),
            can_delegate=meta.get("can_delegate", False),
            delegates_to=meta.get("delegates_to", []),
        )

    @staticmethod
    def _read_frontmatter(path: Path) -> dict:
        """Read YAML frontmatter from a markdown file."""
        try:
            text = path.read_text(encoding="utf-8")
            if not text.startswith("---"):
                return {}
            end = text.index("---", 3)
            return yaml.safe_load(text[3:end]) or {}
        except Exception:
            return {}
