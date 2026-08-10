"""Persona loader — dual-source: file system (seed) + SQLite (runtime).

SOUL.md / ROLE.md identity files live in two layers:

- ``personas/{department}/`` (git-tracked)   — department-level template,
  used as seed source for new officials.
- ``~/.tianshu/personas/{persona_id}/``      — per-official runtime identity;
  each persona gets its own copy that can diverge and "evolve" independently.

On first load of a persona, the runtime directory is created and seeded from
the department template. Subsequent edits through the UI write to the runtime
path only — the git template is untouched. This mirrors the MEMORY.md pattern.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import yaml

from tianshu.persona.model import AgentPersona

logger = logging.getLogger(__name__)


_IDENTITY_FILES = ("SOUL.md", "ROLE.md")


class PersonaLoader:
    """Loads persona definitions from SQLite, seeding from file system on first run."""

    def __init__(
        self,
        personas_dir: Path,
        storage=None,
        runtime_personas_dir: Path | None = None,
    ) -> None:
        self._dir = personas_dir
        self._storage = storage
        if runtime_personas_dir is None:
            runtime_personas_dir = Path("~/.tianshu/personas").expanduser()
        self._runtime_dir = Path(runtime_personas_dir).expanduser()
        self._personas: dict[str, AgentPersona] = {}

    @property
    def runtime_dir(self) -> Path:
        return self._runtime_dir

    def repoint_runtime(self, new_runtime_dir: Path) -> None:
        """切换 runtime 人格根目录（位面切换时调用）并重载内存中的人格。"""
        self._runtime_dir = Path(new_runtime_dir).expanduser()
        self._personas.clear()
        self.load_all()

    def ensure_runtime_identity(
        self,
        persona_id: str,
        template_source_dir: Path,
    ) -> tuple[Path, Path]:
        """Ensure runtime SOUL.md/ROLE.md exist for a persona; seed if missing.

        Returns (soul_path, role_path) pointing to the runtime copies.
        Seeding is idempotent: existing runtime files are never overwritten,
        so each persona can diverge from the template freely.
        """
        target_dir = self._runtime_dir / persona_id
        target_dir.mkdir(parents=True, exist_ok=True)
        for fname in _IDENTITY_FILES:
            target = target_dir / fname
            if target.exists():
                continue
            template = template_source_dir / fname
            if template.exists():
                shutil.copy2(template, target)
                logger.info(
                    "Seeded %s/%s from %s",
                    persona_id,
                    fname,
                    template,
                )
        return target_dir / "SOUL.md", target_dir / "ROLE.md"

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
        """Delete a persona from SQLite, in-memory cache, and runtime overlay.

        Packaged defaults (``self._dir``) are immutable and never touched;
        the migration ledger guarantees a deleted default is not reseeded.
        """
        if not self._storage:
            raise RuntimeError("Storage not configured for persona persistence")
        deleted = self._storage.delete_persona(persona_id)
        self._personas.pop(persona_id, None)
        runtime_dir = self._runtime_dir / persona_id
        if runtime_dir.is_dir():
            import shutil

            shutil.rmtree(runtime_dir)
            logger.info("Removed runtime persona overlay: %s", runtime_dir)
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
        template_soul = persona_dir / "SOUL.md"
        template_role = persona_dir / "ROLE.md"
        memory_path = persona_dir / "MEMORY.md"

        if not template_soul.exists() or not template_role.exists():
            missing = []
            if not template_soul.exists():
                missing.append("SOUL.md")
            if not template_role.exists():
                missing.append("ROLE.md")
            logger.warning(
                "Persona '%s' missing %s — skipping. "
                "Create these files in %s to activate this persona.",
                persona_dir.name,
                " and ".join(missing),
                persona_dir,
            )
            return None

        meta = self._read_frontmatter(template_soul)
        name = meta.get("name", persona_dir.name)
        department = meta.get("department", persona_dir.name)

        # Seed runtime identity from this template dir (persona_id == dir name here)
        soul_path, role_path = self.ensure_runtime_identity(
            persona_dir.name,
            persona_dir,
        )

        return AgentPersona(
            id=persona_dir.name,
            name=name,
            department=department,
            title=meta.get("title"),
            soul_path=soul_path,
            role_path=role_path,
            memory_path=memory_path,
            skills_dir=persona_dir / "skills" if (persona_dir / "skills").is_dir() else None,
            tools_allowed=meta.get("tools_allowed", []),
            tools_denied=meta.get("tools_denied", []),
            allowed_paths=meta.get("allowed_paths", []),
            workspace_dir=meta.get("workspace_dir", ""),
            tool_tier_max=meta.get("tool_tier_max", 4),
            can_delegate=meta.get("can_delegate", False),
            memory_global_read=meta.get("memory_global_read", False),
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
            "title": persona.title,
            "tools_allowed": persona.tools_allowed,
            "tools_denied": persona.tools_denied,
            "skills_allowed": persona.skills_allowed,
            "tool_tier_max": persona.tool_tier_max,
            "workspace_dir": persona.workspace_dir,
            "can_delegate": persona.can_delegate,
            "memory_global_read": persona.memory_global_read,
            "delegates_to": persona.delegates_to,
            "soul_path": str(persona.soul_path),
            "role_path": str(persona.role_path),
            "llm_config_name": persona.llm_config_name,
            "created_at": datetime.now(UTC).isoformat(),
        }

    def _dict_to_persona(self, d: dict) -> AgentPersona:
        # Locate the department template directory (the seed source).
        dept_dir = self._dir / d.get("department", d["id"])
        id_dir = self._dir / d["id"]
        if dept_dir.is_dir():
            template_dir = dept_dir
        elif id_dir.is_dir():
            template_dir = id_dir
        else:
            template_dir = dept_dir  # will yield missing-file warnings on first use

        # SOUL.md / ROLE.md: per-persona runtime copies (seeded from template).
        # Stored DB paths are ignored — runtime location is authoritative.
        soul_path, role_path = self.ensure_runtime_identity(d["id"], template_dir)

        memory_path = template_dir / "MEMORY.md"
        skills_dir = template_dir / "skills" if (template_dir / "skills").is_dir() else None

        return AgentPersona(
            id=d["id"],
            name=d["name"],
            department=d["department"],
            title=d.get("title"),
            soul_path=soul_path,
            role_path=role_path,
            memory_path=memory_path,
            skills_dir=skills_dir,
            tools_allowed=d.get("tools_allowed", []),
            tools_denied=d.get("tools_denied", []),
            allowed_paths=d.get("allowed_paths", []),
            workspace_dir=d.get("workspace_dir", ""),
            skills_allowed=d.get("skills_allowed", []),
            tool_tier_max=d.get("tool_tier_max", 4),
            can_delegate=d.get("can_delegate", False),
            memory_global_read=d.get("memory_global_read", False),
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
