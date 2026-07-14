"""In-process async EventBus with priority-ordered named consumers."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Collection, Coroutine
from dataclasses import dataclass
from typing import Any

from tianshu.models.events import EventEnvelope

logger = logging.getLogger(__name__)

EventHandler = Callable[[EventEnvelope], Coroutine[Any, Any, None]]


@dataclass(frozen=True, slots=True)
class ConsumerDispatchResult:
    """Outcome from invoking one named consumer."""

    consumer_name: str
    succeeded: bool
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Every attempted consumer outcome for one event envelope."""

    event_id: str
    results: tuple[ConsumerDispatchResult, ...]


class _HandlerEntry:
    __slots__ = ("consumer_name", "handler", "priority")

    def __init__(self, consumer_name: str, handler: EventHandler, priority: int) -> None:
        self.consumer_name = consumer_name
        self.handler = handler
        self.priority = priority


class EventBus:
    """Pure-asyncio event bus with priority ordering and exception isolation.

    ``dispatch()`` reports each named consumer outcome to durable callers.
    ``emit()`` and ``fire()`` remain best-effort compatibility paths and make
    no persistence or redelivery guarantee.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[_HandlerEntry]] = defaultdict(list)
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def dispatch(
        self,
        event: EventEnvelope,
        *,
        skip_consumers: Collection[str] = (),
    ) -> DispatchReport:
        """Invoke unskipped named consumers and report every outcome."""
        skipped = frozenset(skip_consumers)
        results: list[ConsumerDispatchResult] = []
        for entry in self._entries_for(event.event_type):
            if entry.consumer_name in skipped:
                continue
            try:
                await entry.handler(event)
            except Exception as exc:
                results.append(
                    ConsumerDispatchResult(
                        consumer_name=entry.consumer_name,
                        succeeded=False,
                        error=exc,
                    )
                )
            else:
                results.append(
                    ConsumerDispatchResult(
                        consumer_name=entry.consumer_name,
                        succeeded=True,
                    )
                )
        return DispatchReport(event_id=event.event_id, results=tuple(results))

    async def emit(self, event: EventEnvelope) -> None:
        """Best-effort sequential fan-out in priority order."""
        entries = self._entries_for(event.event_type)
        logger.debug(
            "[BUS] emit %s: edict=%s, memorial=%s, handlers=%d",
            event.event_type,
            event.edict_id,
            event.memorial_id,
            len(entries),
        )
        report = await self.dispatch(event)
        self._log_failures(event, report)

    def fire(self, event: EventEnvelope) -> None:
        """Schedule best-effort fan-out without blocking the caller."""
        entries = self._entries_for(event.event_type)
        logger.debug(
            "[BUS] fire %s: edict=%s, memorial=%s, handlers=%d",
            event.event_type,
            event.edict_id,
            event.memorial_id,
            len(entries),
        )
        task = asyncio.create_task(self._run_handlers(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def on(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        consumer_name: str,
        priority: int = 100,
    ) -> None:
        """Register one stable consumer name for an event type."""
        if not consumer_name.strip():
            raise ValueError("consumer_name must be non-blank")
        entries = self._handlers[event_type]
        if any(entry.consumer_name == consumer_name for entry in entries):
            raise ValueError(
                f"consumer_name {consumer_name!r} is already registered for {event_type!r}"
            )
        entries.append(_HandlerEntry(consumer_name, handler, priority))
        entries.sort(key=lambda entry: entry.priority)

    def off(self, event_type: str, handler: EventHandler) -> None:
        """Unregister a handler."""
        entries = self._handlers.get(event_type, [])
        self._handlers[event_type] = [entry for entry in entries if entry.handler is not handler]

    def _entries_for(self, event_type: str) -> list[_HandlerEntry]:
        if event_type == "*":
            return list(self._handlers.get(event_type, ()))
        entries = [
            *self._handlers.get("*", ()),
            *self._handlers.get(event_type, ()),
        ]
        return sorted(entries, key=lambda entry: entry.priority)

    async def _run_handlers(self, event: EventEnvelope) -> None:
        report = await self.dispatch(event)
        self._log_failures(event, report)

    @staticmethod
    def _log_failures(event: EventEnvelope, report: DispatchReport) -> None:
        for result in report.results:
            if result.error is None:
                continue
            logger.error(
                "Consumer %s failed for event %s",
                result.consumer_name,
                event.event_type,
                exc_info=(type(result.error), result.error, result.error.__traceback__),
            )
