"""Focused durable outbox dispatcher behavior."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRecord, OutboxRepository

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


class _TamperClaimedRecord:
    def __init__(
        self,
        storage: Storage,
        repository: OutboxRepository,
        *,
        column_name: str,
        tampered_value: object,
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._column_name = column_name
        self._tampered_value = tampered_value

    def claim_batch(
        self,
        *,
        owner_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> list[OutboxRecord]:
        records = self._repository.claim_batch(
            owner_id=owner_id,
            now=now,
            limit=limit,
            lease_seconds=lease_seconds,
        )
        if not records:
            return []
        event_id = records[0].event_id
        self._storage._conn.execute(  # noqa: SLF001 - deliberate post-claim tamper
            f"UPDATE outbox_events SET {self._column_name} = ? WHERE event_id = ?",
            (self._tampered_value, event_id),
        )
        self._storage._conn.commit()  # noqa: SLF001 - deliberate post-claim tamper
        tampered = self._repository.get(self._storage._conn, event_id)  # noqa: SLF001
        assert tampered is not None
        return [tampered]

    def __getattr__(self, name: str) -> object:
        return getattr(self._repository, name)


@pytest.mark.parametrize(
    ("kwargs", "field_name"),
    [
        ({"lease_seconds": True}, "lease_seconds"),
        ({"lease_seconds": 1.5}, "lease_seconds"),
        ({"base_backoff_seconds": True}, "base_backoff_seconds"),
        ({"base_backoff_seconds": float("nan")}, "base_backoff_seconds"),
        (
            {
                "base_backoff_seconds": float("inf"),
                "max_backoff_seconds": float("inf"),
            },
            "base_backoff_seconds",
        ),
        ({"max_backoff_seconds": True}, "max_backoff_seconds"),
        ({"max_backoff_seconds": float("nan")}, "max_backoff_seconds"),
        ({"max_backoff_seconds": float("inf")}, "max_backoff_seconds"),
        ({"poll_interval_seconds": True}, "poll_interval_seconds"),
        ({"poll_interval_seconds": float("nan")}, "poll_interval_seconds"),
        ({"poll_interval_seconds": float("inf")}, "poll_interval_seconds"),
    ],
)
def test_dispatcher_rejects_invalid_timing_configuration(
    kwargs: dict[str, object],
    field_name: str,
) -> None:
    with pytest.raises(ValueError, match=field_name):
        _dispatcher_type()(
            OutboxRepository(),
            EventBus(),
            owner_id="worker",
            **kwargs,
        )


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


@pytest.mark.parametrize(
    ("column_name", "tampered_value"),
    [
        ("max_attempts", "invalid-max"),
        ("max_attempts", 2.5),
        ("version", "invalid-version"),
        ("version", 2.5),
    ],
)
async def test_malformed_dispatch_control_is_forced_dead_without_handler_delivery(
    storage: Storage,
    column_name: str,
    tampered_value: object,
) -> None:
    _add_event(storage, event_id="malformed-control", max_attempts=20)
    repository = OutboxRepository(storage.unit_of_work)
    tampering_repository = _TamperClaimedRecord(
        storage,
        repository,
        column_name=column_name,
        tampered_value=tampered_value,
    )
    event_bus = EventBus()
    calls = 0

    async def handler(_event: EventEnvelope) -> None:
        nonlocal calls
        calls += 1

    event_bus.on("test.dispatch", handler, consumer_name="test.never-control.v1")
    dispatcher = _dispatcher_type()(
        tampering_repository,
        event_bus,
        owner_id="worker",
        clock=lambda: _NOW,
    )

    assert await dispatcher.drain_once() == 1

    record = repository.get(storage._conn, "malformed-control")  # noqa: SLF001
    assert record is not None
    assert record.status == "dead_letter"
    assert record.lease_owner is None
    assert record.lease_expires_at is None
    assert calls == 0
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


async def test_stop_cancels_hanging_dispatch_and_claim_can_be_recovered(
    storage: Storage,
) -> None:
    _add_event(storage, event_id="hanging-stop")
    repository = OutboxRepository(storage.unit_of_work)
    event_bus = EventBus()
    clock = [_NOW]
    entered = asyncio.Event()
    never_finishes = asyncio.Event()
    calls = 0

    async def handler(_event: EventEnvelope) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await never_finishes.wait()

    event_bus.on("test.dispatch", handler, consumer_name="test.hanging-stop.v1")
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="worker-one",
        clock=lambda: clock[0],
        lease_seconds=2,
        poll_interval_seconds=60,
    )

    run_task = asyncio.create_task(dispatcher.run())
    await asyncio.wait_for(entered.wait(), timeout=1)
    await asyncio.wait_for(dispatcher.stop(), timeout=1)
    await asyncio.wait_for(run_task, timeout=1)
    await asyncio.wait_for(dispatcher.stop(), timeout=1)

    claimed = repository.get(storage._conn, "hanging-stop")  # noqa: SLF001
    assert claimed is not None
    assert (claimed.status, claimed.lease_owner, claimed.attempt_count) == (
        "claimed",
        "worker-one",
        1,
    )

    clock[0] = _NOW + timedelta(seconds=3)
    recovering_dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="worker-two",
        clock=lambda: clock[0],
        lease_seconds=2,
    )
    assert await recovering_dispatcher.drain_once() == 1

    recovered = repository.get(storage._conn, "hanging-stop")  # noqa: SLF001
    assert recovered is not None
    assert (recovered.status, recovered.attempt_count, calls) == ("published", 2, 2)


async def test_immediate_stop_request_is_preserved_before_run_starts(
    storage: Storage,
) -> None:
    _add_event(storage, event_id="pre-start-stop")
    repository = OutboxRepository(storage.unit_of_work)
    event_bus = EventBus()
    calls = 0

    async def handler(_event: EventEnvelope) -> None:
        nonlocal calls
        calls += 1

    event_bus.on("test.dispatch", handler, consumer_name="test.pre-start-stop.v1")
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="worker",
        clock=lambda: _NOW,
    )

    run_task = asyncio.create_task(dispatcher.run())
    await asyncio.wait_for(dispatcher.stop(), timeout=1)
    await asyncio.wait_for(run_task, timeout=1)
    await asyncio.wait_for(dispatcher.stop(), timeout=1)

    record = repository.get(storage._conn, "pre-start-stop")  # noqa: SLF001
    assert record is not None
    assert (record.status, record.attempt_count, calls) == ("pending", 0, 0)


async def test_external_run_cancellation_propagates_and_keeps_claim_recoverable(
    storage: Storage,
) -> None:
    _add_event(storage, event_id="external-cancel")
    repository = OutboxRepository(storage.unit_of_work)
    event_bus = EventBus()
    entered = asyncio.Event()
    never_finishes = asyncio.Event()

    async def handler(_event: EventEnvelope) -> None:
        entered.set()
        await never_finishes.wait()

    event_bus.on("test.dispatch", handler, consumer_name="test.external-cancel.v1")
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="worker",
        clock=lambda: _NOW,
        lease_seconds=2,
    )

    run_task = asyncio.create_task(dispatcher.run())
    await asyncio.wait_for(entered.wait(), timeout=1)
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    claimed = repository.get(storage._conn, "external-cancel")  # noqa: SLF001
    assert claimed is not None
    assert (claimed.status, claimed.lease_owner, claimed.attempt_count) == (
        "claimed",
        "worker",
        1,
    )
