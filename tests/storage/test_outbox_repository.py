"""Leased durable outbox repository contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tianshu.models.canonical import RedactedError
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_NOW = datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC)


def _repository(storage: Storage) -> OutboxRepository:
    return OutboxRepository(storage.unit_of_work)


def _add_event(
    storage: Storage,
    *,
    event_id: str,
    occurred_at: datetime = _NOW,
    max_attempts: int = 20,
) -> None:
    repository = OutboxRepository()
    with storage.unit_of_work() as unit_of_work:
        repository.add(
            unit_of_work.connection,
            EventEnvelope(
                event_id=event_id,
                event_type="test.outbox",
                timestamp=occurred_at,
                payload={"value": 1},
            ),
        )
        unit_of_work.connection.execute(
            "UPDATE outbox_events SET max_attempts = ? WHERE event_id = ?",
            (max_attempts, event_id),
        )
        unit_of_work.commit()


def test_claim_batch_claims_due_rows_once_and_increments_attempt_and_version(
    storage: Storage,
) -> None:
    _add_event(storage, event_id="due-first", occurred_at=_NOW - timedelta(seconds=2))
    _add_event(storage, event_id="due-second", occurred_at=_NOW - timedelta(seconds=1))
    _add_event(storage, event_id="not-due", occurred_at=_NOW + timedelta(seconds=1))
    repository = _repository(storage)

    claimed = repository.claim_batch(
        owner_id="worker-a",
        now=_NOW,
        limit=10,
        lease_seconds=30,
    )

    assert [record.event_id for record in claimed] == ["due-first", "due-second"]
    assert [(record.attempt_count, record.version) for record in claimed] == [(1, 2), (1, 2)]
    assert all(record.status == "claimed" for record in claimed)
    assert all(record.lease_owner == "worker-a" for record in claimed)
    assert all(
        record.lease_expires_at == (_NOW + timedelta(seconds=30)).isoformat() for record in claimed
    )
    assert (
        repository.claim_batch(
            owner_id="worker-b",
            now=_NOW,
            limit=10,
            lease_seconds=30,
        )
        == []
    )


def test_expired_claim_is_reclaimed_and_stale_owner_version_is_fenced(storage: Storage) -> None:
    _add_event(storage, event_id="reclaim-me")
    repository = _repository(storage)
    first = repository.claim_batch(
        owner_id="worker-a",
        now=_NOW,
        limit=1,
        lease_seconds=5,
    )[0]

    second = repository.claim_batch(
        owner_id="worker-b",
        now=_NOW + timedelta(seconds=6),
        limit=1,
        lease_seconds=5,
    )[0]

    assert (second.attempt_count, second.version, second.lease_owner) == (2, 3, "worker-b")
    assert (
        repository.mark_published(
            event_id=first.event_id,
            owner_id="worker-a",
            expected_version=first.version,
            now=_NOW + timedelta(seconds=6),
        )
        is False
    )
    assert (
        repository.mark_failed(
            event_id=first.event_id,
            owner_id="worker-a",
            expected_version=first.version,
            error=RedactedError(
                code="stale",
                message="redacted",
                retryable=True,
                details_hash=None,
            ),
            available_at=_NOW + timedelta(seconds=7),
        )
        is False
    )
    assert (
        repository.mark_published(
            event_id=second.event_id,
            owner_id="worker-b",
            expected_version=second.version,
            now=_NOW + timedelta(seconds=6),
        )
        is True
    )
    published = repository.get(storage._conn, second.event_id)  # noqa: SLF001
    assert published is not None
    assert published.status == "published"
    assert published.published_at == (_NOW + timedelta(seconds=6)).isoformat()
    assert published.lease_owner is None
    assert published.version == 4


def test_mark_failed_retries_nonretryable_error_until_max_attempts(storage: Storage) -> None:
    _add_event(storage, event_id="poison", max_attempts=2)
    repository = _repository(storage)
    error = RedactedError(
        code="consumer_dispatch_failed",
        message="one or more consumers failed",
        retryable=False,
        details_hash="a" * 64,
    )
    first = repository.claim_batch(
        owner_id="worker",
        now=_NOW,
        limit=1,
        lease_seconds=30,
    )[0]

    assert (
        repository.mark_failed(
            event_id=first.event_id,
            owner_id="worker",
            expected_version=first.version,
            error=error,
            available_at=_NOW + timedelta(seconds=2),
        )
        is True
    )
    retry = repository.get(storage._conn, first.event_id)  # noqa: SLF001
    assert retry is not None
    assert retry.status == "retry_wait"
    assert retry.available_at == (_NOW + timedelta(seconds=2)).isoformat()
    assert retry.lease_owner is None
    assert retry.last_error_json == (
        '{"code":"consumer_dispatch_failed","details_hash":"'
        + "a" * 64
        + '","message":"one or more consumers failed","retryable":false}'
    )

    second = repository.claim_batch(
        owner_id="worker",
        now=_NOW + timedelta(seconds=2),
        limit=1,
        lease_seconds=30,
    )[0]
    assert (
        repository.mark_failed(
            event_id=second.event_id,
            owner_id="worker",
            expected_version=second.version,
            error=error,
            available_at=_NOW + timedelta(seconds=6),
        )
        is True
    )
    dead = repository.get(storage._conn, second.event_id)  # noqa: SLF001
    assert dead is not None
    assert dead.status == "dead_letter"
    assert dead.attempt_count == 2
    assert (
        repository.claim_batch(
            owner_id="another-worker",
            now=_NOW + timedelta(days=1),
            limit=1,
            lease_seconds=30,
        )
        == []
    )


def test_record_consumption_is_idempotent_and_preserves_first_result(storage: Storage) -> None:
    _add_event(storage, event_id="consumed")
    repository = _repository(storage)

    assert (
        repository.record_consumption(
            event_id="consumed",
            consumer_name="consumer.v1",
            result_hash="a" * 64,
        )
        is True
    )
    assert (
        repository.record_consumption(
            event_id="consumed",
            consumer_name="consumer.v1",
            result_hash="b" * 64,
        )
        is False
    )

    assert repository.consumed_consumers("consumed") == frozenset({"consumer.v1"})
    row = storage._conn.execute(  # noqa: SLF001
        "SELECT result_hash FROM outbox_consumptions WHERE event_id = ?",
        ("consumed",),
    ).fetchone()
    assert row["result_hash"] == "a" * 64


def test_repository_rejects_invalid_claim_arguments_without_mutation(storage: Storage) -> None:
    _add_event(storage, event_id="invalid-claim")
    repository = _repository(storage)

    for kwargs in (
        {"owner_id": "", "limit": 1, "lease_seconds": 1},
        {"owner_id": " ", "limit": 1, "lease_seconds": 1},
        {"owner_id": "worker", "limit": 0, "lease_seconds": 1},
        {"owner_id": "worker", "limit": 1, "lease_seconds": 0},
    ):
        try:
            repository.claim_batch(now=_NOW, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid claim arguments must be rejected")

    record = repository.get(storage._conn, "invalid-claim")  # noqa: SLF001
    assert record is not None
    assert (record.status, record.attempt_count, record.version) == ("pending", 0, 1)
