"""Append-only SQLite repository for tamper-evident system audit events."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from ulid import ULID

from tianshu.models.system_audit import (
    GENESIS_SYSTEM_AUDIT_HASH,
    MAX_SYSTEM_AUDIT_PAGE_SIZE,
    AppendSystemAuditRequest,
    SystemAuditEventV1,
    SystemAuditExportV1,
    SystemAuditVerificationV1,
    canonical_system_audit_json,
    hash_system_audit_payload,
)

_PERSISTED_FIELDS = (
    "sequence",
    "id",
    "schema_version",
    "correlation_id",
    "actor_digest",
    "action",
    "outcome",
    "reason_code",
    "subject_kind",
    "subject_digest",
    "metadata_json",
    "previous_hash",
    "created_at",
)


class SystemAuditIntegrityError(RuntimeError):
    """Stable failure raised when an audit read detects chain corruption."""

    def __init__(self, reason_code: str, sequence: int) -> None:
        self.reason_code = reason_code
        self.sequence = sequence
        super().__init__(
            f"system audit integrity check failed: {reason_code} at sequence {sequence}"
        )


def _row_hash_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {field: row[field] for field in _PERSISTED_FIELDS}


def _row_to_event(row: sqlite3.Row) -> SystemAuditEventV1:
    metadata = json.loads(row["metadata_json"])
    if not isinstance(metadata, dict):
        raise ValueError("metadata_json must contain an object")
    return SystemAuditEventV1(
        schema_version=int(row["schema_version"]),
        id=row["id"],
        sequence=row["sequence"],
        correlation_id=row["correlation_id"],
        actor_digest=row["actor_digest"],
        action=row["action"],
        outcome=row["outcome"],
        reason_code=row["reason_code"],
        subject_kind=row["subject_kind"],
        subject_digest=row["subject_digest"],
        metadata=metadata,
        previous_hash=row["previous_hash"],
        event_hash=row["event_hash"],
        created_at=row["created_at"],
    )


def _validate_row(
    row: sqlite3.Row,
    *,
    expected_sequence: int,
    expected_previous_hash: str | None,
) -> SystemAuditEventV1:
    sequence = row["sequence"]
    if sequence != expected_sequence:
        raise SystemAuditIntegrityError("sequence_gap", expected_sequence)
    if expected_previous_hash is not None and row["previous_hash"] != expected_previous_hash:
        raise SystemAuditIntegrityError("previous_hash_mismatch", sequence)
    if row["event_hash"] != hash_system_audit_payload(_row_hash_payload(row)):
        raise SystemAuditIntegrityError("event_hash_mismatch", sequence)
    try:
        return _row_to_event(row)
    except (TypeError, ValueError, ValidationError) as exc:
        raise SystemAuditIntegrityError("row_invalid", sequence) from exc


def _verification(
    rows: list[sqlite3.Row],
) -> tuple[SystemAuditVerificationV1, tuple[SystemAuditEventV1, ...]]:
    events: list[SystemAuditEventV1] = []
    previous_hash = GENESIS_SYSTEM_AUDIT_HASH
    for expected_sequence, row in enumerate(rows, start=1):
        try:
            event = _validate_row(
                row,
                expected_sequence=expected_sequence,
                expected_previous_hash=previous_hash,
            )
        except SystemAuditIntegrityError as exc:
            return (
                SystemAuditVerificationV1(
                    verified=False,
                    event_count=len(events),
                    start_sequence=1 if rows else None,
                    end_sequence=events[-1].sequence if events else None,
                    terminal_hash=events[-1].event_hash if events else GENESIS_SYSTEM_AUDIT_HASH,
                    failure_sequence=exc.sequence,
                    reason_code=exc.reason_code,
                ),
                tuple(events),
            )
        events.append(event)
        previous_hash = event.event_hash
    return (
        SystemAuditVerificationV1(
            verified=True,
            event_count=len(events),
            start_sequence=events[0].sequence if events else None,
            end_sequence=events[-1].sequence if events else None,
            terminal_hash=events[-1].event_hash if events else GENESIS_SYSTEM_AUDIT_HASH,
            reason_code="verified",
        ),
        tuple(events),
    )


def _append_system_audit_unlocked(
    conn: sqlite3.Connection,
    request: AppendSystemAuditRequest,
) -> SystemAuditEventV1:
    """Append exactly one verified event without opening a transaction."""

    tail = conn.execute(
        "SELECT * FROM system_audit_events ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    if tail is None:
        sequence = 1
        previous_hash = GENESIS_SYSTEM_AUDIT_HASH
    else:
        sequence = int(tail["sequence"])
        expected_previous_hash: str | None = None
        if sequence == 1:
            expected_previous_hash = GENESIS_SYSTEM_AUDIT_HASH
        elif sequence > 1:
            predecessor = conn.execute(
                "SELECT * FROM system_audit_events WHERE sequence = ?",
                (sequence - 1,),
            ).fetchone()
            if predecessor is None:
                raise SystemAuditIntegrityError("sequence_gap", sequence - 1)
            predecessor_event = _validate_row(
                predecessor,
                expected_sequence=sequence - 1,
                expected_previous_hash=None,
            )
            expected_previous_hash = predecessor_event.event_hash
        tail_event = _validate_row(
            tail,
            expected_sequence=sequence,
            expected_previous_hash=expected_previous_hash,
        )
        sequence += 1
        previous_hash = tail_event.event_hash

    metadata_json = canonical_system_audit_json(request.metadata)
    created_at = datetime.now(UTC).isoformat()
    payload: dict[str, Any] = {
        "sequence": sequence,
        "id": str(ULID()),
        "schema_version": "1",
        "correlation_id": request.correlation_id,
        "actor_digest": request.actor_digest,
        "action": request.action,
        "outcome": request.outcome,
        "reason_code": request.reason_code,
        "subject_kind": request.subject_kind,
        "subject_digest": request.subject_digest,
        "metadata_json": metadata_json,
        "previous_hash": previous_hash,
        "created_at": created_at,
    }
    event_hash = hash_system_audit_payload(payload)
    conn.execute(
        """
        INSERT INTO system_audit_events (
            sequence, id, schema_version, correlation_id, actor_digest, action,
            outcome, reason_code, subject_kind, subject_digest, metadata_json,
            previous_hash, event_hash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["sequence"],
            payload["id"],
            payload["schema_version"],
            payload["correlation_id"],
            payload["actor_digest"],
            payload["action"],
            payload["outcome"],
            payload["reason_code"],
            payload["subject_kind"],
            payload["subject_digest"],
            payload["metadata_json"],
            payload["previous_hash"],
            event_hash,
            payload["created_at"],
        ),
    )
    row = conn.execute(
        "SELECT * FROM system_audit_events WHERE sequence = ?",
        (sequence,),
    ).fetchone()
    if row is None:
        raise SystemAuditIntegrityError("append_missing", sequence)
    return _validate_row(
        row,
        expected_sequence=sequence,
        expected_previous_hash=previous_hash,
    )


class SystemAuditMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def append_system_audit(self, request: AppendSystemAuditRequest) -> SystemAuditEventV1:
        with self._lock, self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            return _append_system_audit_unlocked(self._conn, request)

    def list_system_audit(
        self,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> list[SystemAuditEventV1]:
        if type(after) is not int or after < 0:
            raise ValueError("after must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= MAX_SYSTEM_AUDIT_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_SYSTEM_AUDIT_PAGE_SIZE}")

        with self._lock:
            previous_hash = GENESIS_SYSTEM_AUDIT_HASH
            if after > 0:
                prefix_rows = self._conn.execute(
                    """
                    SELECT * FROM system_audit_events
                    WHERE sequence <= ?
                    ORDER BY sequence
                    """,
                    (after,),
                ).fetchall()
                verification, prefix = _verification(prefix_rows)
                if not verification.verified:
                    raise SystemAuditIntegrityError(
                        verification.reason_code,
                        verification.failure_sequence or 1,
                    )
                if not prefix or prefix[-1].sequence != after:
                    raise SystemAuditIntegrityError("missing_anchor", after)
                previous_hash = prefix[-1].event_hash
            rows = self._conn.execute(
                """
                SELECT * FROM system_audit_events
                WHERE sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (after, limit),
            ).fetchall()

            events: list[SystemAuditEventV1] = []
            for expected_sequence, row in enumerate(rows, start=after + 1):
                event = _validate_row(
                    row,
                    expected_sequence=expected_sequence,
                    expected_previous_hash=previous_hash,
                )
                events.append(event)
                previous_hash = event.event_hash
            return events

    def export_system_audit(self) -> SystemAuditExportV1:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM system_audit_events ORDER BY sequence"
            ).fetchall()
            verification, events = _verification(rows)
            if not verification.verified:
                raise SystemAuditIntegrityError(
                    verification.reason_code,
                    verification.failure_sequence or 1,
                )
            return SystemAuditExportV1(
                start_sequence=verification.start_sequence,
                end_sequence=verification.end_sequence,
                terminal_hash=verification.terminal_hash,
                events=events,
            )

    def verify_system_audit(self) -> SystemAuditVerificationV1:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM system_audit_events ORDER BY sequence"
            ).fetchall()
            verification, _ = _verification(rows)
            return verification
