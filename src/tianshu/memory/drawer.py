"""Drawer model, DrawerResult, and MemoryBackend Protocol for the Memory Palace."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Drawer:
    """Minimum memory unit — stores a verbatim content chunk."""

    id: str
    wing: str  # Owner wing (persona ID, "court", or "emperor")
    room: str  # Topic room within the wing
    content: str  # Verbatim content (max ~800 chars)
    source_edict_id: str  # Edict that produced this memory
    timestamp: str  # ISO 8601 UTC
    category: str  # W(world) / B(biographical) / O(opinion) / D(decision)
    confidence: float  # 0.0–1.0
    chunk_index: int  # Position within multi-chunk source


@dataclass(frozen=True)
class DrawerResult:
    """Search result wrapping a drawer with relevance score."""

    drawer_id: str
    content: str
    wing: str
    room: str
    score: float  # 0.0–1.0 relevance
    matched_via: str  # "bm25" | "fts5" | "exact"


@dataclass(frozen=True)
class Closet:
    """Topic pointer — indexes into drawers without storing content."""

    id: str
    wing: str
    room: str
    topics: tuple[str, ...]
    entities: tuple[str, ...]
    drawer_ids: tuple[str, ...]


@dataclass(frozen=True)
class Tunnel:
    """Cross-wing link connecting two rooms."""

    id: str
    from_wing: str
    from_room: str
    to_wing: str
    to_room: str
    reason: str
    created_by: str


@runtime_checkable
class MemoryBackend(Protocol):
    """Pluggable memory storage backend."""

    async def store_drawer(self, drawer: Drawer) -> str: ...

    async def search(
        self,
        query: str,
        wing: str | None = None,
        room: str | None = None,
        n_results: int = 10,
    ) -> list[DrawerResult]: ...

    async def get_drawers(
        self,
        wing: str,
        room: str | None = None,
        limit: int = 100,
    ) -> list[Drawer]: ...

    async def delete_drawer(self, drawer_id: str) -> bool: ...

    async def get_l1(self, wing: str, max_chars: int = 3200) -> str: ...
