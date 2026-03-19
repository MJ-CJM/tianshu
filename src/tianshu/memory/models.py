"""Memory data models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from ulid import ULID


class MemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(ULID()))
    persona_id: str
    edict_id: str | None = None
    memorial_id: str | None = None
    category: Literal["observation", "insight", "entity", "summary"] = "observation"
    content: str
    source: Literal["agent", "compaction", "reflection"] = "agent"
    confidence: float = 1.0
    entity_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None
    access_level: Literal["private", "shared", "court"] = "private"


class CompactionResult(BaseModel):
    original_count: int
    compacted_count: int
    summary: str
    tokens_saved: int = 0


class MemoryQuery(BaseModel):
    persona_id: str
    query: str | None = None
    category: str | None = None
    limit: int = 20
    include_shared: bool = False
