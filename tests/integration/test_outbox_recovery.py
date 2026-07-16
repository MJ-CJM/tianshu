"""Restart recovery for committed and leased durable outbox events."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tianshu.application.edicts import EdictApplicationService, SubmitEdictCommand
from tianshu.bus.event_bus import EventBus
from tianshu.models import Edict
from tianshu.models.events import EventEnvelope
from tianshu.models.governance_contract import ObjectiveV1, RequestedGovernanceContractV1
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_NOW = datetime(2026, 7, 15, 3, 4, 5, tzinfo=UTC)


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="recovery-principal",
            kind=PrincipalKind.HUMAN,
            display_name="Recovery Principal",
            scopes=frozenset({"edicts:write"}),
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.API,
        correlation_id="correlation-recovery",
    )


def _dispatcher_type():  # type: ignore[no-untyped-def]
    from tianshu.application.outbox import OutboxDispatcher

    return OutboxDispatcher


async def test_committed_submission_is_dispatched_after_process_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "commit-before-dispatch.sqlite3"
    first_storage = Storage(str(database_path))
    first_storage.init_db()
    result = EdictApplicationService(first_storage).submit(
        SubmitEdictCommand(
            edict=Edict(goal="survive commit before dispatch"),
            idempotency_key="restart-key",
            requested_contract=RequestedGovernanceContractV1(
                objective=ObjectiveV1(goal="survive commit before dispatch")
            ),
            extra_payload={"source": "recovery-test"},
        ),
        auth=_auth(),
        producer="tests.recovery",
        correlation_id="correlation-recovery",
    )
    first_storage.close()

    second_storage = Storage(str(database_path))
    second_storage.init_db()
    repository = OutboxRepository(second_storage.unit_of_work)
    pending = repository.get(second_storage._conn, result.event_id)  # noqa: SLF001
    assert pending is not None
    dispatch_at = datetime.fromisoformat(pending.available_at)
    event_bus = EventBus()
    delivered: list[EventEnvelope] = []

    async def handler(event: EventEnvelope) -> None:
        delivered.append(event)

    event_bus.on("edict.submitted", handler, consumer_name="test.recovery.v1")
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="restarted-worker",
        clock=lambda: dispatch_at,
    )
    try:
        assert await dispatcher.drain_once() == 1
        record = repository.get(second_storage._conn, result.event_id)  # noqa: SLF001
        assert record is not None
        assert record.status == "published"
        assert [(event.event_id, event.edict_id, event.memorial_id) for event in delivered] == [
            (result.event_id, result.edict.id, result.memorial.id)
        ]
    finally:
        second_storage.close()


async def test_expired_lease_is_reclaimed_after_process_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "expired-lease.sqlite3"
    first_storage = Storage(str(database_path))
    first_storage.init_db()
    with first_storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id="expired-event",
                event_type="test.expired",
                timestamp=_NOW,
                payload={"value": 1},
            ),
        )
        unit_of_work.commit()
    first_repository = OutboxRepository(first_storage.unit_of_work)
    first_claim = first_repository.claim_batch(
        owner_id="crashed-worker",
        now=_NOW,
        limit=1,
        lease_seconds=5,
    )[0]
    first_storage.close()

    second_storage = Storage(str(database_path))
    second_storage.init_db()
    repository = OutboxRepository(second_storage.unit_of_work)
    event_bus = EventBus()
    delivered: list[str] = []

    async def handler(event: EventEnvelope) -> None:
        delivered.append(event.event_id)

    event_bus.on("test.expired", handler, consumer_name="test.expired.v1")
    dispatcher = _dispatcher_type()(
        repository,
        event_bus,
        owner_id="restarted-worker",
        clock=lambda: _NOW + timedelta(seconds=6),
    )
    try:
        assert await dispatcher.drain_once() == 1
        record = repository.get(second_storage._conn, "expired-event")  # noqa: SLF001
        assert record is not None
        assert (record.status, record.attempt_count, delivered) == (
            "published",
            2,
            ["expired-event"],
        )
        assert (
            repository.mark_published(
                event_id="expired-event",
                owner_id="crashed-worker",
                expected_version=first_claim.version,
                now=_NOW + timedelta(seconds=7),
            )
            is False
        )
    finally:
        second_storage.close()


def test_two_connections_claim_once_and_reclaim_fences_original_owner(tmp_path: Path) -> None:
    database_path = tmp_path / "two-connection-claim.sqlite3"
    first_storage = Storage(str(database_path))
    first_storage.init_db()
    second_storage = Storage(str(database_path))
    second_storage.init_db()
    with first_storage.unit_of_work() as unit_of_work:
        OutboxRepository().add(
            unit_of_work.connection,
            EventEnvelope(
                event_id="concurrent-event",
                event_type="test.concurrent",
                timestamp=_NOW,
            ),
        )
        unit_of_work.commit()
    first_repository = OutboxRepository(first_storage.unit_of_work)
    second_repository = OutboxRepository(second_storage.unit_of_work)
    barrier = threading.Barrier(2)

    def claim(repository: OutboxRepository, owner_id: str):  # type: ignore[no-untyped-def]
        barrier.wait(timeout=5)
        return repository.claim_batch(
            owner_id=owner_id,
            now=_NOW,
            limit=1,
            lease_seconds=5,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(claim, first_repository, "worker-a")
            second_future = executor.submit(claim, second_repository, "worker-b")
            first_result = first_future.result(timeout=5)
            second_result = second_future.result(timeout=5)

        claimed = [*first_result, *second_result]
        assert len(claimed) == 1
        original = claimed[0]
        original_repository = (
            first_repository if original.lease_owner == "worker-a" else second_repository
        )
        reclaim_repository = (
            second_repository if original.lease_owner == "worker-a" else first_repository
        )
        reclaimed = reclaim_repository.claim_batch(
            owner_id="reclaimer",
            now=_NOW + timedelta(seconds=6),
            limit=1,
            lease_seconds=5,
        )[0]

        assert (reclaimed.attempt_count, reclaimed.version) == (2, original.version + 1)
        assert (
            original_repository.mark_published(
                event_id=original.event_id,
                owner_id=original.lease_owner or "",
                expected_version=original.version,
                now=_NOW + timedelta(seconds=6),
            )
            is False
        )
        assert (
            reclaim_repository.mark_published(
                event_id=reclaimed.event_id,
                owner_id="reclaimer",
                expected_version=reclaimed.version,
                now=_NOW + timedelta(seconds=6),
            )
            is True
        )
    finally:
        second_storage.close()
        first_storage.close()
