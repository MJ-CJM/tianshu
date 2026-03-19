"""In-process async EventBus with priority-ordered handlers."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any

from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage

logger = logging.getLogger(__name__)

EventHandler = Callable[[EventEnvelope], Coroutine[Any, Any, None]]


class _HandlerEntry:
    __slots__ = ("handler", "priority")

    def __init__(self, handler: EventHandler, priority: int) -> None:
        self.handler = handler
        self.priority = priority


class EventBus:
    """Pure-asyncio event bus with priority ordering and exception isolation."""

    def __init__(self, storage: Storage | None = None) -> None:
        self._handlers: dict[str, list[_HandlerEntry]] = defaultdict(list)
        self._storage = storage

    async def emit(self, event: EventEnvelope) -> None:
        """Persist event then fan-out to handlers in priority order."""
        if self._storage and event.edict_id:
            self._storage.append_event(
                edict_id=event.edict_id,
                memorial_id=event.memorial_id,
                event_type=event.event_type,
                payload=event.payload,
            )

        entries = self._handlers.get(event.event_type, [])
        for entry in entries:
            try:
                await asyncio.wait_for(entry.handler(event), timeout=30.0)
            except asyncio.TimeoutError:
                logger.error(
                    "Handler %s timed out for event %s",
                    entry.handler.__qualname__,
                    event.event_type,
                )
            except Exception:
                logger.exception(
                    "Handler %s failed for event %s",
                    entry.handler.__qualname__,
                    event.event_type,
                )

    def on(
        self,
        event_type: str,
        handler: EventHandler,
        priority: int = 100,
    ) -> None:
        """Register a handler. Lower priority number = earlier execution."""
        entries = self._handlers[event_type]
        entries.append(_HandlerEntry(handler, priority))
        entries.sort(key=lambda e: e.priority)

    def off(self, event_type: str, handler: EventHandler) -> None:
        """Unregister a handler."""
        entries = self._handlers.get(event_type, [])
        self._handlers[event_type] = [
            e for e in entries if e.handler is not handler
        ]
