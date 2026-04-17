"""Per-agent memory manager — append-only memory log per persona."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from tianshu.persona.model import AgentPersona

logger = logging.getLogger(__name__)


class PersonaMemoryManager:
    """Manages per-persona memory files (append-only log)."""

    def read_memory(self, persona_id: str, personas_dir: Path) -> str:
        """Read the memory file for a persona."""
        memory_path = personas_dir / persona_id / "MEMORY.md"
        if not memory_path.exists():
            return ""
        try:
            return memory_path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Failed to read memory for %s", persona_id)
            return ""

    def append_log(
        self,
        persona_id: str,
        personas_dir: Path,
        entry: str,
        category: str = "O",
    ) -> None:
        """Append a log entry to persona's MEMORY.md."""
        memory_path = personas_dir / persona_id / "MEMORY.md"
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        line = f"\n- [{category}] {timestamp}: {entry}\n"

        try:
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            with memory_path.open("a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            logger.warning("Failed to append memory for %s", persona_id)

    async def on_agent_end(self, **context: object) -> None:
        """Hook handler for AGENT_END — log execution summary to persona memory."""
        edict = context.get("edict")
        memorial = context.get("memorial")
        persona = context.get("persona")
        personas_dir = context.get("personas_dir")

        if not persona or not personas_dir or not memorial:
            return

        persona_obj = persona if isinstance(persona, AgentPersona) else None
        if not persona_obj:
            return

        summary = getattr(memorial, "summary", None) or "No summary"
        edict_goal = getattr(edict, "goal", "unknown") if edict else "unknown"
        status = getattr(memorial, "status", "unknown")
        entry = f"Task '{edict_goal[:50]}' -> {status}: {summary[:100]}"

        self.append_log(
            persona_obj.id,
            Path(str(personas_dir)),
            entry,
        )
        return None
