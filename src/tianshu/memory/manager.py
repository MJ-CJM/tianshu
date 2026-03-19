"""MemoryManager — core memory operations + EventBus/Hook integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from tianshu.memory.access_control import MemoryAccessControl
from tianshu.memory.backends.sqlite_backend import SQLiteMemoryBackend
from tianshu.memory.compactor import MemoryCompactor
from tianshu.memory.models import CompactionResult, MemoryEntry, MemoryQuery
from tianshu.memory.reflect import Reflector

if TYPE_CHECKING:
    from tianshu.config_manager import ConfigManager
    from tianshu.models.events import EventEnvelope
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class MemoryManager:
    """Unified memory management for all personas.

    Integrates with:
    - EventBus: execution.completed, audit.completed → extract memories
    - Hooks: BEFORE_AGENT_START → inject memory context
             AGENT_END → persist execution memory
    """

    def __init__(
        self,
        storage: Storage,
        config_manager: ConfigManager,
        hook_registry: object | None = None,
    ) -> None:
        self._backend = SQLiteMemoryBackend(storage)
        self._compactor = MemoryCompactor(config_manager)
        self._reflector = Reflector(config_manager)
        self._access_control = MemoryAccessControl(storage)
        self._config_manager = config_manager
        self._hooks = hook_registry

    # --- Core operations ---

    def store(
        self,
        entry: MemoryEntry,
        writer: str | None = None,
    ) -> MemoryEntry:
        """Store a memory entry with optional access control check."""
        if writer and not self._access_control.can_store(writer, entry):
            logger.warning(
                "Access denied: %s cannot write to %s", writer, entry.persona_id
            )
            return entry
        self._backend.save(entry)
        logger.debug("Memory stored: %s for %s", entry.id, entry.persona_id)
        return entry

    def recall(
        self,
        query: MemoryQuery,
        requestor: str | None = None,
    ) -> list[MemoryEntry]:
        """Recall memories matching a query with optional access filtering."""
        entries = self._backend.recall(query)
        if requestor:
            entries = self._access_control.filter_readable(requestor, entries)
        return entries

    def list_by_persona(self, persona_id: str, limit: int = 50) -> list[MemoryEntry]:
        """List all memories for a persona."""
        return self._backend.list_by_persona(persona_id, limit=limit)

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry."""
        return self._backend.delete(entry_id)

    def delete_batch(self, entry_ids: list[str]) -> int:
        """Delete multiple memory entries. Returns count of deleted rows."""
        return self._backend.delete_batch(entry_ids)

    async def compact(self, persona_id: str) -> CompactionResult:
        """Compact old memories for a persona into a summary."""
        entries = self._backend.list_by_persona(persona_id, limit=100)
        if len(entries) <= 5:
            return CompactionResult(
                original_count=len(entries),
                compacted_count=len(entries),
                summary="Not enough entries to compact",
            )

        # BEFORE_COMPACTION hook — allow extracting important info before compaction
        if self._hooks and hasattr(self._hooks, "run"):
            from tianshu.executor.hooks import HookType
            await self._hooks.run(
                HookType.BEFORE_COMPACTION,
                persona_id=persona_id,
                entries=entries,
            )

        result = await self._compactor.compact(persona_id, entries)

        # Store compacted summary
        summary_entry = MemoryEntry(
            persona_id=persona_id,
            category="summary",
            content=result.summary,
            source="compaction",
        )
        self.store(summary_entry)

        # Delete original entries that were compacted
        for entry in entries:
            if entry.category != "summary":
                self._backend.delete(entry.id)

        return result

    async def retain(self, persona_id: str) -> CompactionResult:
        """Retain cycle: compact old memories to free context budget."""
        return await self.compact(persona_id)

    async def reflect(self, persona_id: str) -> list[MemoryEntry]:
        """Reflect cycle: generate insights from observations."""
        observations = self._backend.recall(MemoryQuery(
            persona_id=persona_id,
            category="observation",
            limit=20,
        ))
        if not observations:
            return []

        insights = await self._reflector.reflect(persona_id, observations)
        for insight in insights:
            self.store(insight)
        return insights

    # --- Hook handlers ---

    async def on_before_agent_start(self, **context: object) -> object:
        """BEFORE_AGENT_START hook — inject relevant memories into history."""
        from tianshu.executor.hooks import HookResult

        edict = context.get("edict")
        if not edict:
            return None

        # Determine persona from plan or default to 'bingbu'
        plan = context.get("plan")
        persona_id = "bingbu"
        if plan and hasattr(plan, "tasks") and plan.tasks:
            first_task = plan.tasks[0]
            if hasattr(first_task, "persona_id") and first_task.persona_id:
                persona_id = first_task.persona_id

        query = MemoryQuery(
            persona_id=persona_id,
            query=getattr(edict, "goal", None),
            include_shared=True,
            limit=10,
        )
        memories = self.recall(query)
        if not memories:
            return None

        history_messages = MemoryCompactor.guard_context(memories)
        if not history_messages:
            return None

        return HookResult(modified_args={"memory_history": history_messages})

    async def on_agent_end(self, **context: object) -> None:
        """AGENT_END hook — extract and store memories from execution.

        Only stores memories for COMPLETED executions that have a meaningful summary.
        """
        from tianshu.models import TaskStatus

        edict = context.get("edict")
        memorial = context.get("memorial")
        if not edict or not memorial:
            return

        # Only store completed executions with a real summary
        status = getattr(memorial, "status", None)
        summary = getattr(memorial, "summary", None)
        if status != TaskStatus.COMPLETED or not summary:
            return

        # Determine persona_id
        persona_id = "bingbu"
        persona = context.get("persona")
        if persona and hasattr(persona, "id"):
            persona_id = persona.id

        edict_goal = getattr(edict, "goal", "unknown")

        entry = MemoryEntry(
            persona_id=persona_id,
            edict_id=getattr(edict, "id", None),
            memorial_id=getattr(memorial, "id", None),
            category="observation",
            content=f"Task '{edict_goal[:80]}' -> {status.value}: {summary[:200]}",
            source="agent",
        )
        self.store(entry)

    # --- EventBus handlers ---

    async def handle_execution_completed(self, event: EventEnvelope) -> None:
        """EventBus handler for execution.completed — no-op.

        Redundant with on_agent_end which already stores the observation
        with actual summary content. Kept as empty handler to avoid breaking
        EventBus subscription wiring.
        """
        return

    async def handle_audit_completed(self, event: EventEnvelope) -> None:
        """EventBus handler for audit.completed — store audit insights."""
        if not event.memorial_id:
            return

        payload = event.payload or {}
        verdict = payload.get("verdict", "")
        if verdict in ("flag", "block"):
            reasons = payload.get("reasons", [])
            entry = MemoryEntry(
                persona_id="ducha",
                edict_id=event.edict_id,
                memorial_id=event.memorial_id,
                category="insight",
                content=f"Audit {verdict}: {', '.join(reasons[:3])}",
                source="agent",
            )
            self.store(entry)
