"""Recoverable at-least-once dispatch for durable outbox envelopes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from tianshu.bus.event_bus import EventBus
from tianshu.models.canonical import RedactedError
from tianshu.models.events import EventEnvelope
from tianshu.storage.outbox_repo import OutboxRecord


class _OutboxOperations(Protocol):
    def claim_batch(
        self,
        *,
        owner_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> list[OutboxRecord]: ...

    def mark_published(
        self,
        *,
        event_id: str,
        owner_id: str,
        expected_version: int,
        now: datetime,
    ) -> bool: ...

    def mark_failed(
        self,
        *,
        event_id: str,
        owner_id: str,
        expected_version: int,
        error: RedactedError,
        available_at: datetime,
    ) -> bool: ...

    def record_consumption(
        self,
        *,
        event_id: str,
        consumer_name: str,
        result_hash: str | None,
    ) -> bool: ...

    def consumed_consumers(self, event_id: str) -> frozenset[str]: ...


class OutboxDispatcher:
    """Lease, reconstruct, and deliver durable rows with at-least-once transport."""

    def __init__(
        self,
        repository: _OutboxOperations,
        event_bus: EventBus,
        *,
        owner_id: str,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 30,
        base_backoff_seconds: float = 1,
        max_backoff_seconds: float = 300,
        poll_interval_seconds: float = 1,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-blank")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if base_backoff_seconds <= 0:
            raise ValueError("base_backoff_seconds must be positive")
        if max_backoff_seconds < base_backoff_seconds:
            raise ValueError("max_backoff_seconds must be at least base_backoff_seconds")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._repository = repository
        self._event_bus = event_bus
        self._owner_id = owner_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_seconds = lease_seconds
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._stopped_event = asyncio.Event()
        self._stopped_event.set()
        self._running = False

    async def drain_once(self, *, limit: int = 50) -> int:
        records = self._repository.claim_batch(
            owner_id=self._owner_id,
            now=self._clock(),
            limit=limit,
            lease_seconds=self._lease_seconds,
        )
        for record in records:
            await self._dispatch_record(record)
        return len(records)

    async def run(self) -> None:
        if self._running:
            raise RuntimeError("outbox dispatcher is already running")
        self._running = True
        self._stop_event.clear()
        self._stopped_event.clear()
        try:
            while not self._stop_event.is_set():
                await self.drain_once()
                if self._stop_event.is_set():
                    break
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval_seconds,
                    )
        finally:
            self._running = False
            self._stopped_event.set()

    async def stop(self) -> None:
        self._stop_event.set()
        if self._running:
            await self._stopped_event.wait()

    async def _dispatch_record(self, record: OutboxRecord) -> None:
        try:
            event = _event_from_record(record)
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            self._mark_failure(
                record,
                error,
                code="malformed_outbox_event",
                message="durable event envelope is invalid",
                retryable=False,
            )
            return

        skip_consumers = self._repository.consumed_consumers(record.event_id)
        report = await self._event_bus.dispatch(event, skip_consumers=skip_consumers)
        failures: list[Exception] = []
        for result in report.results:
            if result.succeeded:
                self._repository.record_consumption(
                    event_id=record.event_id,
                    consumer_name=result.consumer_name,
                    result_hash=None,
                )
            elif result.error is not None:
                failures.append(result.error)

        if failures:
            self._mark_failure(
                record,
                failures[0],
                code="consumer_dispatch_failed",
                message="one or more consumers failed",
                retryable=True,
            )
            return
        self._repository.mark_published(
            event_id=record.event_id,
            owner_id=self._owner_id,
            expected_version=record.version,
            now=self._clock(),
        )

    def _mark_failure(
        self,
        record: OutboxRecord,
        error: Exception,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        now = self._clock()
        self._repository.mark_failed(
            event_id=record.event_id,
            owner_id=self._owner_id,
            expected_version=record.version,
            error=RedactedError(
                code=code,
                message=message,
                retryable=retryable,
                details_hash=_bounded_error_hash(error),
            ),
            available_at=now + timedelta(seconds=self._backoff_seconds(record.attempt_count)),
        )

    def _backoff_seconds(self, attempt_count: int) -> float:
        exponent = min(max(attempt_count - 1, 0), 30)
        return min(
            self._base_backoff_seconds * (2**exponent),
            self._max_backoff_seconds,
        )


def _event_from_record(record: OutboxRecord) -> EventEnvelope:
    event_id = _required_text(record.event_id, "event_id")
    event_type = _required_text(record.event_type, "event_type")
    aggregate_type = _required_text(record.aggregate_type, "aggregate_type")
    producer = _required_text(record.producer, "producer")
    edict_id = _optional_text(record.edict_id, "edict_id")
    memorial_id = _optional_text(record.memorial_id, "memorial_id")
    if memorial_id is not None and edict_id is None:
        raise ValueError("memorial_id requires edict_id")
    expected_aggregate_type = "edict" if edict_id is not None else "system"
    if aggregate_type != expected_aggregate_type:
        raise ValueError("aggregate_type does not match envelope identity")
    if not isinstance(record.attempt_count, int) or isinstance(record.attempt_count, bool):
        raise TypeError("attempt_count must be an integer")
    if record.attempt_count <= 0:
        raise ValueError("attempt_count must be positive")

    occurred_at = _required_text(record.occurred_at, "occurred_at")
    timestamp = datetime.fromisoformat(occurred_at)
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise ValueError("occurred_at must be timezone-aware UTC")

    payload_json = _required_text(record.payload_json, "payload_json", allow_blank=True)
    payload = json.loads(payload_json, parse_constant=_reject_json_constant)
    if not isinstance(payload, dict):
        raise TypeError("payload_json must contain an object")
    _validate_json_value(payload)
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        edict_id=edict_id,
        memorial_id=memorial_id,
        attempt=record.attempt_count,
        timestamp=timestamp,
        producer=producer,
        payload=cast(dict[str, Any], payload),
    )


def _required_text(value: object, field_name: str, *, allow_blank: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not allow_blank and not value.strip():
        raise ValueError(f"{field_name} must be non-blank")
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _validate_json_value(value: object) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("payload numbers must be finite")
        return
    if isinstance(value, list):
        for nested in value:
            _validate_json_value(nested)
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError("payload keys must be strings")
            _validate_json_value(nested)
        return
    raise TypeError("payload contains a non-JSON value")


def _bounded_error_hash(error: Exception) -> str:
    try:
        detail = str(error)
    except Exception:
        detail = "unprintable error"
    error_type = f"{type(error).__module__}.{type(error).__qualname__}"
    bounded = f"{error_type}:{detail[:4096]}".encode("utf-8", errors="replace")
    return hashlib.sha256(bounded).hexdigest()


__all__ = ["OutboxDispatcher"]
