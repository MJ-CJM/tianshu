"""Transactional authority for persistent governance decisions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from ulid import ULID

from tianshu.models.canonical import canonical_sha256
from tianshu.models.decision import (
    DecisionKind,
    DecisionRecordV1,
    DecisionRequestV1,
    DecisionResolutionV1,
    DecisionStatus,
    RequestDecisionCommand,
    ResolveDecisionCommand,
    validate_resolution_payload,
)
from tianshu.models.events import EventEnvelope
from tianshu.models.principal import AuthContext
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.decision_repo import (
    DecisionIdentityConflict,
    DecisionRepository,
    DecisionStateConflict,
)
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked
from tianshu.storage.unit_of_work import SqliteUnitOfWork

_PRODUCER = "governance.decision_service.v1"


class _DecisionStorage(Protocol):
    decision_repo: DecisionRepository

    def unit_of_work(self) -> SqliteUnitOfWork: ...


class DecisionServiceError(RuntimeError):
    """Stable, disclosure-safe DecisionService failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DecisionNotFound(DecisionServiceError):
    """The requested durable decision does not exist."""


class DecisionConflict(DecisionServiceError):
    """The requested transition conflicts with durable decision authority."""


class DecisionValidationError(DecisionServiceError):
    """A command violates the fixed decision contract."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DecisionValidationError("invalid_decision_timestamp")
    return value.astimezone(UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _audit_request(
    *,
    auth: AuthContext,
    action: str,
    reason_code: str,
    decision_request_id: str,
    metadata: dict[str, str | int | bool | None],
) -> AppendSystemAuditRequest:
    return AppendSystemAuditRequest(
        correlation_id=auth.correlation_id,
        actor_digest=_digest(auth.principal.id),
        action=action,
        outcome="denied",
        reason_code=reason_code,
        subject_kind="decision_request",
        subject_digest=_digest(decision_request_id),
        metadata=metadata,
    )


def _conflict_code(reason_code: str) -> str:
    return {
        "cancelled": "decision_cancelled",
        "deadline_elapsed": "decision_expired",
        "expired": "decision_expired",
        "resolved": "decision_already_resolved",
        "stale_version": "decision_stale",
    }.get(reason_code, "decision_conflict")


class DecisionService:
    """Own request identity, resolution/expiry CAS, audit, and durable outbox writes."""

    def __init__(
        self,
        storage: _DecisionStorage,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._repository = storage.decision_repo
        self._outbox = OutboxRepository()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        return _utc(self._clock())

    def request(
        self,
        command: RequestDecisionCommand,
        *,
        auth: AuthContext,
    ) -> DecisionRequestV1:
        now = self._now()
        if command.expires_at <= now:
            raise DecisionValidationError("invalid_decision_expiry")
        request = DecisionRequestV1(
            decision_request_id=str(ULID()),
            kind=command.kind,
            edict_id=command.edict_id,
            memorial_id=command.memorial_id,
            request_key=command.request_key,
            payload=command.payload,
            payload_hash=canonical_sha256(command.payload),
            requested_by=auth.principal.id,
            expires_at=command.expires_at,
            status=DecisionStatus.PENDING,
            version=1,
            created_at=now,
            updated_at=now,
        )
        with self._storage.unit_of_work() as unit_of_work:
            try:
                saved = self._repository.add_or_get(unit_of_work.connection, request)
            except DecisionIdentityConflict as exc:
                _append_system_audit_unlocked(
                    unit_of_work.connection,
                    _audit_request(
                        auth=auth,
                        action="decision.request.denied",
                        reason_code="decision_identity_conflict",
                        decision_request_id=(
                            exc.existing_request_id or request.decision_request_id
                        ),
                        metadata={"kind": command.kind.value},
                    ),
                )
                unit_of_work.commit()
                raise DecisionConflict("decision_identity_conflict") from None
            unit_of_work.commit()
            return saved

    def get(self, decision_request_id: str) -> DecisionRecordV1 | None:
        with self._storage.unit_of_work() as unit_of_work:
            record = self._repository.get(unit_of_work.connection, decision_request_id)
            unit_of_work.commit()
            return record

    def list_pending(
        self,
        *,
        kind: DecisionKind | None = None,
    ) -> list[DecisionRequestV1]:
        with self._storage.unit_of_work() as unit_of_work:
            requests = self._repository.list_pending(unit_of_work.connection, kind=kind)
            unit_of_work.commit()
            return requests

    def resolve(
        self,
        decision_request_id: str,
        command: ResolveDecisionCommand,
        *,
        auth: AuthContext,
    ) -> DecisionResolutionV1:
        now = self._now()
        with self._storage.unit_of_work() as unit_of_work:
            record = self._repository.get(unit_of_work.connection, decision_request_id)
            if record is None:
                self._deny_resolution(
                    unit_of_work,
                    auth=auth,
                    decision_request_id=decision_request_id,
                    reason_code="decision_not_found",
                    kind=None,
                    status="missing",
                    expected_version=command.expected_version,
                    actual_version=None,
                )
                raise DecisionNotFound("decision_not_found")
            try:
                validate_resolution_payload(
                    record.request.kind,
                    command.action,
                    command.payload,
                )
            except ValueError:
                self._deny_resolution(
                    unit_of_work,
                    auth=auth,
                    decision_request_id=decision_request_id,
                    reason_code="invalid_decision_resolution",
                    kind=record.request.kind,
                    status=record.request.status.value,
                    expected_version=command.expected_version,
                    actual_version=record.request.version,
                )
                raise DecisionValidationError("invalid_decision_resolution") from None
            conflict_reason = self._resolution_conflict(record, command, now)
            if conflict_reason is not None:
                code = _conflict_code(conflict_reason)
                self._deny_resolution(
                    unit_of_work,
                    auth=auth,
                    decision_request_id=decision_request_id,
                    reason_code=code,
                    kind=record.request.kind,
                    status=record.request.status.value,
                    expected_version=command.expected_version,
                    actual_version=record.request.version,
                )
                raise DecisionConflict(code)
            resolution = DecisionResolutionV1(
                decision_request_id=decision_request_id,
                action=command.action,
                reason=command.reason,
                payload=command.payload,
                actor_principal_id=auth.principal.id,
                actor_display_name=auth.principal.display_name,
                resolved_at=now,
            )
            try:
                resolved = self._repository.resolve(
                    unit_of_work.connection,
                    resolution,
                    expected_version=command.expected_version,
                    now=now,
                )
            except DecisionStateConflict as exc:
                if exc.reason_code == "not_found":
                    code = "decision_not_found"
                else:
                    code = _conflict_code(exc.reason_code)
                self._deny_resolution(
                    unit_of_work,
                    auth=auth,
                    decision_request_id=decision_request_id,
                    reason_code=code,
                    kind=record.request.kind,
                    status=record.request.status.value,
                    expected_version=command.expected_version,
                    actual_version=record.request.version,
                )
                if code == "decision_not_found":
                    raise DecisionNotFound(code) from None
                raise DecisionConflict(code) from None
            self._outbox.add(
                unit_of_work.connection,
                EventEnvelope(
                    event_type="decision.resolved",
                    edict_id=resolved.request.edict_id,
                    memorial_id=resolved.request.memorial_id,
                    producer=_PRODUCER,
                    timestamp=now,
                    payload={
                        "schema_version": 1,
                        "decision_request_id": decision_request_id,
                        "kind": resolved.request.kind.value,
                        "action": resolution.action,
                        "request_version": resolved.request.version,
                        "correlation_id": auth.correlation_id,
                    },
                ),
            )
            unit_of_work.commit()
            return resolution

    def deny_invalid_resolution(
        self,
        decision_request_id: str,
        *,
        auth: AuthContext,
    ) -> None:
        """Audit a resolution body rejected before a valid command exists."""

        with self._storage.unit_of_work() as unit_of_work:
            record = self._repository.get(unit_of_work.connection, decision_request_id)
            self._deny_resolution(
                unit_of_work,
                auth=auth,
                decision_request_id=decision_request_id,
                reason_code="invalid_decision_resolution",
                kind=record.request.kind if record is not None else None,
                status=(record.request.status.value if record is not None else "missing"),
                expected_version=None,
                actual_version=(record.request.version if record is not None else None),
            )

    def expire_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        effective_now = self._now() if now is None else _utc(now)
        with self._storage.unit_of_work() as unit_of_work:
            due = self._repository.list_due(
                unit_of_work.connection,
                now=effective_now,
                limit=limit,
            )
            expired_count = 0
            for request in due:
                expired = self._repository.expire(
                    unit_of_work.connection,
                    request.decision_request_id,
                    expected_version=request.version,
                    now=effective_now,
                )
                if expired is None:
                    continue
                self._outbox.add(
                    unit_of_work.connection,
                    EventEnvelope(
                        event_type="decision.expired",
                        edict_id=expired.edict_id,
                        memorial_id=expired.memorial_id,
                        producer=_PRODUCER,
                        timestamp=effective_now,
                        payload={
                            "schema_version": 1,
                            "decision_request_id": expired.decision_request_id,
                            "kind": expired.kind.value,
                            "request_version": expired.version,
                        },
                    ),
                )
                expired_count += 1
            unit_of_work.commit()
            return expired_count

    @staticmethod
    def _resolution_conflict(
        record: DecisionRecordV1,
        command: ResolveDecisionCommand,
        now: datetime,
    ) -> str | None:
        if record.request.status is not DecisionStatus.PENDING:
            return record.request.status.value
        if record.request.version != command.expected_version:
            return "stale_version"
        if record.request.expires_at <= now:
            return "deadline_elapsed"
        return None

    @staticmethod
    def _deny_resolution(
        unit_of_work: SqliteUnitOfWork,
        *,
        auth: AuthContext,
        decision_request_id: str,
        reason_code: str,
        kind: DecisionKind | None,
        status: str,
        expected_version: int | None,
        actual_version: int | None,
    ) -> None:
        metadata: dict[str, str | int | bool | None] = {"status": status}
        if kind is not None:
            metadata["kind"] = kind.value
        if expected_version is not None:
            metadata["expected_version"] = expected_version
        if actual_version is not None:
            metadata["actual_version"] = actual_version
        _append_system_audit_unlocked(
            unit_of_work.connection,
            _audit_request(
                auth=auth,
                action="decision.resolve.denied",
                reason_code=reason_code,
                decision_request_id=decision_request_id,
                metadata=metadata,
            ),
        )
        unit_of_work.commit()


__all__ = [
    "DecisionConflict",
    "DecisionNotFound",
    "DecisionService",
    "DecisionServiceError",
    "DecisionValidationError",
]
