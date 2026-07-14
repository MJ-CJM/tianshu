"""Focused durable outbox dispatcher behavior."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_NOW = datetime(2026, 7, 15, 2, 3, 4, tzinfo=UTC)


def _add_event(
    storage: Storage,
    *,
    event_id: str,
    max_attempts: int = 20,
) -> None:
    repository = OutboxRepository()
    with storage.unit_of_work() as unit_of_work:
        repository.add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="test.dispatch",
                timestamp=_NOW,
                producer="tests",
                payload={"value": 1},
            ),
        )
        unit_of_work.connection.execute(
            "UPDATE outbox_events SET max_attempts = ? WHERE event_id = ?",
            (max_attempts, event_id),
        )
        unit_of_work.commit()


def _dispatcher_type():  # type: ignore[no-untyped-def]
    from tianshu.application.outbox import OutboxDispatcher

    return OutboxDispatcher


async def test_partial_success_is_durable_and_retry_skips_successful_consumer(
    storage: Storage,
) -> None:
    _add_event(storage, event_id="partial")
    repository = OutboxRepository(storage.unit_of_work)
    event_bus = EventBus()
    clock = [_NOW]
    calls = {"stable": 0, "flaky": 0}

    async def stable(_event: EventEnvelope) -> None:
        calls["stable"] += 1

    async def flaky(_event: EventEnvelope) -> None:
        calls["flaky"] += 1
        if calls["flaky"] == 1:
            raise RuntimeError("Bearer sk-live-raw-dispatch-secret")

    event_bus.on(
        "test.dispatch",
        stable,
        consumer_name="test.stable.v1",
        priority=10,
    )
    event_bus.on(
        "test.dispatch",
        flaky,
        consumer_name="test.flaky.v1",
        priority=20,
    )
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="worker",
        clock=lambda: clock[0],
        lease_seconds=30,
        base_backoff_seconds=2,
        max_backoff_seconds=8,
    )

    assert await dispatcher.drain_once() == 1

    failed = repository.get(storage._conn, "partial")  # noqa: SLF001
    assert failed is not None
    assert failed.status == "retry_wait"
    assert failed.available_at == (_NOW + timedelta(seconds=2)).isoformat()
    assert repository.consumed_consumers("partial") == frozenset({"test.stable.v1"})
    assert calls == {"stable": 1, "flaky": 1}
    assert "sk-live-raw-dispatch-secret" not in (failed.last_error_json or "")
    assert json.loads(failed.last_error_json or "") == {
        "code": "consumer_dispatch_failed",
        "details_hash": json.loads(failed.last_error_json or "")["details_hash"],
        "message": "one or more consumers failed",
        "retryable": True,
    }
    assert len(json.loads(failed.last_error_json or "")["details_hash"]) == 64

    clock[0] = _NOW + timedelta(seconds=2)
    assert await dispatcher.drain_once() == 1

    published = repository.get(storage._conn, "partial")  # noqa: SLF001
    assert published is not None
    assert published.status == "published"
    assert calls == {"stable": 1, "flaky": 2}
    assert repository.consumed_consumers("partial") == frozenset(
        {"test.stable.v1", "test.flaky.v1"}
    )


async def test_dispatcher_dead_letters_only_after_max_attempts(storage: Storage) -> None:
    _add_event(storage, event_id="poison", max_attempts=4)
    repository = OutboxRepository(storage.unit_of_work)
    event_bus = EventBus()
    clock = [_NOW]
    calls = 0

    async def always_fails(_event: EventEnvelope) -> None:
        nonlocal calls
        calls += 1
        raise ValueError("private failure detail")

    event_bus.on(
        "test.dispatch",
        always_fails,
        consumer_name="test.poison.v1",
    )
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="worker",
        clock=lambda: clock[0],
        base_backoff_seconds=1,
        max_backoff_seconds=2,
    )

    assert await dispatcher.drain_once() == 1
    first = repository.get(storage._conn, "poison")  # noqa: SLF001
    assert first is not None
    assert first.status == "retry_wait"
    assert first.available_at == (_NOW + timedelta(seconds=1)).isoformat()

    clock[0] = _NOW + timedelta(seconds=1)
    assert await dispatcher.drain_once() == 1
    second = repository.get(storage._conn, "poison")  # noqa: SLF001
    assert second is not None
    assert second.status == "retry_wait"
    assert second.available_at == (_NOW + timedelta(seconds=3)).isoformat()

    clock[0] = _NOW + timedelta(seconds=3)
    assert await dispatcher.drain_once() == 1
    third = repository.get(storage._conn, "poison")  # noqa: SLF001
    assert third is not None
    assert third.status == "retry_wait"
    assert third.available_at == (_NOW + timedelta(seconds=5)).isoformat()

    clock[0] = _NOW + timedelta(seconds=5)
    assert await dispatcher.drain_once() == 1
    fourth = repository.get(storage._conn, "poison")  # noqa: SLF001
    assert fourth is not None
    assert (fourth.status, fourth.attempt_count, calls) == ("dead_letter", 4, 4)
    assert "private failure detail" not in (fourth.last_error_json or "")


@pytest.mark.parametrize(
    ("column_name", "tampered_value"),
    [
        ("payload_json", '["raw-malformed-secret"]'),
        ("payload_json", '{"value":NaN,"secret":"raw-malformed-secret"}'),
        ("payload_json", '{"secret":"raw-malformed-secret"'),
        ("occurred_at", "2026-07-15T02:03:04"),
        ("aggregate_type", "edict"),
        ("event_type", ""),
    ],
)
async def test_malformed_durable_row_fails_closed_without_handler_delivery(
    storage: Storage,
    column_name: str,
    tampered_value: str,
) -> None:
    _add_event(storage, event_id="malformed", max_attempts=1)
    storage._conn.execute(  # noqa: SLF001 - deliberate durable-row tamper
        f"UPDATE outbox_events SET {column_name} = ? WHERE event_id = ?",
        (tampered_value, "malformed"),
    )
    storage._conn.commit()  # noqa: SLF001 - deliberate durable-row tamper
    repository = OutboxRepository(storage.unit_of_work)
    event_bus = EventBus()
    calls = 0

    async def handler(_event: EventEnvelope) -> None:
        nonlocal calls
        calls += 1

    event_bus.on("test.dispatch", handler, consumer_name="test.never.v1")
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="worker",
        clock=lambda: _NOW,
    )

    assert await dispatcher.drain_once() == 1

    record = repository.get(storage._conn, "malformed")  # noqa: SLF001
    assert record is not None
    assert record.status == "dead_letter"
    assert calls == 0
    assert "raw-malformed-secret" not in (record.last_error_json or "")
    assert json.loads(record.last_error_json or "")["code"] == "malformed_outbox_event"


async def test_dispatcher_run_stops_promptly_after_processing(storage: Storage) -> None:
    _add_event(storage, event_id="run-stop")
    repository = OutboxRepository(storage.unit_of_work)
    event_bus = EventBus()
    delivered = asyncio.Event()

    async def handler(_event: EventEnvelope) -> None:
        delivered.set()

    event_bus.on("test.dispatch", handler, consumer_name="test.run-stop.v1")
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="worker",
        clock=lambda: _NOW,
        poll_interval_seconds=60,
    )

    run_task = asyncio.create_task(dispatcher.run())
    await asyncio.wait_for(delivered.wait(), timeout=1)
    await asyncio.wait_for(dispatcher.stop(), timeout=1)
    await asyncio.wait_for(run_task, timeout=1)

    record = repository.get(storage._conn, "run-stop")  # noqa: SLF001
    assert record is not None
    assert record.status == "published"
