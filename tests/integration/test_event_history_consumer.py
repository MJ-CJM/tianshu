"""Durable history compatibility tests for EventBus envelopes."""

from datetime import UTC, datetime

from fastapi import FastAPI

from tianshu.application.event_history import EventHistoryConsumer
from tianshu.bootstrap.wiring_storage import wire_storage
from tianshu.config import TianshuSettings
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.events import EventEnvelope


async def test_history_consumer_preserves_envelope_identity_and_time_idempotently(storage):
    edict = Edict(goal="preserve durable history")
    memorial = Memorial(edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    event_time = datetime(2026, 7, 14, 3, 4, 5, 678901, tzinfo=UTC)
    event = EventEnvelope(
        event_id="event-history-exact-id",
        event_type="execution.started",
        edict_id=edict.id,
        memorial_id=memorial.id,
        timestamp=event_time,
        payload={"nested": {"value": 1}},
    )
    consumer = EventHistoryConsumer(storage)

    await consumer(event)

    first_heartbeat = storage.get_memorial(memorial.id).last_heartbeat_at
    await consumer(event)
    second_heartbeat = storage.get_memorial(memorial.id).last_heartbeat_at
    events = storage.get_events(edict.id)

    assert consumer.consumer_name == "event_history.v1"
    assert events == [
        {
            "id": "event-history-exact-id",
            "edict_id": edict.id,
            "memorial_id": memorial.id,
            "event_type": "execution.started",
            "payload": {"nested": {"value": 1}},
            "created_at": event_time.isoformat(),
        }
    ]
    assert first_heartbeat is not None
    assert second_heartbeat == first_heartbeat


async def test_history_consumer_ignores_event_without_edict_id(storage):
    consumer = EventHistoryConsumer(storage)

    await consumer(EventEnvelope(event_id="system-event", event_type="system.ready"))

    assert storage.get_recent_events() == []


async def test_storage_wiring_registers_explicit_history_consumer(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    settings = TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "tianshu.sqlite3"),
        workspace_dir=str(source),
        workspace_staging_root=str(tmp_path / "staging"),
    )
    app = FastAPI()
    wire_storage(app, settings)
    edict = Edict(goal="wired history")
    app.state.storage.save_edict(edict)
    event = EventEnvelope(
        event_id="wired-event-id",
        event_type="edict.submitted",
        edict_id=edict.id,
        timestamp=datetime(2026, 7, 14, 5, 6, 7, tzinfo=UTC),
    )

    try:
        report = await app.state.event_bus.dispatch(event)
        await app.state.event_bus.emit(event)
        events = app.state.storage.get_events(edict.id)
    finally:
        app.state.storage.close()

    assert [row["id"] for row in events] == ["wired-event-id"]
    assert [(result.consumer_name, result.succeeded) for result in report.results] == [
        ("event_history.v1", True)
    ]
