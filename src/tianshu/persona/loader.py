"""Persona loader — dual-source: file system (seed) + SQLite (runtime)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from tianshu.persona.model import AgentPersona

logger = logging.getLogger(__name__)


class PersonaLoader:
    """Loads persona definitions from SQLite, seeding from file system on first run."""

    def __init__(self, personas_dir: Path, storage=None) -> None:
        self._dir = personas_dir
        self._storage = storage
        self._personas: dict[str, AgentPersona] = {}

    def load_all(self) -> dict[str, AgentPersona]:
        """Load from SQLite (primary) + file system (seed)."""
        if self._storage:
            self._seed_from_files()
            self._load_from_db()
        else:
            self._load_from_files()
        logger.info("Loaded %d personas", len(self._personas))
        return dict(self._personas)

    def get(self, persona_id: str) -> AgentPersona | None:
        return self._personas.get(persona_id)

    def save(self, persona: AgentPersona) -> None:
        """Save or update a persona in SQLite and in-memory cache."""
        if not self._storage:
            raise RuntimeError("Storage not configured for persona persistence")
        self._storage.save_persona(self._persona_to_dict(persona))
        self._personas[persona.id] = persona

    def delete(self, persona_id: str) -> bool:
        """Delete a persona from SQLite, in-memory cache, and file system."""
        if not self._storage:
            raise RuntimeError("Storage not configured for persona persistence")
        deleted = self._storage.delete_persona(persona_id)
        self._personas.pop(persona_id, None)
        # Remove file system directory so _seed_from_files won't resurrect it
        persona_dir = self._dir / persona_id
        if persona_dir.is_dir():
            import shutil
            shutil.rmtree(persona_dir)
            logger.info("Removed persona directory: %s", persona_dir)
        return deleted

    # --- Internal ---

    def _seed_from_files(self) -> None:
        """Ensure file-system persona directories are available as prompt templates.

        File directories (e.g. personas/ducha/) serve as department-level prompt
        templates (SOUL.md, ROLE.md).  They are NOT auto-seeded into the database
        as standalone personas.  Named personas in the DB reference their department
        directory for prompt files via the ``department`` field.
        """
        # No-op: file directories are used as template sources only.
        # DB personas link to them via _dict_to_persona() using the department field.

    def _load_from_db(self) -> None:
        """Load all personas from SQLite into in-memory cache."""
        self._personas.clear()
        rows = self._storage.list_personas()
        for row in rows:
            persona = self._dict_to_persona(row)
            self._personas[persona.id] = persona

    def _load_from_files(self) -> None:
        """Legacy: load directly from file system (no SQLite)."""
        if not self._dir.is_dir():
            logger.warning("Personas directory not found: %s", self._dir)
            return
        self._personas.clear()
        for entry in sorted(self._dir.iterdir()):
            if not entry.is_dir() or entry.name == "court":
                continue
            persona = self._load_persona_from_dir(entry)
            if persona:
                self._personas[persona.id] = persona

    def _load_persona_from_dir(self, persona_dir: Path) -> AgentPersona | None:
        soul_path = persona_dir / "SOUL.md"
        role_path = persona_dir / "ROLE.md"
        memory_path = persona_dir / "MEMORY.md"

        if not soul_path.exists() or not role_path.exists():
            missing = []
            if not soul_path.exists():
                missing.append("SOUL.md")
            if not role_path.exists():
                missing.append("ROLE.md")
            logger.warning(
                "Persona '%s' missing %s — skipping. "
                "Create these files in %s to activate this persona.",
                persona_dir.name, " and ".join(missing), persona_dir,
            )
            return None

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
            llm_config_name=meta.get("llm_config_name"),
            skills_allowed=meta.get("skills_allowed", []),
        )

    @staticmethod
    def _persona_to_dict(persona: AgentPersona) -> dict:
        return {
            "id": persona.id,
            "name": persona.name,
            "department": persona.department,
            "tools_allowed": persona.tools_allowed,
            "tools_denied": persona.tools_denied,
            "skills_allowed": persona.skills_allowed,
            "tool_tier_max": persona.tool_tier_max,
            "can_delegate": persona.can_delegate,
            "delegates_to": persona.delegates_to,
            "soul_path": str(persona.soul_path),
            "role_path": str(persona.role_path),
            "llm_config_name": persona.llm_config_name,
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _dict_to_persona(self, d: dict) -> AgentPersona:
        # Resolve prompt files: prefer department directory (shared template),
        # fall back to persona ID directory, then explicit DB path.
        dept_dir = self._dir / d.get("department", d["id"])
        id_dir = self._dir / d["id"]
        if dept_dir.is_dir():
            persona_dir = dept_dir
        elif id_dir.is_dir():
            persona_dir = id_dir
        else:
            persona_dir = dept_dir  # will yield missing-file warnings
        soul_path = Path(d["soul_path"]) if d.get("soul_path") and Path(d["soul_path"]).exists() else persona_dir / "SOUL.md"
        role_path = Path(d["role_path"]) if d.get("role_path") and Path(d["role_path"]).exists() else persona_dir / "ROLE.md"
        memory_path = persona_dir / "MEMORY.md"
        skills_dir = persona_dir / "skills" if (persona_dir / "skills").is_dir() else None

        return AgentPersona(
            id=d["id"],
            name=d["name"],
            department=d["department"],
            soul_path=soul_path,
            role_path=role_path,
            memory_path=memory_path,
            skills_dir=skills_dir,
            tools_allowed=d.get("tools_allowed", []),
            tools_denied=d.get("tools_denied", []),
            skills_allowed=d.get("skills_allowed", []),
            tool_tier_max=d.get("tool_tier_max", 0),
            can_delegate=d.get("can_delegate", False),
            delegates_to=d.get("delegates_to", []),
            llm_config_name=d.get("llm_config_name"),
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
