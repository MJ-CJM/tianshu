"""SQLite-backed memory storage — thin wrapper over Storage methods."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.memory.models import MemoryEntry, MemoryQuery

if TYPE_CHECKING:
    from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class SQLiteMemoryBackend:
    """Delegates persistence to the shared Storage instance."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def save(self, entry: MemoryEntry) -> None:
        self._storage.save_memory_entry(entry)

    def recall(self, query: MemoryQuery) -> list[MemoryEntry]:
        return self._storage.search_memory(
            persona_id=query.persona_id,
            query=query.query,
            category=query.category,
            limit=query.limit,
            include_shared=query.include_shared,
        )

    def list_by_persona(self, persona_id: str, limit: int = 50) -> list[MemoryEntry]:
        return self._storage.list_memory_by_persona(persona_id, limit=limit)

    def delete(self, entry_id: str) -> bool:
        return self._storage.delete_memory_entry(entry_id)

    def delete_batch(self, entry_ids: list[str]) -> int:
        return self._storage.delete_memory_entries_batch(entry_ids)
