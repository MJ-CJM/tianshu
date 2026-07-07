"""Reflector — LLM-driven reflection and insight extraction."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tianshu.memory.models import MemoryEntry

if TYPE_CHECKING:
    from tianshu.config_manager import ConfigManager
    from tianshu.memory.markdown_backend import MarkdownMemoryBackend

logger = logging.getLogger(__name__)

_REFLECTION_PROMPT = """You are a reflection agent for persona '{persona_id}'. Analyze the following observations and extract key insights, patterns, and learnings.

Observations:
{observations}

Generate 1-3 concise insight statements. Each insight should be a single sentence capturing a pattern, lesson, or strategic observation. Focus on actionable knowledge.

Format each insight on its own line, prefixed with "- "."""

# Minimum cooldown between reflections (seconds)
REFLECTION_COOLDOWN = 3600  # 1 hour


class Reflector:
    """Generates insights from observations via LLM reflection."""

    def __init__(
        self,
        config_manager: ConfigManager,
        md_backend: MarkdownMemoryBackend | None = None,
    ) -> None:
        self._config_manager = config_manager
        self._md_backend = md_backend
        self._last_reflection: dict[str, datetime] = {}

    def can_reflect(self, persona_id: str) -> bool:
        """Check if enough time has passed since last reflection."""
        last = self._last_reflection.get(persona_id)
        if not last:
            return True
        elapsed = (datetime.now(UTC) - last).total_seconds()
        return elapsed >= REFLECTION_COOLDOWN

    async def reflect(
        self,
        persona_id: str,
        observations: list[MemoryEntry],
    ) -> list[MemoryEntry]:
        """Reflect on observations and generate insight entries."""
        if not observations:
            return []

        if not self.can_reflect(persona_id):
            logger.debug("Reflection cooldown active for %s", persona_id)
            return []

        obs_text = "\n".join(f"- {o.content}" for o in observations[:20])

        from tianshu.llm import LLMClient

        state = self._config_manager.state
        llm = LLMClient(
            model=state.model,
            api_key=state.api_key,
            api_base=state.api_base,
            temperature=0.5,
            max_tokens=512,
        )

        prompt = _REFLECTION_PROMPT.format(
            persona_id=persona_id,
            observations=obs_text,
        )

        response = await llm.chat(
            [
                {"role": "system", "content": "You produce concise strategic insights."},
                {"role": "user", "content": prompt},
            ]
        )

        self._last_reflection[persona_id] = datetime.now(UTC)

        content = response.content or ""
        insights: list[MemoryEntry] = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if line.startswith("- "):
                line = line[2:].strip()
            if not line:
                continue
            insights.append(
                MemoryEntry(
                    persona_id=persona_id,
                    category="insight",
                    content=line,
                    source="reflection",
                    confidence=0.8,
                )
            )

        # Write insights to persona MEMORY.md (source of truth)
        if insights and self._md_backend and hasattr(self._md_backend, "write_core_memory"):
            date_str = datetime.now(UTC).strftime("%Y-%m-%d")
            insight_lines = "\n".join(f"- {i.content}" for i in insights)

            # Append to persona's MEMORY.md
            try:
                existing = self._md_backend.read_core_memory(persona_id)
                new_section = f"\n## Insights ({date_str})\n{insight_lines}\n"
                self._md_backend.write_core_memory(persona_id, existing + new_section)
            except Exception:
                logger.debug("Failed to write insights to MEMORY.md for %s", persona_id)

            # Also append cross-persona insights to court/MEMORY.md
            if persona_id != "court":
                try:
                    court_existing = self._md_backend.read_core_memory("court")
                    court_section = f"\n## {persona_id} Insights ({date_str})\n{insight_lines}\n"
                    self._md_backend.write_core_memory("court", court_existing + court_section)
                except Exception:
                    logger.debug("Failed to write cross-persona insights to court MEMORY.md")

        return insights
