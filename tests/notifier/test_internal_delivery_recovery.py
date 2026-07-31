"""Durable internal notification delivery recovery and deadline contracts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.models import Edict, EdictDispatch, Memorial, TaskStatus, make_event
from tianshu.notifier.delivery_outbox import (
    InternalDeliveryOutbox,
    InternalDeliveryWorker,
)
from tianshu.notifier.notifier import Notifier
from tianshu.storage import Storage


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _storage(path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    return storage


async def test_retry_backoff_and_delivery_survive_process_restart(tmp_path) -> None:
    db_path = tmp_path / "delivery.db"
    clock = _Clock(datetime(2026, 7, 17, 1, 0, tzinfo=UTC))
    storage = _storage(db_path)
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    delivery = outbox.enqueue(
        event_id="event-restart",
        event_type="execution.failed",
        correlation_id="correlation-restart",
        edict_id=None,
        memorial_id=None,
        available_at=clock(),
        deadline_at=clock() + timedelta(minutes=10),
        max_attempts=3,
    )
    calls: list[str] = []

    async def fail_once(record) -> None:
        calls.append(record.delivery_id)
        raise RuntimeError("provider token must never be persisted")

    first_worker = InternalDeliveryWorker(
        outbox,
        fail_once,
        owner_id="worker-before-restart",
        clock=clock,
        lease_seconds=30,
        base_backoff_seconds=10,
        max_backoff_seconds=10,
    )
    assert await first_worker.drain_once() == 1
    failed = outbox.get(delivery.delivery_id)
    assert failed is not None
    assert failed.status == "retry_wait"
    assert failed.attempt_count == 1
    assert failed.available_at == clock() + timedelta(seconds=10)
    assert failed.last_error is not None
    assert "provider token" not in failed.last_error.message
    storage.close()

    clock.now += timedelta(seconds=10)
    restarted_storage = _storage(db_path)
    restarted_outbox = InternalDeliveryOutbox(restarted_storage.unit_of_work)

    async def succeed(record) -> None:
        calls.append(record.delivery_id)

    restarted_worker = InternalDeliveryWorker(
        restarted_outbox,
        succeed,
        owner_id="worker-after-restart",
        clock=clock,
        lease_seconds=30,
        base_backoff_seconds=10,
        max_backoff_seconds=10,
    )
    assert await restarted_worker.drain_once() == 1
    delivered = restarted_outbox.get(delivery.delivery_id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.attempt_count == 2
    assert calls == [delivery.delivery_id, delivery.delivery_id]
    restarted_storage.close()


async def test_deadline_expiry_moves_delivery_to_dlq_without_calling_handler(tmp_path) -> None:
    storage = _storage(tmp_path / "deadline.db")
    clock = _Clock(datetime(2026, 7, 17, 2, 0, tzinfo=UTC))
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    delivery = outbox.enqueue(
        event_id="event-deadline",
        event_type="audit.completed",
        correlation_id="correlation-deadline",
        edict_id=None,
        memorial_id=None,
        available_at=clock(),
        deadline_at=clock() + timedelta(seconds=5),
        max_attempts=3,
    )
    clock.now += timedelta(seconds=6)
    called = False

    async def handler(_record) -> None:
        nonlocal called
        called = True

    worker = InternalDeliveryWorker(
        outbox,
        handler,
        owner_id="deadline-worker",
        clock=clock,
    )
    assert await worker.drain_once() == 0
    expired = outbox.get(delivery.delivery_id)
    assert expired is not None
    assert expired.status == "dead_letter"
    assert expired.last_error is not None
    assert expired.last_error.code == "delivery_deadline_expired"
    assert called is False
    audit = storage.list_system_audit()
    assert audit[-1].action == "notification.delivery.dead_lettered"
    assert audit[-1].correlation_id == "correlation-deadline"
    storage.close()


async def test_final_failure_is_retained_in_dlq_and_never_deletes_legacy_pending(tmp_path) -> None:
    storage = _storage(tmp_path / "retained.db")
    clock = _Clock(datetime(2026, 7, 17, 3, 0, tzinfo=UTC))
    storage.save_pending_notification(
        {
            "id": "legacy-pending",
            "edict_id": None,
            "memorial_id": None,
            "message": {"type": "legacy"},
            "channels": ["feishu"],
            "created_at": clock().isoformat(),
        }
    )
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    delivery = outbox.enqueue(
        event_id="event-final-failure",
        event_type="execution.failed",
        correlation_id="correlation-final-failure",
        edict_id=None,
        memorial_id=None,
        available_at=clock(),
        deadline_at=clock() + timedelta(minutes=10),
        max_attempts=1,
    )

    async def fail(_record) -> None:
        raise OSError("secret webhook URL")

    worker = InternalDeliveryWorker(
        outbox,
        fail,
        owner_id="failing-worker",
        clock=clock,
    )
    assert await worker.drain_once() == 1
    retained = outbox.get(delivery.delivery_id)
    assert retained is not None
    assert retained.status == "dead_letter"
    assert retained.attempt_count == 1
    assert storage.list_pending_notifications()[0]["id"] == "legacy-pending"
    assert storage.list_system_audit()[-1].action == "notification.delivery.dead_lettered"
    storage.close()


async def test_worker_readiness_requires_a_completed_storage_probe(tmp_path) -> None:
    storage = _storage(tmp_path / "worker.db")
    outbox = InternalDeliveryOutbox(storage.unit_of_work)

    async def handler(_record) -> None:
        return None

    worker = InternalDeliveryWorker(
        outbox,
        handler,
        owner_id="lifecycle-worker",
        poll_interval_seconds=0.01,
    )
    assert worker.is_ready is False
    await worker.start()
    assert worker.is_ready is True
    await worker.stop()
    assert worker.is_ready is False
    await worker.stop()
    storage.close()


def test_enqueue_is_idempotent_and_binds_correlation_identity(tmp_path) -> None:
    storage = _storage(tmp_path / "identity.db")
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    now = datetime(2026, 7, 17, 4, 0, tzinfo=UTC)
    first = outbox.enqueue(
        event_id="event-idempotent",
        event_type="audit.completed",
        correlation_id="correlation-idempotent",
        edict_id=None,
        memorial_id=None,
        available_at=now,
        deadline_at=now + timedelta(hours=1),
        max_attempts=2,
    )
    second = outbox.enqueue(
        event_id="event-idempotent",
        event_type="audit.completed",
        correlation_id="correlation-idempotent",
        edict_id=None,
        memorial_id=None,
        available_at=now,
        deadline_at=now + timedelta(hours=1),
        max_attempts=2,
    )
    assert second == first
    with pytest.raises(ValueError, match="identity conflict"):
        outbox.enqueue(
            event_id="event-idempotent",
            event_type="execution.failed",
            correlation_id="different-correlation",
            edict_id=None,
            memorial_id=None,
            available_at=now,
            deadline_at=now + timedelta(hours=1),
            max_attempts=2,
        )
    with pytest.raises(ValueError, match="opaque identifier"):
        outbox.enqueue(
            event_id="event-invalid-correlation",
            event_type="audit.completed",
            correlation_id="invalid correlation",
            edict_id=None,
            memorial_id=None,
            available_at=now,
            deadline_at=now + timedelta(hours=1),
            max_attempts=2,
        )
    storage.close()


def test_future_availability_does_not_rewrite_record_creation_time(tmp_path) -> None:
    storage = _storage(tmp_path / "future-availability.db")
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    available_at = datetime.now(UTC) + timedelta(hours=1)

    delivery = outbox.enqueue(
        event_id="event-future-availability",
        event_type="audit.completed",
        correlation_id="correlation-future-availability",
        edict_id=None,
        memorial_id=None,
        available_at=available_at,
        deadline_at=available_at + timedelta(hours=1),
        max_attempts=2,
    )

    assert delivery.created_at < delivery.available_at
    storage.close()


async def test_missing_channel_registry_is_not_marked_delivered(tmp_path) -> None:
    storage = _storage(tmp_path / "event-handoff.db")
    edict = Edict(
        id="edict-notification-handoff",
        goal="retain the internal delivery handoff",
        dispatch=EdictDispatch(channels=["feishu"]),
    )
    memorial = Memorial(
        id="memorial-notification-handoff",
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.FAILED,
        error="execution failed",
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    notifier = Notifier(storage=storage, channel_registry=None)
    notifier.set_delivery_outbox(outbox)
    event = make_event(
        "execution.failed",
        edict_id=edict.id,
        memorial_id=memorial.id,
        payload={"correlation_id": "notification-ingress-correlation"},
    )

    await notifier.handle_execution_failed(event)

    delivery_id = hashlib.sha256(f"internal-notification:{event.event_id}".encode()).hexdigest()
    retained = outbox.get(delivery_id)
    assert retained is not None
    assert retained.status == "pending"
    assert retained.correlation_id == "notification-ingress-correlation"
    worker = InternalDeliveryWorker(
        outbox,
        notifier.deliver_internal,
        owner_id="no-channel-registry-worker",
        clock=lambda: retained.available_at,
    )
    assert await worker.drain_once() == 1
    retrying = outbox.get(delivery_id)
    assert retrying is not None
    assert retrying.status == "retry_wait"
    assert retrying.last_error is not None
    storage.close()


async def test_low_priority_notification_uses_durable_outbox_and_is_delivered(tmp_path) -> None:
    storage = _storage(tmp_path / "low-priority.db")
    edict = Edict(
        id="edict-low-priority",
        goal="deliver low priority work without a fake digest",
        priority="low",
        dispatch=EdictDispatch(channels=["feishu"]),
    )
    memorial = Memorial(
        id="memorial-low-priority",
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.COMPLETED,
        result="done",
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)

    class _Channel:
        def __init__(self) -> None:
            self.calls = 0

        async def send(self, _message, _rendered) -> bool:
            self.calls += 1
            return True

    class _Registry:
        def __init__(self, channel: _Channel) -> None:
            self._channel = channel

        def get(self, _name: str) -> _Channel:
            return self._channel

    channel = _Channel()
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    notifier = Notifier(storage=storage, channel_registry=_Registry(channel))
    notifier.set_delivery_outbox(outbox)
    event = make_event(
        "audit.completed",
        edict_id=edict.id,
        memorial_id=memorial.id,
        payload={"correlation_id": "low-priority-correlation"},
    )

    await notifier.handle_audit_completed(event)

    delivery_id = hashlib.sha256(f"internal-notification:{event.event_id}".encode()).hexdigest()
    retained = outbox.get(delivery_id)
    assert retained is not None
    assert retained.status == "pending"
    worker = InternalDeliveryWorker(
        outbox,
        notifier.deliver_internal,
        owner_id="low-priority-worker",
        clock=lambda: retained.available_at,
    )
    assert await worker.drain_once() == 1
    delivered = outbox.get(delivery_id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert channel.calls == 1
    storage.close()
