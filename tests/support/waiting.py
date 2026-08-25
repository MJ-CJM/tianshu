"""Predicate-based EventBus waits for deterministic async tests."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Iterable
from types import TracebackType

from tianshu.bus.event_bus import EventBus
from tianshu.models.events import EventEnvelope

EventPredicate = Callable[[EventEnvelope], bool]


class EventProbe:
    """Buffer selected events and wait for an exact predicate match."""

    def __init__(self, bus: EventBus, event_types: Iterable[str]) -> None:
        selected = tuple(dict.fromkeys(event_types))
        if not selected or any(not event_type.strip() for event_type in selected):
            raise ValueError("event_types must contain non-blank values")
        self._bus = bus
        self._event_types = selected
        self._pending: deque[EventEnvelope] = deque()
        self._seen_types: list[str] = []
        self._condition = asyncio.Condition()
        self._closed = False
        self._handler = self._capture
        for event_type in self._event_types:
            self._bus.on_local(event_type, self._handler)

    async def _capture(self, event: EventEnvelope) -> None:
        async with self._condition:
            self._pending.append(event)
            self._seen_types.append(event.event_type)
            self._condition.notify_all()

    async def wait_for(
        self,
        predicate: EventPredicate,
        *,
        timeout: float,
    ) -> EventEnvelope:
        """Consume the first buffered or future event matching ``predicate``."""
        if self._closed:
            raise RuntimeError("event probe is closed")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        async def _wait() -> EventEnvelope:
            async with self._condition:
                while True:
                    for index, event in enumerate(self._pending):
                        if predicate(event):
                            del self._pending[index]
                            return event
                    await self._condition.wait()

        return await asyncio.wait_for(_wait(), timeout=timeout)

    def drain(self) -> tuple[EventEnvelope, ...]:
        """Consume every unmatched buffered event."""
        events = tuple(self._pending)
        self._pending.clear()
        return events

    def seen_types(self) -> tuple[str, ...]:
        """Return all event types observed since probe creation."""
        return tuple(self._seen_types)

    def close(self) -> None:
        """Detach subscriptions. Repeated close calls are harmless."""
        if self._closed:
            return
        for event_type in self._event_types:
            self._bus.off_local(event_type, self._handler)
        self._closed = True

    async def __aenter__(self) -> EventProbe:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


async def await_event(
    bus: EventBus,
    event_type: str,
    predicate: EventPredicate,
    *,
    timeout: float,
) -> EventEnvelope:
    """Wait for one future event without using a sleep as synchronization."""
    async with EventProbe(bus, (event_type,)) as probe:
        return await probe.wait_for(predicate, timeout=timeout)
