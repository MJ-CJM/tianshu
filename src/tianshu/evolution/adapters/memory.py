"""Memory candidate source validation."""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tianshu.evolution.adapters.base import AdapterError, BaseCandidateAdapter
from tianshu.memory.models import MemoryEntry
from tianshu.memory.safety import validate_content
from tianshu.models.canonical import JsonValue
from tianshu.models.evolution_candidate import CandidateKind


class _MemorySourceV1(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    persona_id: str
    edict_id: str | None = None
    memorial_id: str | None = None
    category: Literal["observation", "insight", "entity", "summary"] = "observation"
    content: str
    source: Literal["agent", "compaction", "reflection"] = "agent"
    confidence: float = Field(default=1.0, ge=0, le=1)
    entity_refs: list[str] = Field(default_factory=list)
    created_at: datetime
    expires_at: datetime | None = None
    access_level: Literal["private", "shared", "court"] = "private"


class MemoryCandidateAdapter(BaseCandidateAdapter):
    kind = CandidateKind.MEMORY

    def _normalize_domain(self, payload: Mapping[str, object]) -> dict[str, JsonValue]:
        try:
            source = _MemorySourceV1.model_validate(payload)
            entry = MemoryEntry.model_validate(source.model_dump())
            validate_content(entry.content)
        except (ValidationError, TypeError, ValueError):
            raise AdapterError("memory source validation failed") from None
        return source.model_dump(mode="json")


__all__ = ["MemoryCandidateAdapter"]
