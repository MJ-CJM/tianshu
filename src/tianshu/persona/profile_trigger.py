"""ProfileSynthesizer EventBus wiring — AGENT_END + cron."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

PROFILE_TRIGGER_THRESHOLD = 20


class ProfileTrigger:
    def __init__(self, synthesizer, storage, threshold: int = PROFILE_TRIGGER_THRESHOLD):
        self._syn = synthesizer
        self._storage = storage
        self._threshold = threshold

    async def handle_agent_end(self, **context: Any) -> None:
        """HookRegistry AGENT_END hook body."""
        # Mirror MemoryManager._resolve_persona_id: prefer context["persona"].id
        persona = context.get("persona")
        persona_id: str | None = None
        if persona and hasattr(persona, "id") and persona.id:
            persona_id = persona.id
        if not persona_id:
            return
        count = self._storage.increment_persona_task_counter(persona_id)
        if count % self._threshold == 0:
            logger.info(
                "profile.synthesis triggered for %s at N=%d", persona_id, count
            )
            asyncio.create_task(
                self._syn.run(persona_id, trigger_source="agent_end_hook")
            )

    async def run_for_all_personas(self, trigger_source: str = "cron") -> None:
        """Daily cron body: synthesize every active persona."""
        persona_loader = self._syn._personas
        for persona in persona_loader.load_all().values():
            try:
                await self._syn.run(persona.id, trigger_source=trigger_source)
            except Exception:
                logger.exception("cron synthesis failed for %s", persona.id)
