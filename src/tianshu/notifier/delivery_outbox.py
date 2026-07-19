"""Durable retry boundary for internal notification handling.

This module proves only that a notification reaches the in-process notification
handler. Existing Feishu, DingTalk, email, Telegram, and webhook adapters remain
best-effort external boundaries and are deliberately not relabelled as durable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tianshu.models.canonical import RedactedError, canonical_json_bytes
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.correlation import require_correlation_id
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked
from tianshu.storage.unit_of_work import SqliteUnitOfWork


@dataclass(frozen=True, slots=True)
class InternalDeliveryRecord:
    delivery_id: str
    event_id: str
    event_type: str
    correlation_id: str
    edict_id: str | None
    memorial_id: str | None
    status: str
    available_at: datetime
    deadline_at: datetime
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error: RedactedError | None
    delivered_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class InternalDeliveryOutbox:
    """Same-connection repository for retained internal delivery records."""

    def __init__(self, unit_of_work_factory: Callable[[], SqliteUnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def enqueue(
        self,
        *,
        event_id: str,
        event_type: str,
        correlation_id: str,
        edict_id: str | None,
        memorial_id: str | None,
        available_at: datetime,
        deadline_at: datetime,
        max_attempts: int,
    ) -> InternalDeliveryRecord:
        for name, value in (
            ("event_id", event_id),
            ("event_type", event_type),
            ("correlation_id", correlation_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-blank")
        correlation_id = require_correlation_id(correlation_id)
        if type(max_attempts) is not int or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")
        available = _utc(available_at)
        deadline = _utc(deadline_at)
        if deadline < available:
            raise ValueError("deadline_at must not precede available_at")
        delivery_id = hashlib.sha256(f"internal-notification:{event_id}".encode()).hexdigest()
        now = min(datetime.now(UTC), available, deadline)
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            existing = _select(connection, delivery_id)
            if existing is not None:
                if (
                    existing.event_id != event_id
                    or existing.event_type != event_type
                    or existing.correlation_id != correlation_id
                    or existing.edict_id != edict_id
                    or existing.memorial_id != memorial_id
                ):
                    raise ValueError("internal delivery identity conflict")
                unit_of_work.commit()
                return existing
            connection.execute(
                """
                INSERT INTO internal_notification_deliveries (
                    delivery_id, event_id, event_type, correlation_id, edict_id,
                    memorial_id, status, available_at, deadline_at, attempt_count,
                    max_attempts, lease_owner, lease_expires_at, last_error_json,
                    delivered_at, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, 0, ?, NULL, NULL,
                          NULL, NULL, 1, ?, ?)
                """,
                (
                    delivery_id,
                    event_id,
                    event_type,
                    correlation_id,
                    edict_id,
                    memorial_id,
                    available.isoformat(),
                    deadline.isoformat(),
                    max_attempts,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            saved = _select(connection, delivery_id)
            if saved is None:  # pragma: no cover - SQLite insert invariant
                raise RuntimeError("internal delivery disappeared after enqueue")
            unit_of_work.commit()
            return saved

    def get(self, delivery_id: str) -> InternalDeliveryRecord | None:
        with self._unit_of_work_factory() as unit_of_work:
            record = _select(unit_of_work.connection, delivery_id)
            unit_of_work.commit()
            return record

    def probe(self) -> bool:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.connection.execute(
                    "SELECT delivery_id FROM internal_notification_deliveries LIMIT 1"
                ).fetchone()
                unit_of_work.commit()
            return True
        except Exception:  # noqa: BLE001 - readiness probe is fail-closed
            return False

    def expire_due(self, *, now: datetime) -> int:
        current = _utc(now)
        error = _deadline_expired_error()
        expired = 0
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            rows = connection.execute(
                """
                SELECT delivery_id, correlation_id, attempt_count, max_attempts
                FROM internal_notification_deliveries
                WHERE status IN ('pending','claimed','retry_wait') AND deadline_at <= ?
                ORDER BY deadline_at, delivery_id
                """,
                (current.isoformat(),),
            ).fetchall()
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE internal_notification_deliveries
                    SET status='dead_letter', lease_owner=NULL, lease_expires_at=NULL,
                        last_error_json=?, delivered_at=NULL, version=version+1,
                        updated_at=?
                    WHERE delivery_id=? AND status IN ('pending','claimed','retry_wait')
                    """,
                    (
                        canonical_json_bytes(error).decode("utf-8"),
                        current.isoformat(),
                        row["delivery_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    continue
                _append_delivery_audit(
                    connection,
                    action="notification.delivery.dead_lettered",
                    correlation_id=row["correlation_id"],
                    delivery_id=row["delivery_id"],
                    outcome="failed",
                    reason_code="delivery_deadline_expired",
                    attempt_count=int(row["attempt_count"]),
                    max_attempts=int(row["max_attempts"]),
                    deadline_expired=True,
                )
                expired += 1
            unit_of_work.commit()
        return expired

    def claim_batch(
        self,
        *,
        owner_id: str,
        now: datetime,
        limit: int,
        lease_seconds: int,
    ) -> list[InternalDeliveryRecord]:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-blank")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a positive integer")
        current = _utc(now)
        self.expire_due(now=current)
        lease_expires = current + timedelta(seconds=lease_seconds)
        claimed: list[InternalDeliveryRecord] = []
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            rows = connection.execute(
                """
                SELECT delivery_id, version
                FROM internal_notification_deliveries
                WHERE deadline_at > ? AND (
                    (status IN ('pending','retry_wait') AND available_at <= ?)
                    OR
                    (status='claimed' AND lease_expires_at <= ?)
                )
                ORDER BY available_at, created_at, delivery_id
                LIMIT ?
                """,
                (current.isoformat(), current.isoformat(), current.isoformat(), limit),
            ).fetchall()
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE internal_notification_deliveries
                    SET status='claimed', attempt_count=attempt_count+1,
                        lease_owner=?, lease_expires_at=?, version=version+1,
                        updated_at=?
                    WHERE delivery_id=? AND version=? AND deadline_at > ? AND (
                        (status IN ('pending','retry_wait') AND available_at <= ?)
                        OR
                        (status='claimed' AND lease_expires_at <= ?)
                    )
                    """,
                    (
                        owner_id,
                        lease_expires.isoformat(),
                        current.isoformat(),
                        row["delivery_id"],
                        row["version"],
                        current.isoformat(),
                        current.isoformat(),
                        current.isoformat(),
                    ),
                )
                if cursor.rowcount == 1:
                    record = _select(connection, row["delivery_id"])
                    if record is not None:
                        claimed.append(record)
            unit_of_work.commit()
        return claimed

    def mark_delivered(
        self,
        record: InternalDeliveryRecord,
        *,
        owner_id: str,
        now: datetime,
    ) -> bool:
        current = _utc(now)
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            cursor = connection.execute(
                """
                UPDATE internal_notification_deliveries
                SET status='delivered', lease_owner=NULL, lease_expires_at=NULL,
                    last_error_json=NULL, delivered_at=?, version=version+1,
                    updated_at=?
                WHERE delivery_id=? AND status='claimed' AND lease_owner=? AND version=?
                    AND lease_expires_at > ? AND deadline_at > ?
                """,
                (
                    current.isoformat(),
                    current.isoformat(),
                    record.delivery_id,
                    owner_id,
                    record.version,
                    current.isoformat(),
                    current.isoformat(),
                ),
            )
            if cursor.rowcount == 1:
                _append_delivery_audit(
                    connection,
                    action="notification.delivery.delivered",
                    correlation_id=record.correlation_id,
                    delivery_id=record.delivery_id,
                    outcome="succeeded",
                    reason_code="internal_handler_completed",
                    attempt_count=record.attempt_count,
                    max_attempts=record.max_attempts,
                    deadline_expired=False,
                )
            delivered = cursor.rowcount == 1
            if not delivered:
                _dead_letter_expired_claim(connection, record, owner_id, current)
            unit_of_work.commit()
            return delivered

    def mark_failed(
        self,
        record: InternalDeliveryRecord,
        *,
        owner_id: str,
        error: RedactedError,
        available_at: datetime,
        now: datetime,
    ) -> bool:
        current = _utc(now)
        next_available = _utc(available_at)
        dead_letter = (
            record.attempt_count >= record.max_attempts or next_available >= record.deadline_at
        )
        status = "dead_letter" if dead_letter else "retry_wait"
        action = (
            "notification.delivery.dead_lettered"
            if dead_letter
            else "notification.delivery.retry_scheduled"
        )
        reason_code = "delivery_attempts_exhausted" if dead_letter else "delivery_retryable_failure"
        with self._unit_of_work_factory() as unit_of_work:
            connection = unit_of_work.connection
            cursor = connection.execute(
                """
                UPDATE internal_notification_deliveries
                SET status=?, available_at=?, lease_owner=NULL, lease_expires_at=NULL,
                    last_error_json=?, delivered_at=NULL, version=version+1,
                    updated_at=?
                WHERE delivery_id=? AND status='claimed' AND lease_owner=? AND version=?
                    AND lease_expires_at > ? AND deadline_at > ?
                """,
                (
                    status,
                    next_available.isoformat(),
                    canonical_json_bytes(error).decode("utf-8"),
                    current.isoformat(),
                    record.delivery_id,
                    owner_id,
                    record.version,
                    current.isoformat(),
                    current.isoformat(),
                ),
            )
            if cursor.rowcount == 1:
                _append_delivery_audit(
                    connection,
                    action=action,
                    correlation_id=record.correlation_id,
                    delivery_id=record.delivery_id,
                    outcome="failed",
                    reason_code=reason_code,
                    attempt_count=record.attempt_count,
                    max_attempts=record.max_attempts,
                    deadline_expired=False,
                )
            failed = cursor.rowcount == 1
            if not failed:
                _dead_letter_expired_claim(connection, record, owner_id, current)
            unit_of_work.commit()
            return failed


class InternalDeliveryWorker:
    """Lease and invoke one internal handler; retain every terminal record."""

    def __init__(
        self,
        outbox: InternalDeliveryOutbox,
        handler: Callable[[InternalDeliveryRecord], Awaitable[None]],
        *,
        owner_id: str,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: int = 30,
        base_backoff_seconds: float = 1,
        max_backoff_seconds: float = 300,
        poll_interval_seconds: float = 0.5,
    ) -> None:
        if not owner_id.strip():
            raise ValueError("owner_id must be non-blank")
        self._outbox = outbox
        self._handler = handler
        self._owner_id = owner_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lease_seconds = _positive_int(lease_seconds, "lease_seconds")
        self._base_backoff_seconds = _positive_number(base_backoff_seconds, "base_backoff_seconds")
        self._max_backoff_seconds = _positive_number(max_backoff_seconds, "max_backoff_seconds")
        if self._max_backoff_seconds < self._base_backoff_seconds:
            raise ValueError("max_backoff_seconds must be at least base_backoff_seconds")
        self._poll_interval_seconds = _positive_number(
            poll_interval_seconds, "poll_interval_seconds"
        )
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @property
    def is_ready(self) -> bool:
        return (
            self._ready_event.is_set()
            and self._task is not None
            and not self._task.done()
            and not self._stop_event.is_set()
        )

    async def drain_once(self, *, limit: int = 50) -> int:
        now = _utc(self._clock())
        records = self._outbox.claim_batch(
            owner_id=self._owner_id,
            now=now,
            limit=limit,
            lease_seconds=self._lease_seconds,
        )
        for record in records:
            try:
                await self._handler(record)
            except Exception as error:  # noqa: BLE001 - durable retry boundary
                completed_at = _utc(self._clock())
                delay = min(
                    self._max_backoff_seconds,
                    self._base_backoff_seconds * (2 ** max(record.attempt_count - 1, 0)),
                )
                self._outbox.mark_failed(
                    record,
                    owner_id=self._owner_id,
                    error=_redacted_failure(error),
                    available_at=completed_at + timedelta(seconds=delay),
                    now=completed_at,
                )
            else:
                completed_at = _utc(self._clock())
                self._outbox.mark_delivered(
                    record,
                    owner_id=self._owner_id,
                    now=completed_at,
                )
        return len(records)

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("internal delivery worker is already started")
        self._stop_event.clear()
        self._ready_event.clear()
        self._task = asyncio.create_task(self._run(), name="internal-notification-delivery")
        ready_waiter = asyncio.create_task(self._ready_event.wait())
        done, _ = await asyncio.wait(
            {self._task, ready_waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        if self._task in done:
            ready_waiter.cancel()
            with suppress(asyncio.CancelledError):
                await ready_waiter
            await self._task
            raise RuntimeError("internal delivery worker exited during startup")
        await ready_waiter

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop_event.set()
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._ready_event.clear()

    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self.drain_once()
                self._ready_event.set()
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._poll_interval_seconds
                    )
        finally:
            self._ready_event.clear()


def _select(connection, delivery_id: str) -> InternalDeliveryRecord | None:
    row = connection.execute(
        "SELECT * FROM internal_notification_deliveries WHERE delivery_id=?",
        (delivery_id,),
    ).fetchone()
    if row is None:
        return None
    last_error = (
        RedactedError.model_validate(json.loads(row["last_error_json"]))
        if row["last_error_json"] is not None
        else None
    )
    return InternalDeliveryRecord(
        delivery_id=row["delivery_id"],
        event_id=row["event_id"],
        event_type=row["event_type"],
        correlation_id=row["correlation_id"],
        edict_id=row["edict_id"],
        memorial_id=row["memorial_id"],
        status=row["status"],
        available_at=_parse_utc(row["available_at"]),
        deadline_at=_parse_utc(row["deadline_at"]),
        attempt_count=row["attempt_count"],
        max_attempts=row["max_attempts"],
        lease_owner=row["lease_owner"],
        lease_expires_at=(
            _parse_utc(row["lease_expires_at"]) if row["lease_expires_at"] is not None else None
        ),
        last_error=last_error,
        delivered_at=(_parse_utc(row["delivered_at"]) if row["delivered_at"] is not None else None),
        version=row["version"],
        created_at=_parse_utc(row["created_at"]),
        updated_at=_parse_utc(row["updated_at"]),
    )


def _deadline_expired_error() -> RedactedError:
    return RedactedError(
        code="delivery_deadline_expired",
        message="internal notification delivery deadline expired",
        retryable=False,
        details_hash=None,
    )


def _dead_letter_expired_claim(
    connection,
    record: InternalDeliveryRecord,
    owner_id: str,
    current: datetime,
) -> bool:
    cursor = connection.execute(
        """
        UPDATE internal_notification_deliveries
        SET status='dead_letter', lease_owner=NULL, lease_expires_at=NULL,
            last_error_json=?, delivered_at=NULL, version=version+1,
            updated_at=?
        WHERE delivery_id=? AND status='claimed' AND lease_owner=? AND version=?
            AND deadline_at <= ?
        """,
        (
            canonical_json_bytes(_deadline_expired_error()).decode("utf-8"),
            current.isoformat(),
            record.delivery_id,
            owner_id,
            record.version,
            current.isoformat(),
        ),
    )
    if cursor.rowcount != 1:
        return False
    _append_delivery_audit(
        connection,
        action="notification.delivery.dead_lettered",
        correlation_id=record.correlation_id,
        delivery_id=record.delivery_id,
        outcome="failed",
        reason_code="delivery_deadline_expired",
        attempt_count=record.attempt_count,
        max_attempts=record.max_attempts,
        deadline_expired=True,
    )
    return True


def _append_delivery_audit(
    connection,
    *,
    action: str,
    correlation_id: str,
    delivery_id: str,
    outcome: str,
    reason_code: str,
    attempt_count: int,
    max_attempts: int,
    deadline_expired: bool,
) -> None:
    actor_digest = hashlib.sha256(b"tianshu:internal-notification-worker").hexdigest()
    subject_digest = hashlib.sha256(delivery_id.encode("utf-8")).hexdigest()
    _append_system_audit_unlocked(
        connection,
        AppendSystemAuditRequest(
            correlation_id=correlation_id,
            actor_digest=actor_digest,
            action=action,
            outcome=outcome,
            reason_code=reason_code,
            subject_kind="notification_delivery",
            subject_digest=subject_digest,
            metadata={
                "attempt_count": attempt_count,
                "max_attempts": max_attempts,
                "deadline_expired": deadline_expired,
            },
        ),
    )


def _redacted_failure(error: Exception) -> RedactedError:
    fingerprint = hashlib.sha256(
        f"{type(error).__name__}:{error}".encode("utf-8", errors="replace")
    ).hexdigest()
    return RedactedError(
        code="internal_delivery_failed",
        message="internal notification handler failed",
        retryable=True,
        details_hash=fingerprint,
    )


def _parse_utc(value: str) -> datetime:
    return _utc(datetime.fromisoformat(value))


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("internal delivery timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _positive_int(value: int, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return result


__all__ = [
    "InternalDeliveryOutbox",
    "InternalDeliveryRecord",
    "InternalDeliveryWorker",
]
