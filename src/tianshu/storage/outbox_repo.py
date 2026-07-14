"""Persistence records and connection-level operations for durable submission."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.events import EventEnvelope


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
    """Write submission envelopes without taking transaction ownership."""

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


__all__ = ["OutboxRecord", "OutboxRepository", "SubmissionIdempotencyRecord"]
