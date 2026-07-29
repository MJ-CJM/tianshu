"""LLM-driven memory compaction + context window guard."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from tianshu.memory.models import CompactionResult, MemoryEntry

if TYPE_CHECKING:
    from tianshu.config_manager import ConfigManager

logger = logging.getLogger(__name__)

_COMPACTION_PROMPT = """You are a memory compaction agent. Given the following memory entries for persona '{persona_id}', produce a concise summary that preserves the most important facts, entities, and insights.

Entries:
{entries_text}

Output a single concise paragraph summarizing the key information. Focus on facts, decisions, and outcomes. Drop redundant or trivial observations."""

# Maximum number of history messages to inject (context window guard)
MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_CHARS = 8000


class MemoryCompactor:
    """Compacts old memory entries into summary entries using LLM."""

    def __init__(
        self, config_manager: ConfigManager, provider_manager: Any = None
    ) -> None:
        self._config_manager = config_manager
        self._provider_manager = provider_manager

    async def compact(
        self,
        persona_id: str,
        entries: list[MemoryEntry],
    ) -> CompactionResult:
        """Compact a list of memory entries into a summary entry."""
        if len(entries) <= 3:
            return CompactionResult(
                original_count=len(entries),
                compacted_count=len(entries),
                summary="Too few entries to compact",
            )

        entries_text = "\n".join(f"- [{e.category}] {e.content}" for e in entries)

        if self._provider_manager is not None:
            llm = self._provider_manager.get_client_for_slot(
                "memory", temperature=0.3, max_tokens=1024
            )
        else:
            from tianshu.llm import LLMClient

            state = self._config_manager.state
            llm = LLMClient(
                model=state.model,
                api_key=state.api_key,
                api_base=state.api_base,
                temperature=0.3,
                max_tokens=1024,
            )

        prompt = _COMPACTION_PROMPT.format(
            persona_id=persona_id,
            entries_text=entries_text,
        )
        response = await llm.chat(
            [
                {"role": "system", "content": "You are a concise memory compaction agent."},
                {"role": "user", "content": prompt},
            ]
        )

        summary = response.content or "Compaction failed"
        tokens_saved = sum(len(e.content) // 4 for e in entries) - len(summary) // 4

        return CompactionResult(
            original_count=len(entries),
            compacted_count=1,
            summary=summary,
            tokens_saved=max(0, tokens_saved),
        )

    @staticmethod
    def guard_context(
        memories: list[MemoryEntry],
    ) -> list[dict]:
        """Convert memories to history messages within context budget."""
        messages: list[dict] = []
        total_chars = 0

        for entry in memories[:MAX_HISTORY_MESSAGES]:
            content = f"[Memory/{entry.category}] {entry.content}"
            if total_chars + len(content) > MAX_HISTORY_CHARS:
                break
            messages.append({"role": "system", "content": content})
            total_chars += len(content)

        return messages
