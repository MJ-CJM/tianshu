"""EventEnvelope model — structured event for the EventBus."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from ulid import ULID


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(ULID()))
    event_type: str
    edict_id: str | None = None
    memorial_id: str | None = None
    attempt: int = 1
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: str = "system"
    payload: dict[str, Any] = Field(default_factory=dict)


def make_event(
    event_type: str,
    edict_id: str | None = None,
    memorial_id: str | None = None,
    producer: str = "system",
    payload: dict[str, Any] | None = None,
    attempt: int = 1,
) -> EventEnvelope:
    return EventEnvelope(
        event_type=event_type,
        edict_id=edict_id,
        memorial_id=memorial_id,
        attempt=attempt,
        producer=producer,
        payload=payload or {},
    )
