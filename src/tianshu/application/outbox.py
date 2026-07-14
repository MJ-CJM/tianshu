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


class OutboxShutdownTimeout(TimeoutError):
    """The dispatcher still owns a live drain after its shutdown deadline."""


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

    def mark_poisoned(
        self,
        *,
        event_id: str,
        owner_id: str,
        expected_version: object,
        error: RedactedError,
        now: datetime,
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
        shutdown_timeout_seconds: float = 5,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-blank")
        validated_lease_seconds = _exact_positive_integer(lease_seconds, "lease_seconds")
        validated_base_backoff = _finite_positive_number(
            base_backoff_seconds,
            "base_backoff_seconds",
        )
        validated_max_backoff = _finite_positive_number(
            max_backoff_seconds,
            "max_backoff_seconds",
        )
        validated_poll_interval = _finite_positive_number(
            poll_interval_seconds,
            "poll_interval_seconds",
        )
        validated_shutdown_timeout = _finite_positive_number(
            shutdown_timeout_seconds,
            "shutdown_timeout_seconds",
        )
        if validated_max_backoff < validated_base_backoff:
            raise ValueError("max_backoff_seconds must be at least base_backoff_seconds")
        self._repository = repository
        self._event_bus = event_bus
        self._owner_id = owner_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_seconds = validated_lease_seconds
        self._base_backoff_seconds = validated_base_backoff
        self._max_backoff_seconds = validated_max_backoff
        self._poll_interval_seconds = validated_poll_interval
        self._shutdown_timeout_seconds = validated_shutdown_timeout
        self._stop_event = asyncio.Event()
        self._stopped_event = asyncio.Event()
        self._stopped_event.set()
        self._running = False
        self._drain_task: asyncio.Task[int] | None = None

    @property
    def is_stopped(self) -> bool:
        """Whether no dispatcher run is still waiting for an active drain."""
        return self._stopped_event.is_set()

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
        self._stopped_event.clear()
        run_task = asyncio.current_task()
        try:
            while not self._stop_event.is_set():
                drain_task = asyncio.create_task(self.drain_once())
                self._drain_task = drain_task
                try:
                    await drain_task
                except asyncio.CancelledError:
                    if run_task is not None and run_task.cancelling():
                        raise
                    if not self._stop_event.is_set():
                        raise
                finally:
                    if self._drain_task is drain_task:
                        self._drain_task = None
                if self._stop_event.is_set():
                    break
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval_seconds,
                    )
        finally:
            final_drain_task = self._drain_task
            if final_drain_task is not None and not final_drain_task.done():
                final_drain_task.cancel()
                with suppress(asyncio.CancelledError):
                    await final_drain_task
            self._drain_task = None
            self._running = False
            self._stopped_event.set()

    async def stop(self) -> None:
        self._stop_event.set()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._shutdown_timeout_seconds
        drain_task = self._drain_task
        if drain_task is not None and not drain_task.done():
            drain_task.cancel()
            done, _ = await asyncio.wait(
                {drain_task},
                timeout=max(deadline - loop.time(), 0),
            )
            if drain_task not in done:
                raise OutboxShutdownTimeout("outbox drain did not stop before the shutdown timeout")
            with suppress(asyncio.CancelledError):
                drain_task.result()
        if self._running and not self._stopped_event.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise OutboxShutdownTimeout(
                    "outbox dispatcher did not stop before the shutdown timeout"
                )
            try:
                await asyncio.wait_for(self._stopped_event.wait(), timeout=remaining)
            except TimeoutError as error:
                raise OutboxShutdownTimeout(
                    "outbox dispatcher did not stop before the shutdown timeout"
                ) from error

    async def _dispatch_record(self, record: OutboxRecord) -> None:
        try:
            _validate_dispatch_control(record)
        except (TypeError, ValueError) as error:
            self._repository.mark_poisoned(
                event_id=record.event_id,
                owner_id=self._owner_id,
                expected_version=record.version,
                error=RedactedError(
                    code="malformed_outbox_event",
                    message="durable dispatch control is invalid",
                    retryable=False,
                    details_hash=_bounded_error_hash(error),
                ),
                now=self._clock(),
            )
            return
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


def _validate_dispatch_control(record: OutboxRecord) -> None:
    attempt_count = _exact_positive_integer(record.attempt_count, "attempt_count")
    max_attempts = _exact_positive_integer(record.max_attempts, "max_attempts")
    _exact_positive_integer(record.version, "version")
    if attempt_count > max_attempts:
        raise ValueError("attempt_count must not exceed max_attempts")


def _exact_positive_integer(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field_name} must be an integer")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _finite_positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name} must be finite and positive")
    return normalized


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


__all__ = ["OutboxDispatcher", "OutboxShutdownTimeout"]
