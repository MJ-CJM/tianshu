"""Prompt builder — 8-layer system prompt injection for persona-aware agents."""

from __future__ import annotations

import logging
from pathlib import Path

from tianshu.models.edict import Edict
from tianshu.persona.model import AgentPersona
from tianshu.skills.loader import SkillsLoader

logger = logging.getLogger(__name__)

_BASE_IDENTITY = (
    "You are Tianshu, an AI execution assistant. "
    "Follow the user's instructions and use available tools to complete tasks. "
    "When done, summarize the result concisely. "
    "If you cannot complete the task, explain why."
)


class PromptBuilder:
    """Builds system prompts with 8-layer injection."""

    def __init__(
        self,
        personas_dir: Path,
        skills_loader: SkillsLoader,
    ) -> None:
        self._personas_dir = personas_dir
        self._skills = skills_loader

    def build(
        self,
        edict: Edict,
        persona: AgentPersona | None = None,
        skills_char_budget: int = 30000,
    ) -> str:
        """Build system prompt with 8-layer injection order."""
        parts: list[str] = []

        # Layer 1: Base Identity
        parts.append(_BASE_IDENTITY)

        if persona:
            # Layer 2: COURT.md (shared court context)
            court_path = self._personas_dir / "court" / "COURT.md"
            court_text = self._read_file(court_path)
            if court_text:
                parts.append(court_text)

            # Layer 3: SOUL.md (persona identity)
            soul_text = self._read_file(persona.soul_path)
            if soul_text:
                parts.append(soul_text)

            # Layer 4: ROLE.md (persona role specifics)
            role_text = self._read_file(persona.role_path)
            if role_text:
                parts.append(role_text)

            # Layer 5: Per-agent MEMORY.md
            memory_text = self._read_file(persona.memory_path)
            if memory_text:
                parts.append(f"# Agent Memory\n\n{memory_text}")

            # Layer 6: Court MEMORY.md (shared memory)
            court_memory = self._personas_dir / "court" / "MEMORY.md"
            court_mem_text = self._read_file(court_memory)
            if court_mem_text:
                parts.append(f"# Court Memory\n\n{court_mem_text}")

        # Layer 7: Skills
        self._skills.set_char_budget(skills_char_budget)
        skills_text = self._skills.load_all()
        if skills_text:
            parts.append(skills_text)

        # Layer 8: Task Context
        parts.append(f"Current task ID: {edict.id}")

        return "\n\n".join(parts)

    @staticmethod
    def _read_file(path: Path) -> str:
        """Read a file, stripping frontmatter if present."""
        if not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8")
            # Strip YAML frontmatter
            if text.startswith("---"):
                try:
                    end = text.index("---", 3)
                    text = text[end + 3:].strip()
                except ValueError:
                    pass
            return text
        except Exception:
            logger.warning("Failed to read %s", path)
            return ""
