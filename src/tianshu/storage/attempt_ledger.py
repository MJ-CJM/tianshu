"""Transactional lease and fencing operations for durable execution attempts."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from ulid import ULID

from tianshu.models.attempt import (
    AttemptDisposition,
    AttemptLeaseV1,
    AttemptOutcomeV1,
    AttemptStatus,
)
from tianshu.models.canonical import RedactedError, canonical_json_bytes
from tianshu.storage.unit_of_work import SqliteUnitOfWork


class AttemptRepositoryError(RuntimeError):
    """Base error for execution-attempt persistence."""


class AttemptConflict(AttemptRepositoryError):
    """The requested attempt transition conflicts with durable state."""


class AttemptFenceLost(AttemptConflict):
    """The supplied execution authority is no longer current."""


class AttemptDecodeError(AttemptRepositoryError):
    """A persisted attempt does not satisfy the strict v1 contract."""


_SELECT_ATTEMPT = """
SELECT attempt_id, schema_version, memorial_id, attempt_no, status,
       owner_id, fencing_token, lease_expires_at, heartbeat_at,
       available_at, max_attempts, failure_json, version, created_at, updated_at
FROM execution_attempts
"""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


def _non_blank(value: str, *, field: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} must be non-blank")
    return value


def _decode_attempt(row: sqlite3.Row) -> AttemptLeaseV1:
    failure: object = None
    if row["failure_json"] is not None:
        try:
            failure = json.loads(row["failure_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise AttemptDecodeError("persisted attempt failure is invalid") from exc
        if not isinstance(failure, dict):
            raise AttemptDecodeError("persisted attempt failure is not an object")
    payload = {
        "attempt_id": row["attempt_id"],
        "schema_version": row["schema_version"],
        "memorial_id": row["memorial_id"],
        "attempt_no": row["attempt_no"],
        "status": row["status"],
        "owner_id": row["owner_id"],
        "fencing_token": row["fencing_token"],
        "lease_expires_at": row["lease_expires_at"],
        "heartbeat_at": row["heartbeat_at"],
        "available_at": row["available_at"],
        "max_attempts": row["max_attempts"],
        "failure": failure,
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    try:
        return AttemptLeaseV1.model_validate_json(json.dumps(payload))
    except (ValidationError, TypeError, ValueError) as exc:
        raise AttemptDecodeError("persisted attempt violates the v1 contract") from exc


def _select_attempt(connection: sqlite3.Connection, attempt_id: str) -> AttemptLeaseV1 | None:
    row = connection.execute(
        _SELECT_ATTEMPT + " WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    return _decode_attempt(row) if row is not None else None


def _insert_retry_attempt(
    connection: sqlite3.Connection,
    *,
    previous: AttemptLeaseV1,
    available_at: datetime,
    created_at: datetime,
) -> AttemptLeaseV1:
    attempt_id = str(ULID())
    connection.execute(
        """
        INSERT INTO execution_attempts (
            attempt_id, schema_version, memorial_id, attempt_no, status,
            owner_id, fencing_token, lease_expires_at, heartbeat_at,
            available_at, max_attempts, failure_json, version, created_at, updated_at
        ) VALUES (?, 1, ?, ?, 'claimable', NULL, ?, NULL, NULL, ?, ?, NULL, 1, ?, ?)
        """,
        (
            attempt_id,
            previous.memorial_id,
            previous.attempt_no + 1,
            previous.fencing_token,
            _iso(available_at),
            previous.max_attempts,
            _iso(created_at),
            _iso(created_at),
        ),
    )
    created = _select_attempt(connection, attempt_id)
    if created is None:  # pragma: no cover - the insert established the identity
        raise AttemptConflict("attempt retry row disappeared")
    return created


class AttemptLeaseRepository:
    """Connection primitive for enqueue plus transaction-owned lease transitions."""

    def __init__(self, unit_of_work_factory: Callable[[], SqliteUnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def enqueue_initial(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        available_at: datetime,
        max_attempts: int = 3,
        attempt_id: str | None = None,
    ) -> AttemptLeaseV1:
        _non_blank(memorial_id, field="memorial_id")
        if attempt_id is not None:
            _non_blank(attempt_id, field="attempt_id")
        available_at = _utc(available_at)
        if type(max_attempts) is not int or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        memorial = connection.execute(
            "SELECT dag_node_id FROM memorials WHERE id = ?", (memorial_id,)
        ).fetchone()
        if memorial is None:
            raise AttemptConflict("attempt memorial does not exist")
        if memorial["dag_node_id"] is not None:
            raise AttemptConflict("attempt memorial is not a root")
        existing_row = connection.execute(
            _SELECT_ATTEMPT + " WHERE memorial_id = ? AND attempt_no = 1",
            (memorial_id,),
        ).fetchone()
        if existing_row is not None:
            existing = _decode_attempt(existing_row)
            if (
                existing.available_at != available_at
                or existing.max_attempts != max_attempts
                or (attempt_id is not None and existing.attempt_id != attempt_id)
            ):
                raise AttemptConflict("initial attempt replay does not match durable envelope")
            return existing
        created_at = min(datetime.now(UTC), available_at)
        attempt_id = attempt_id or str(ULID())
        try:
            connection.execute(
                """
                INSERT INTO execution_attempts (
                    attempt_id, schema_version, memorial_id, attempt_no, status,
                    owner_id, fencing_token, lease_expires_at, heartbeat_at,
                    available_at, max_attempts, failure_json, version, created_at, updated_at
                ) VALUES (?, 1, ?, 1, 'claimable', NULL, 0, NULL, NULL,
                          ?, ?, NULL, 1, ?, ?)
                """,
                (
                    attempt_id,
                    memorial_id,
                    available_at.isoformat(),
                    max_attempts,
                    created_at.isoformat(),
                    created_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AttemptConflict("initial attempt identity conflict") from exc
        created = _select_attempt(connection, attempt_id)
        if created is None:  # pragma: no cover - successful insert preserves the primary key
            raise AttemptConflict("initial attempt disappeared")
        return created

    def list_dispatchable_memorial_ids(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[str, ...]:
        now = _utc(now)
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        with self._unit_of_work_factory() as unit_of_work:
            rows = unit_of_work.connection.execute(
                """
                SELECT attempt.memorial_id
                FROM execution_attempts AS attempt
                JOIN memorials AS memorial ON memorial.id = attempt.memorial_id
                WHERE memorial.dag_node_id IS NULL
                  AND (
                      (attempt.status = 'claimable' AND attempt.available_at <= ?)
                      OR
                      (attempt.status = 'claimed' AND attempt.lease_expires_at <= ?)
                  )
                ORDER BY attempt.available_at, attempt.created_at, attempt.attempt_id
                LIMIT ?
                """,
                (now.isoformat(), now.isoformat(), limit),
            ).fetchall()
            unit_of_work.commit()
        return tuple(str(row["memorial_id"]) for row in rows)

    def require_current(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> None:
        _non_blank(attempt_id, field="attempt_id")
        _non_blank(owner_id, field="owner_id")
        if type(fencing_token) is not int or fencing_token <= 0:
            raise ValueError("fencing_token must be a positive integer")
        now = _utc(now)
        row = connection.execute(
            """
            SELECT status, owner_id, fencing_token, heartbeat_at, lease_expires_at
            FROM execution_attempts
            WHERE attempt_id = ?
            """,
            (attempt_id,),
        ).fetchone()
        try:
            heartbeat_at = (
                datetime.fromisoformat(str(row["heartbeat_at"])) if row is not None else None
            )
            lease_expires_at = (
                datetime.fromisoformat(str(row["lease_expires_at"])) if row is not None else None
            )
        except (TypeError, ValueError) as exc:
            raise AttemptFenceLost("attempt authority is no longer current") from exc
        if (
            row is None
            or row["status"] != AttemptStatus.CLAIMED.value
            or row["owner_id"] != owner_id
            or row["fencing_token"] != fencing_token
            or heartbeat_at is None
            or lease_expires_at is None
            or now < heartbeat_at
            or now >= lease_expires_at
        ):
            raise AttemptFenceLost("attempt authority is no longer current")

    def claim(
        self,
        *,
        memorial_id: str,
        owner_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> AttemptLeaseV1 | None:
        _non_blank(memorial_id, field="memorial_id")
        _non_blank(owner_id, field="owner_id")
        now = _utc(now)
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        try:
            with self._unit_of_work_factory() as unit_of_work:
                connection = unit_of_work.connection
                memorial = connection.execute(
                    "SELECT dag_node_id FROM memorials WHERE id = ?",
                    (memorial_id,),
                ).fetchone()
                if memorial is not None and memorial["dag_node_id"] is not None:
                    raise AttemptConflict("attempt memorial is not a root")
                row = connection.execute(
                    _SELECT_ATTEMPT
                    + """
                    WHERE memorial_id = ?
                      AND status IN ('claimable','claimed','suspended')
                    ORDER BY available_at, created_at, attempt_id
                    LIMIT 1
                    """,
                    (memorial_id,),
                ).fetchone()
                if row is None:
                    unit_of_work.commit()
                    return None
                current = _decode_attempt(row)
                if current.status is AttemptStatus.SUSPENDED:
                    unit_of_work.commit()
                    return None
                if current.status is AttemptStatus.CLAIMED:
                    assert current.lease_expires_at is not None
                    if current.lease_expires_at > now:
                        unit_of_work.commit()
                        return None
                    replacement = self._expire_claim(connection, current=current, now=now)
                    if replacement is None:
                        unit_of_work.commit()
                        return None
                    current = replacement
                if current.available_at > now:
                    unit_of_work.commit()
                    return None
                lease_expires_at = now + timedelta(seconds=lease_seconds)
                cursor = connection.execute(
                    """
                    UPDATE execution_attempts
                    SET status='claimed', owner_id=?, fencing_token=fencing_token + 1,
                        lease_expires_at=?, heartbeat_at=?, version=version + 1, updated_at=?
                    WHERE attempt_id=? AND status='claimable' AND version=? AND available_at <= ?
                    """,
                    (
                        owner_id,
                        lease_expires_at.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                        current.attempt_id,
                        current.version,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount != 1:
                    unit_of_work.commit()
                    return None
                claimed = _select_attempt(connection, current.attempt_id)
                if claimed is None:  # pragma: no cover
                    raise AttemptConflict("claimed attempt disappeared")
                unit_of_work.commit()
                return claimed
        except sqlite3.IntegrityError as exc:
            raise AttemptConflict("attempt claim or retry conflict") from exc

    def _expire_claim(
        self,
        connection: sqlite3.Connection,
        *,
        current: AttemptLeaseV1,
        now: datetime,
    ) -> AttemptLeaseV1 | None:
        failure = RedactedError(
            code="attempt_lease_expired",
            message="execution lease expired",
            retryable=True,
            details_hash=None,
        )
        exhausted = current.attempt_no >= current.max_attempts
        cursor = connection.execute(
            """
            UPDATE execution_attempts
            SET status=?, owner_id=NULL, lease_expires_at=NULL, failure_json=?,
                version=version + 1, updated_at=?
            WHERE attempt_id=? AND status='claimed' AND owner_id=?
              AND fencing_token=? AND version=? AND lease_expires_at <= ?
            """,
            (
                AttemptStatus.DEAD_LETTER.value if exhausted else AttemptStatus.FAILED.value,
                canonical_json_bytes(failure).decode("utf-8"),
                now.isoformat(),
                current.attempt_id,
                current.owner_id,
                current.fencing_token,
                current.version,
                now.isoformat(),
            ),
        )
        if cursor.rowcount != 1:
            raise AttemptConflict("expired attempt changed during reconciliation")
        if exhausted:
            return None
        return _insert_retry_attempt(
            connection,
            previous=current,
            available_at=now,
            created_at=now,
        )

    def heartbeat(
        self,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        now: datetime,
    ) -> bool:
        _non_blank(attempt_id, field="attempt_id")
        _non_blank(owner_id, field="owner_id")
        if type(fencing_token) is not int or fencing_token <= 0:
            raise ValueError("fencing_token must be a positive integer")
        now = _utc(now)
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            current = _select_attempt(connection, attempt_id)
            if (
                current is None
                or current.status is not AttemptStatus.CLAIMED
                or current.owner_id != owner_id
                or current.fencing_token != fencing_token
                or current.heartbeat_at is None
                or current.lease_expires_at is None
                or now < current.heartbeat_at
                or now >= current.lease_expires_at
            ):
                unit_of_work.commit()
                return False
            duration = current.lease_expires_at - current.heartbeat_at
            expires_at = now + duration
            cursor = connection.execute(
                """
                UPDATE execution_attempts
                SET heartbeat_at=?, lease_expires_at=?, version=version + 1, updated_at=?
                WHERE attempt_id=? AND status='claimed' AND owner_id=?
                  AND fencing_token=? AND version=?
                  AND heartbeat_at <= ? AND lease_expires_at > ?
                """,
                (
                    now.isoformat(),
                    expires_at.isoformat(),
                    now.isoformat(),
                    attempt_id,
                    owner_id,
                    fencing_token,
                    current.version,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            updated = cursor.rowcount == 1
            if updated:
                durable = _select_attempt(connection, attempt_id)
                if durable is None:  # pragma: no cover
                    raise AttemptConflict("heartbeat attempt disappeared")
            unit_of_work.commit()
            return updated

    def complete_current(
        self,
        connection: sqlite3.Connection,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        outcome: AttemptOutcomeV1,
    ) -> bool:
        _non_blank(attempt_id, field="attempt_id")
        _non_blank(owner_id, field="owner_id")
        if type(fencing_token) is not int or fencing_token <= 0:
            raise ValueError("fencing_token must be a positive integer")
        if not isinstance(outcome, AttemptOutcomeV1):
            raise TypeError("outcome must be AttemptOutcomeV1")
        completed_at = _utc(outcome.completed_at)
        current = _select_attempt(connection, attempt_id)
        if (
            current is None
            or current.status is not AttemptStatus.CLAIMED
            or current.owner_id != owner_id
            or current.fencing_token != fencing_token
            or current.heartbeat_at is None
            or current.lease_expires_at is None
            or completed_at < current.heartbeat_at
            or completed_at >= current.lease_expires_at
        ):
            return False
        retry = outcome.disposition is AttemptDisposition.RETRY
        exhausted = retry and current.attempt_no >= current.max_attempts
        if exhausted:
            status = AttemptStatus.DEAD_LETTER
        elif retry or outcome.disposition is AttemptDisposition.FAILED:
            status = AttemptStatus.FAILED
        elif outcome.disposition is AttemptDisposition.SUCCEEDED:
            status = AttemptStatus.SUCCEEDED
        else:
            status = AttemptStatus.SUSPENDED
        failure_json = (
            canonical_json_bytes(outcome.failure).decode("utf-8")
            if outcome.failure is not None
            else None
        )
        cursor = connection.execute(
            """
            UPDATE execution_attempts
            SET status=?, owner_id=NULL, lease_expires_at=NULL,
                failure_json=?, version=version + 1, updated_at=?
            WHERE attempt_id=? AND status='claimed' AND owner_id=?
              AND fencing_token=? AND version=?
              AND heartbeat_at <= ? AND lease_expires_at > ?
            """,
            (
                status.value,
                failure_json,
                completed_at.isoformat(),
                attempt_id,
                owner_id,
                fencing_token,
                current.version,
                completed_at.isoformat(),
                completed_at.isoformat(),
            ),
        )
        if cursor.rowcount != 1:
            return False
        if retry and not exhausted:
            assert outcome.retry_at is not None
            _insert_retry_attempt(
                connection,
                previous=current,
                available_at=outcome.retry_at,
                created_at=completed_at,
            )
        return True

    def complete(
        self,
        *,
        attempt_id: str,
        owner_id: str,
        fencing_token: int,
        outcome: AttemptOutcomeV1,
    ) -> bool:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                completed = self.complete_current(
                    unit_of_work.connection,
                    attempt_id=attempt_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    outcome=outcome,
                )
                unit_of_work.commit()
                return completed
        except sqlite3.IntegrityError as exc:
            raise AttemptConflict("attempt retry transition conflict") from exc


__all__ = [
    "AttemptConflict",
    "AttemptDecodeError",
    "AttemptFenceLost",
    "AttemptLeaseRepository",
    "AttemptRepositoryError",
]
