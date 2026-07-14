"""One effective consumer transition across handler-effect/ack failure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tianshu.application.event_history import EventHistoryConsumer
from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_NOW = datetime(2026, 7, 15, 4, 5, 6, tzinfo=UTC)


class _FailFirstConsumptionAck:
    def __init__(self, repository: OutboxRepository) -> None:
        self._repository = repository
        self._failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def record_consumption(self, **kwargs: Any) -> bool:
        if not self._failed:
            self._failed = True
            raise RuntimeError("simulated crash after handler effect")
        return self._repository.record_consumption(**kwargs)


def _dispatcher_type():  # type: ignore[no-untyped-def]
    from tianshu.application.outbox import OutboxDispatcher

    return OutboxDispatcher


async def test_event_history_effect_is_idempotent_when_consumption_ack_is_lost(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "effect-before-ack.sqlite3"
    first_storage = Storage(str(database_path))
    first_storage.init_db()
    edict = Edict(goal="one effective history transition")
    first_storage.save_edict(edict)
    with first_storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id="history-idempotency-event",
                event_type="test.history",
                edict_id=edict.id,
                timestamp=_NOW,
                payload={"value": 1},
            ),
        )
        unit_of_work.commit()
    first_repository = OutboxRepository(first_storage.unit_of_work)
    first_bus = EventBus()
    first_history = EventHistoryConsumer(first_storage)
    first_bus.on(
        "*",
        first_history,
        consumer_name=first_history.consumer_name,
        priority=0,
    )
    crashing_dispatcher = _dispatcher_type()(
        _FailFirstConsumptionAck(first_repository),
        first_bus,
        owner_id="first-worker",
        clock=lambda: _NOW,
        lease_seconds=5,
    )

    with pytest.raises(RuntimeError, match="simulated crash"):
        await crashing_dispatcher.drain_once()
    assert [event["id"] for event in first_storage.get_events(edict.id)] == [
        "history-idempotency-event"
    ]
    first_storage.close()

    second_storage = Storage(str(database_path))
    second_storage.init_db()
    repository = OutboxRepository(second_storage.unit_of_work)
    second_bus = EventBus()
    second_history = EventHistoryConsumer(second_storage)
    second_bus.on(
        "*",
        second_history,
        consumer_name=second_history.consumer_name,
        priority=0,
    )
    restarted_dispatcher = _dispatcher_type()(
        repository,
        second_bus,
        owner_id="second-worker",
        clock=lambda: _NOW + timedelta(seconds=6),
        lease_seconds=5,
    )
    try:
        assert await restarted_dispatcher.drain_once() == 1
        assert [event["id"] for event in second_storage.get_events(edict.id)] == [
            "history-idempotency-event"
        ]
        record = repository.get(
            second_storage._conn,  # noqa: SLF001
            "history-idempotency-event",
        )
        assert record is not None
        assert (record.status, record.attempt_count) == ("published", 2)
        assert repository.consumed_consumers(record.event_id) == frozenset({"event_history.v1"})
    finally:
        second_storage.close()
