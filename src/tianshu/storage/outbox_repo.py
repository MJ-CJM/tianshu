"""Persistence records and connection-level operations for durable submission."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tianshu.models.canonical import RedactedError, canonical_json_bytes
from tianshu.models.events import EventEnvelope
from tianshu.storage.unit_of_work import SqliteUnitOfWork


@dataclass(frozen=True, slots=True)
class SubmissionIdempotencyRecord:
    principal_id: str
    idempotency_key: str
    request_hash: str
    edict_id: str
    memorial_id: str
    event_id: str
    response_json: str
    created_at: str


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    event_id: str
    event_type: str
    aggregate_type: str
    edict_id: str | None
    memorial_id: str | None
    producer: str
    payload_json: str
    occurred_at: str
    available_at: str
    status: str
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: str | None
    last_error_json: str | None
    published_at: str | None
    version: int


class OutboxRepository:
    """Connection-level submission writes and transaction-owned dispatch operations."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], SqliteUnitOfWork] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def add(self, conn: sqlite3.Connection, event: EventEnvelope) -> None:
        occurred_at = event.timestamp.isoformat()
        conn.execute(
            """
            INSERT INTO outbox_events (
                event_id, event_type, aggregate_type, edict_id, memorial_id,
                producer, payload_json, occurred_at, available_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                event.event_id,
                event.event_type,
                "edict" if event.edict_id is not None else "system",
                event.edict_id,
                event.memorial_id,
                event.producer,
                canonical_json_bytes(event.payload).decode("utf-8"),
                occurred_at,
                occurred_at,
            ),
        )

    def get(self, conn: sqlite3.Connection, event_id: str) -> OutboxRecord | None:
        return _select_outbox_record(conn, event_id)

    def get_submission(
        self,
        conn: sqlite3.Connection,
        *,
        principal_id: str,
        idempotency_key: str,
    ) -> SubmissionIdempotencyRecord | None:
        row = conn.execute(
            """
            SELECT principal_id, idempotency_key, request_hash, edict_id,
                   memorial_id, event_id, response_json, created_at
            FROM submission_idempotency
            WHERE principal_id = ? AND idempotency_key = ?
            """,
            (principal_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        return SubmissionIdempotencyRecord(
            principal_id=row["principal_id"],
            idempotency_key=row["idempotency_key"],
            request_hash=row["request_hash"],
            edict_id=row["edict_id"],
            memorial_id=row["memorial_id"],
            event_id=row["event_id"],
            response_json=row["response_json"],
            created_at=row["created_at"],
        )

    def add_submission(
        self,
        conn: sqlite3.Connection,
        record: SubmissionIdempotencyRecord,
    ) -> None:
        conn.execute(
            """
            INSERT INTO submission_idempotency (
                principal_id, idempotency_key, request_hash, edict_id,
                memorial_id, event_id, response_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.principal_id,
                record.idempotency_key,
                record.request_hash,
                record.edict_id,
                record.memorial_id,
                record.event_id,
                record.response_json,
                record.created_at,
            ),
        )

    def claim_batch(
        self,
        *,
        owner_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> list[OutboxRecord]:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-blank")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        now_iso = _utc_iso(now)
        lease_expires_at = _utc_iso(now + timedelta(seconds=lease_seconds))
        claimed: list[OutboxRecord] = []
        with self._unit_of_work() as unit_of_work:
            conn = unit_of_work.connection
            candidates = conn.execute(
                """
                SELECT event_id, version, typeof(version) AS version_storage_class
                FROM outbox_events
                WHERE (
                    status IN ('pending', 'retry_wait') AND available_at <= ?
                ) OR (
                    status = 'claimed' AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= ?
                )
                ORDER BY available_at, occurred_at, event_id
                LIMIT ?
                """,
                (now_iso, now_iso, limit),
            ).fetchall()
            for candidate in candidates:
                cursor = conn.execute(
                    """
                    UPDATE outbox_events
                    SET status = 'claimed',
                        attempt_count = CASE
                            WHEN typeof(attempt_count) = 'integer' THEN attempt_count + 1
                            ELSE attempt_count
                        END,
                        lease_owner = ?,
                        lease_expires_at = ?,
                        version = CASE
                            WHEN typeof(version) = 'integer' THEN version + 1
                            ELSE version
                        END
                    WHERE event_id = ? AND version = ?
                      AND typeof(version) = ? AND (
                        (status IN ('pending', 'retry_wait') AND available_at <= ?)
                        OR (
                            status = 'claimed' AND lease_expires_at IS NOT NULL
                            AND lease_expires_at <= ?
                        )
                    )
                    """,
                    (
                        owner_id,
                        lease_expires_at,
                        candidate["event_id"],
                        candidate["version"],
                        candidate["version_storage_class"],
                        now_iso,
                        now_iso,
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                row = _select_outbox_record(conn, candidate["event_id"])
                if row is not None:
                    claimed.append(row)
            unit_of_work.commit()
        return claimed

    def mark_published(
        self,
        *,
        event_id: str,
        owner_id: str,
        expected_version: int,
        now: datetime,
    ) -> bool:
        with self._unit_of_work() as unit_of_work:
            cursor = unit_of_work.connection.execute(
                """
                UPDATE outbox_events
                SET status = 'published',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_json = NULL,
                    published_at = ?,
                    version = version + 1
                WHERE event_id = ? AND status = 'claimed'
                  AND lease_owner = ? AND version = ?
                """,
                (_utc_iso(now), event_id, owner_id, expected_version),
            )
            updated = cursor.rowcount == 1
            unit_of_work.commit()
        return updated

    def mark_failed(
        self,
        *,
        event_id: str,
        owner_id: str,
        expected_version: int,
        error: RedactedError,
        available_at: datetime,
    ) -> bool:
        with self._unit_of_work() as unit_of_work:
            cursor = unit_of_work.connection.execute(
                """
                UPDATE outbox_events
                SET status = CASE
                        WHEN attempt_count >= max_attempts THEN 'dead_letter'
                        ELSE 'retry_wait'
                    END,
                    available_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_json = ?,
                    version = version + 1
                WHERE event_id = ? AND status = 'claimed'
                  AND lease_owner = ? AND version = ?
                """,
                (
                    _utc_iso(available_at),
                    canonical_json_bytes(error).decode("utf-8"),
                    event_id,
                    owner_id,
                    expected_version,
                ),
            )
            updated = cursor.rowcount == 1
            unit_of_work.commit()
        return updated

    def mark_poisoned(
        self,
        *,
        event_id: str,
        owner_id: str,
        expected_version: object,
        error: RedactedError,
        now: datetime,
    ) -> bool:
        expected_storage_class = _sqlite_storage_class(expected_version)
        with self._unit_of_work() as unit_of_work:
            cursor = unit_of_work.connection.execute(
                """
                UPDATE outbox_events
                SET status = 'dead_letter',
                    available_at = ?,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_json = ?
                WHERE event_id = ? AND status = 'claimed'
                  AND lease_owner = ? AND version = ? AND typeof(version) = ?
                """,
                (
                    _utc_iso(now),
                    canonical_json_bytes(error).decode("utf-8"),
                    event_id,
                    owner_id,
                    expected_version,
                    expected_storage_class,
                ),
            )
            updated = cursor.rowcount == 1
            unit_of_work.commit()
        return updated

    def record_consumption(
        self,
        *,
        event_id: str,
        consumer_name: str,
        result_hash: str | None,
    ) -> bool:
        if not consumer_name.strip():
            raise ValueError("consumer_name must be non-blank")
        with self._unit_of_work() as unit_of_work:
            cursor = unit_of_work.connection.execute(
                """
                INSERT INTO outbox_consumptions (
                    event_id, consumer_name, result_hash, consumed_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(event_id, consumer_name) DO NOTHING
                """,
                (event_id, consumer_name, result_hash, datetime.now(UTC).isoformat()),
            )
            inserted = cursor.rowcount == 1
            unit_of_work.commit()
        return inserted

    def consumed_consumers(self, event_id: str) -> frozenset[str]:
        with self._unit_of_work() as unit_of_work:
            rows = unit_of_work.connection.execute(
                """
                SELECT consumer_name
                FROM outbox_consumptions
                WHERE event_id = ?
                ORDER BY consumer_name
                """,
                (event_id,),
            ).fetchall()
            unit_of_work.commit()
        return frozenset(row["consumer_name"] for row in rows)

    def submission_identities_for_edict(self, edict_id: str) -> tuple[tuple[str, str], ...]:
        """Return durable event/memorial identities used to restore scheduler timers."""
        with self._unit_of_work() as unit_of_work:
            rows = unit_of_work.connection.execute(
                """
                SELECT event_id, memorial_id
                FROM outbox_events
                WHERE event_type = 'edict.submitted'
                  AND edict_id = ?
                  AND memorial_id IS NOT NULL
                ORDER BY occurred_at, event_id
                """,
                (edict_id,),
            ).fetchall()
            unit_of_work.commit()
        return tuple((row["event_id"], row["memorial_id"]) for row in rows)

    def _unit_of_work(self) -> SqliteUnitOfWork:
        if self._unit_of_work_factory is None:
            raise RuntimeError("repository requires a unit-of-work factory for durable dispatch")
        return self._unit_of_work_factory()


def _select_outbox_record(
    conn: sqlite3.Connection,
    event_id: str,
) -> OutboxRecord | None:
    row = conn.execute(
        """
        SELECT event_id, event_type, aggregate_type, edict_id, memorial_id,
               producer, payload_json, occurred_at, available_at, status,
               attempt_count, max_attempts, lease_owner, lease_expires_at,
               last_error_json, published_at, version
        FROM outbox_events
        WHERE event_id = ?
        """,
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    return OutboxRecord(
        event_id=row["event_id"],
        event_type=row["event_type"],
        aggregate_type=row["aggregate_type"],
        edict_id=row["edict_id"],
        memorial_id=row["memorial_id"],
        producer=row["producer"],
        payload_json=row["payload_json"],
        occurred_at=row["occurred_at"],
        available_at=row["available_at"],
        status=row["status"],
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        last_error_json=row["last_error_json"],
        published_at=row["published_at"],
        version=row["version"],
    )


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("outbox timestamps must be timezone-aware UTC values")
    return value.isoformat()


def _sqlite_storage_class(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bytes):
        return "blob"
    if isinstance(value, str):
        return "text"
    if isinstance(value, float):
        return "real"
    if isinstance(value, int):
        return "integer"
    raise TypeError("expected_version must be a SQLite scalar value")


__all__ = ["OutboxRecord", "OutboxRepository", "SubmissionIdempotencyRecord"]
