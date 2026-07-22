"""Legacy event timeline consumer for durable EventEnvelope delivery."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tianshu.models.events import EventEnvelope
    from tianshu.storage import Storage


class EventHistoryConsumer:
    """Idempotently project Edict-scoped envelopes into legacy history."""

    consumer_name = "event_history.v1"

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    async def __call__(self, event: EventEnvelope) -> None:
        if event.edict_id is None:
            return
        self._storage.append_event_envelope(event)
