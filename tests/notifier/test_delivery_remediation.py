"""Formal-review regressions for durable internal notification delivery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from tianshu.application.outbox import OutboxDispatcher
from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict, EdictDispatch, Memorial, TaskStatus
from tianshu.models.events import EventEnvelope
from tianshu.notifier.delivery_outbox import InternalDeliveryOutbox, InternalDeliveryWorker
from tianshu.notifier.notifier import Notifier
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_NOW = datetime(2026, 7, 17, 6, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _FailFirstConsumptionAck:
    def __init__(self, repository: OutboxRepository) -> None:
        self._repository = repository
        self._failed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def record_consumption(self, **kwargs: Any) -> bool:
        if not self._failed:
            self._failed = True
            raise RuntimeError("simulated crash after Notifier handoff")
        return self._repository.record_consumption(**kwargs)


class _SequenceChannel:
    def __init__(self, outcomes: list[bool | Exception]) -> None:
        self._outcomes = iter(outcomes)
        self.calls = 0

    async def send(self, _message, _rendered) -> bool:
        self.calls += 1
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Registry:
    def __init__(self, channels: dict[str, _SequenceChannel]) -> None:
        self._channels = channels

    def get(self, name: str) -> _SequenceChannel | None:
        return self._channels.get(name)


def _open(path) -> Storage:
    storage = Storage(str(path))
    storage.init_db()
    return storage


def _seed_notification(storage: Storage) -> EventEnvelope:
    edict = Edict(
        id="edict-notifier-replay",
        goal="preserve the first durable notification schedule",
        priority="urgent",
        dispatch=EdictDispatch(channels=["feishu"]),
    )
    memorial = Memorial(
        id="memorial-notifier-replay",
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.FAILED,
        error="execution failed",
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    event = EventEnvelope(
        event_id="event-notifier-replay",
        event_type="execution.failed",
        edict_id=edict.id,
        memorial_id=memorial.id,
        producer="tests",
        timestamp=_NOW,
        payload={"correlation_id": "notifier-replay-correlation"},
    )
    with storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(unit_of_work.connection, event)
        unit_of_work.commit()
    return event


def _bus(notifier: Notifier) -> EventBus:
    bus = EventBus()
    bus.on(
        "execution.failed",
        notifier.handle_execution_failed,
        consumer_name="notifier.execution_failed.v1",
    )
    return bus


async def test_notifier_replay_after_ack_loss_keeps_first_schedule_and_is_acknowledged(
    tmp_path,
) -> None:
    database = tmp_path / "notifier-replay.db"
    first_storage = _open(database)
    event = _seed_notification(first_storage)
    first_repository = OutboxRepository(first_storage.unit_of_work)
    first_internal = InternalDeliveryOutbox(first_storage.unit_of_work)
    first_notifier = Notifier(first_storage, channel_registry=None)
    first_notifier.set_delivery_outbox(first_internal)
    first_dispatcher = OutboxDispatcher(
        _FailFirstConsumptionAck(first_repository),
        _bus(first_notifier),
        owner_id="notifier-before-restart",
        clock=lambda: _NOW,
        lease_seconds=5,
    )

    with patch("tianshu.notifier.notifier.datetime") as mocked_datetime:
        mocked_datetime.now.return_value = _NOW
        with pytest.raises(RuntimeError, match="crash after Notifier handoff"):
            await first_dispatcher.drain_once()

    first_row = first_storage._conn.execute(  # noqa: SLF001 - persistence contract
        "SELECT delivery_id FROM internal_notification_deliveries WHERE event_id=?",
        (event.event_id,),
    ).fetchone()
    assert first_row is not None
    first_delivery = first_internal.get(str(first_row[0]))
    assert first_delivery is not None
    assert first_delivery.available_at == _NOW
    assert first_repository.consumed_consumers(event.event_id) == frozenset()
    first_storage.close()

    restarted_at = _NOW + timedelta(seconds=6)
    restarted_storage = _open(database)
    restarted_repository = OutboxRepository(restarted_storage.unit_of_work)
    restarted_internal = InternalDeliveryOutbox(restarted_storage.unit_of_work)
    restarted_notifier = Notifier(restarted_storage, channel_registry=None)
    restarted_notifier.set_delivery_outbox(restarted_internal)
    restarted_dispatcher = OutboxDispatcher(
        restarted_repository,
        _bus(restarted_notifier),
        owner_id="notifier-after-restart",
        clock=lambda: restarted_at,
        lease_seconds=5,
    )
    try:
        with patch("tianshu.notifier.notifier.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = restarted_at
            assert await restarted_dispatcher.drain_once() == 1

        source = restarted_repository.get(
            restarted_storage._conn,  # noqa: SLF001 - durable ack contract
            event.event_id,
        )
        replayed = restarted_internal.get(first_delivery.delivery_id)
        assert source is not None and source.status == "published"
        assert restarted_repository.consumed_consumers(event.event_id) == frozenset(
            {"notifier.execution_failed.v1"}
        )
        assert replayed is not None
        assert replayed.available_at == first_delivery.available_at
        assert replayed.deadline_at == first_delivery.deadline_at
        assert replayed.created_at == first_delivery.created_at
        assert replayed.version == first_delivery.version
    finally:
        restarted_storage.close()


async def test_success_after_lease_expiry_is_reclaimed_before_delivery(tmp_path) -> None:
    storage = _open(tmp_path / "lease-expiry.db")
    clock = _Clock(_NOW)
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    delivery = outbox.enqueue(
        event_id="event-lease-expiry",
        event_type="execution.failed",
        correlation_id="correlation-lease-expiry",
        edict_id=None,
        memorial_id=None,
        available_at=clock(),
        deadline_at=clock() + timedelta(minutes=1),
        max_attempts=3,
    )

    async def finish_after_lease(_record) -> None:
        clock.now += timedelta(seconds=6)

    expired_worker = InternalDeliveryWorker(
        outbox,
        finish_after_lease,
        owner_id="expired-owner",
        clock=clock,
        lease_seconds=5,
    )
    assert await expired_worker.drain_once() == 1
    expired_claim = outbox.get(delivery.delivery_id)
    assert expired_claim is not None
    assert expired_claim.status == "claimed"
    assert expired_claim.lease_owner == "expired-owner"

    async def finish_in_lease(_record) -> None:
        return None

    reclaimer = InternalDeliveryWorker(
        outbox,
        finish_in_lease,
        owner_id="reclaimed-owner",
        clock=clock,
        lease_seconds=5,
    )
    assert await reclaimer.drain_once() == 1
    delivered = outbox.get(delivery.delivery_id)
    assert delivered is not None
    assert delivered.status == "delivered"
    assert delivered.attempt_count == 2
    storage.close()


async def test_failure_after_lease_expiry_cannot_schedule_retry(tmp_path) -> None:
    storage = _open(tmp_path / "failed-lease-expiry.db")
    clock = _Clock(_NOW)
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    delivery = outbox.enqueue(
        event_id="event-failed-lease-expiry",
        event_type="execution.failed",
        correlation_id="correlation-failed-lease-expiry",
        edict_id=None,
        memorial_id=None,
        available_at=clock(),
        deadline_at=clock() + timedelta(minutes=1),
        max_attempts=3,
    )

    async def fail_after_lease(_record) -> None:
        clock.now += timedelta(seconds=6)
        raise RuntimeError("late failure")

    worker = InternalDeliveryWorker(
        outbox,
        fail_after_lease,
        owner_id="expired-failure-owner",
        clock=clock,
        lease_seconds=5,
        base_backoff_seconds=10,
        max_backoff_seconds=10,
    )
    assert await worker.drain_once() == 1
    retained = outbox.get(delivery.delivery_id)
    assert retained is not None
    assert retained.status == "claimed"
    assert retained.lease_owner == "expired-failure-owner"
    storage.close()


async def test_failure_backoff_starts_from_handler_completion(tmp_path) -> None:
    storage = _open(tmp_path / "fresh-backoff.db")
    clock = _Clock(_NOW)
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    delivery = outbox.enqueue(
        event_id="event-fresh-backoff",
        event_type="execution.failed",
        correlation_id="correlation-fresh-backoff",
        edict_id=None,
        memorial_id=None,
        available_at=clock(),
        deadline_at=clock() + timedelta(minutes=1),
        max_attempts=3,
    )

    async def fail_after_work(_record) -> None:
        clock.now += timedelta(seconds=7)
        raise RuntimeError("retryable")

    worker = InternalDeliveryWorker(
        outbox,
        fail_after_work,
        owner_id="fresh-backoff-owner",
        clock=clock,
        lease_seconds=30,
        base_backoff_seconds=10,
        max_backoff_seconds=10,
    )
    assert await worker.drain_once() == 1
    failed = outbox.get(delivery.delivery_id)
    assert failed is not None
    assert failed.status == "retry_wait"
    assert failed.available_at == _NOW + timedelta(seconds=17)
    storage.close()


async def test_handler_crossing_deadline_is_atomically_dead_lettered(tmp_path) -> None:
    storage = _open(tmp_path / "deadline-crossing.db")
    clock = _Clock(_NOW)
    outbox = InternalDeliveryOutbox(storage.unit_of_work)
    delivery = outbox.enqueue(
        event_id="event-deadline-crossing",
        event_type="execution.failed",
        correlation_id="correlation-deadline-crossing",
        edict_id=None,
        memorial_id=None,
        available_at=clock(),
        deadline_at=clock() + timedelta(seconds=5),
        max_attempts=3,
    )

    async def finish_after_deadline(_record) -> None:
        clock.now += timedelta(seconds=6)

    worker = InternalDeliveryWorker(
        outbox,
        finish_after_deadline,
        owner_id="deadline-crossing-owner",
        clock=clock,
        lease_seconds=30,
    )
    assert await worker.drain_once() == 1
    expired = outbox.get(delivery.delivery_id)
    assert expired is not None
    assert expired.status == "dead_letter"
    assert expired.last_error is not None
    assert expired.last_error.code == "delivery_deadline_expired"
    assert storage.list_system_audit()[-1].action == "notification.delivery.dead_lettered"
    storage.close()


async def test_legacy_pending_retries_failed_intended_channel_after_restart_with_outbox_bound(
    tmp_path,
) -> None:
    database = tmp_path / "legacy-restart.db"
    first_storage = _open(database)
    edict = Edict(
        id="edict-legacy-restart",
        goal="retry every intended legacy channel",
        dispatch=EdictDispatch(channels=["feishu", "email"]),
    )
    memorial = Memorial(
        id="memorial-legacy-restart",
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.FAILED,
        error="legacy retry",
    )
    first_storage.save_edict(edict)
    first_storage.save_memorial(memorial)
    first_storage.save_pending_notification(
        {
            "id": "pending-legacy-restart",
            "edict_id": edict.id,
            "memorial_id": memorial.id,
            "message": {"type": "execution.failed"},
            "channels": ["feishu", "email"],
            "created_at": _NOW.isoformat(),
        }
    )
    first_feishu = _SequenceChannel([True])
    first_email = _SequenceChannel([False])
    first_notifier = Notifier(
        first_storage,
        channel_registry=_Registry({"feishu": first_feishu, "email": first_email}),
    )
    assert await first_notifier.drain_legacy_pending() == 0
    assert [row["id"] for row in first_storage.list_pending_notifications()] == [
        "pending-legacy-restart"
    ]
    assert (first_feishu.calls, first_email.calls) == (1, 1)
    first_storage.close()

    restarted_storage = _open(database)
    restarted_feishu = _SequenceChannel([True])
    restarted_email = _SequenceChannel([True])
    restarted_notifier = Notifier(
        restarted_storage,
        channel_registry=_Registry({"feishu": restarted_feishu, "email": restarted_email}),
    )
    restarted_notifier.set_delivery_outbox(InternalDeliveryOutbox(restarted_storage.unit_of_work))
    assert await restarted_notifier.drain_legacy_pending() == 1
    assert restarted_storage.list_pending_notifications() == []
    assert await restarted_notifier.drain_legacy_pending() == 0
    assert (restarted_feishu.calls, restarted_email.calls) == (1, 1)
    restarted_storage.close()


async def test_legacy_pending_exception_or_missing_channel_is_retained(tmp_path) -> None:
    storage = _open(tmp_path / "legacy-channel-failures.db")
    edict = Edict(
        id="edict-legacy-channel-failures",
        goal="retain on any channel failure",
    )
    memorial = Memorial(
        id="memorial-legacy-channel-failures",
        edict_id=edict.id,
        instruction="retain on any channel failure",
        status=TaskStatus.FAILED,
        error="legacy retry",
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    for pending_id, channels in (
        ("pending-channel-exception", ["feishu"]),
        ("pending-channel-missing", ["email"]),
    ):
        storage.save_pending_notification(
            {
                "id": pending_id,
                "edict_id": memorial.edict_id,
                "memorial_id": memorial.id,
                "message": {"type": "execution.failed"},
                "channels": channels,
                "created_at": _NOW.isoformat(),
            }
        )
    notifier = Notifier(
        storage,
        channel_registry=_Registry({"feishu": _SequenceChannel([RuntimeError("provider failed")])}),
    )
    assert await notifier.drain_legacy_pending() == 0
    assert {row["id"] for row in storage.list_pending_notifications()} == {
        "pending-channel-exception",
        "pending-channel-missing",
    }
    storage.close()
