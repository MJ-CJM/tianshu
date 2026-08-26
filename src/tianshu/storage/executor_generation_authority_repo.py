"""Caller-owned SQLite persistence for executor generation authority."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind, CandidateLifecycle
from tianshu.models.executor_generation_authority import (
    ExecutorGenerationAuthorityJournalEntryV1,
    ExecutorGenerationAuthorityStatus,
    ExecutorGenerationAuthorityV1,
    executor_generation_authority_from_journal,
    validate_executor_generation_authority_transition,
)
from tianshu.storage.evolution_repo import EvolutionRepository

_LIVE_ROOT_STATUSES = (
    ExecutorGenerationAuthorityStatus.PENDING.value,
    ExecutorGenerationAuthorityStatus.AUTHORIZED.value,
    ExecutorGenerationAuthorityStatus.REVOKING.value,
)
_AUTHORITY_COLUMNS = """
candidate_id, authority_id, schema_version, epoch, candidate_version,
candidate_artifact_digest, candidate_canonical_digest, release_digest, scope,
generation_id, promotion_journal_id, status, authority_json, authority_hash,
version, created_at, updated_at, revoked_at, revocation_reason
"""
_JOURNAL_COLUMNS = """
journal_id, authority_id, candidate_id, authority_version, epoch, transition,
candidate_version, candidate_artifact_digest, candidate_canonical_digest,
release_digest, scope, generation_id, promotion_journal_id, reason_code,
entry_json, entry_hash, created_at
"""
_PROMOTION_JOURNAL_COLUMNS = """
promotion_journal_id, command_key, candidate_id, candidate_version,
gate_snapshot_version, action, status, decision_request_id, entry_json,
entry_hash, created_at
"""


class ExecutorGenerationAuthorityRepositoryError(RuntimeError):
    """Base error for executor generation authority persistence."""


class ExecutorGenerationAuthorityConflict(ExecutorGenerationAuthorityRepositoryError):
    """The requested identity, epoch, transition, or CAS version conflicts."""


class ExecutorGenerationAuthorityDecodeError(ExecutorGenerationAuthorityRepositoryError):
    """Durable authority or journal data violates its canonical contract."""


@dataclass(frozen=True, slots=True)
class ExecutorGenerationAuthorityJournalRecord:
    """One verified immutable authority journal row."""

    journal_id: str
    entry: ExecutorGenerationAuthorityJournalEntryV1
    entry_hash: str


class _StartCanaryIntendedEntryV1(BaseModel):
    """Local verification contract for the promotion intent that grants authority."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    command_key: str
    idempotency_key: str | None
    candidate_id: str
    action: Literal["start_canary"]
    status: Literal["intended"]
    decision_request_id: str | None
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    principal_id: str
    reason: str
    pre_transition_candidate_version: int = Field(ge=1)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    gate_snapshot_version: int = Field(ge=1)
    gate_report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    routing_version: int = Field(ge=1)
    allocation_basis_points: int = Field(gt=0, le=10_000)
    receipt: None = None


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError(
            "executor generation authority writes require a caller-owned transaction"
        )


def _savepoint_call[T](
    connection: sqlite3.Connection,
    name: str,
    operation: Callable[[], T],
) -> T:
    connection.execute(f"SAVEPOINT {name}")
    try:
        result = operation()
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
        connection.execute(f"RELEASE SAVEPOINT {name}")
        raise
    connection.execute(f"RELEASE SAVEPOINT {name}")
    return result


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _decode_timestamp(raw: object, *, field: str) -> datetime:
    if not isinstance(raw, str):
        raise ExecutorGenerationAuthorityDecodeError(f"{field} is not text")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ExecutorGenerationAuthorityDecodeError(f"{field} is not an ISO timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutorGenerationAuthorityDecodeError(f"{field} is not timezone-aware")
    normalized = value.astimezone(UTC)
    if raw != normalized.isoformat():
        raise ExecutorGenerationAuthorityDecodeError(f"{field} is not canonical UTC")
    return normalized


def _optional_timestamp(raw: object, *, field: str) -> datetime | None:
    return None if raw is None else _decode_timestamp(raw, field=field)


def _decode_authority(row: sqlite3.Row) -> ExecutorGenerationAuthorityV1:
    raw = row["authority_json"]
    if not isinstance(raw, str):
        raise ExecutorGenerationAuthorityDecodeError("authority_json is not text")
    try:
        authority = ExecutorGenerationAuthorityV1.model_validate_json(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ExecutorGenerationAuthorityDecodeError(
            "persisted executor generation authority violates the v1 contract"
        ) from exc
    if raw != canonical_json_bytes(authority).decode("utf-8"):
        raise ExecutorGenerationAuthorityDecodeError("authority_json is not canonical JSON")
    if row["authority_hash"] != canonical_sha256(authority):
        raise ExecutorGenerationAuthorityDecodeError("authority_hash does not match authority_json")
    try:
        status = ExecutorGenerationAuthorityStatus(row["status"])
        created_at = _decode_timestamp(row["created_at"], field="created_at")
        updated_at = _decode_timestamp(row["updated_at"], field="updated_at")
        revoked_at = _optional_timestamp(row["revoked_at"], field="revoked_at")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ExecutorGenerationAuthorityDecodeError):
            raise
        raise ExecutorGenerationAuthorityDecodeError("authority columns are invalid") from exc
    if (
        row["candidate_id"] != authority.candidate_id
        or row["authority_id"] != authority.authority_id
        or row["schema_version"] != authority.schema_version
        or row["epoch"] != authority.epoch
        or row["candidate_version"] != authority.candidate_version
        or row["candidate_artifact_digest"] != authority.candidate_artifact_digest
        or row["candidate_canonical_digest"] != authority.candidate_canonical_digest
        or row["release_digest"] != authority.release_digest
        or row["scope"] != authority.scope
        or row["generation_id"] != authority.generation_id
        or row["promotion_journal_id"] != authority.promotion_journal_id
        or status is not authority.status
        or row["version"] != authority.version
        or created_at != authority.created_at
        or updated_at != authority.updated_at
        or revoked_at != authority.revoked_at
        or row["revocation_reason"] != authority.revocation_reason
    ):
        raise ExecutorGenerationAuthorityDecodeError(
            "authority columns do not match authority_json"
        )
    return authority


def _verify_promotion_intent(
    connection: sqlite3.Connection,
    authority: ExecutorGenerationAuthorityV1,
) -> None:
    row = connection.execute(
        f"""SELECT {_PROMOTION_JOURNAL_COLUMNS}
            FROM evolution_promotion_journal
            WHERE promotion_journal_id=?""",
        (authority.promotion_journal_id,),
    ).fetchone()
    if row is None:
        raise ExecutorGenerationAuthorityDecodeError("referenced start-canary intent is missing")
    raw = row["entry_json"]
    if not isinstance(raw, str):
        raise ExecutorGenerationAuthorityDecodeError("start-canary intent entry_json is not text")
    try:
        entry = _StartCanaryIntendedEntryV1.model_validate_json(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ExecutorGenerationAuthorityDecodeError(
            "referenced promotion journal is not a start-canary intent"
        ) from exc
    canonical = canonical_json_bytes(entry).decode("utf-8")
    if raw != canonical:
        raise ExecutorGenerationAuthorityDecodeError(
            "start-canary intent entry_json is not canonical JSON"
        )
    entry_hash = hashlib.sha256(raw.encode()).hexdigest()
    if row["entry_hash"] != entry_hash:
        raise ExecutorGenerationAuthorityDecodeError(
            "start-canary intent entry_hash does not match entry_json"
        )
    journal_id = hashlib.sha256(f"{entry.command_key}\0intended".encode()).hexdigest()
    created_at = _decode_timestamp(row["created_at"], field="promotion_created_at")
    if (
        row["promotion_journal_id"] != journal_id
        or row["promotion_journal_id"] != authority.promotion_journal_id
        or row["command_key"] != entry.command_key
        or row["candidate_id"] != entry.candidate_id
        or row["candidate_version"] != entry.pre_transition_candidate_version
        or row["gate_snapshot_version"] != entry.gate_snapshot_version
        or row["action"] != entry.action
        or row["status"] != entry.status
        or row["decision_request_id"] != entry.decision_request_id
        or entry.command_key != authority.start_command_key
        or entry.candidate_id != authority.candidate_id
        or entry.pre_transition_candidate_version != authority.candidate_version
        or entry.candidate_digest != authority.candidate_artifact_digest
        or created_at > authority.created_at
    ):
        raise ExecutorGenerationAuthorityDecodeError(
            "start-canary intent columns or authority binding do not match entry_json"
        )


def _journal_id(*, authority_id: str, authority_version: int, entry_hash: str) -> str:
    return canonical_sha256(
        {
            "authority_id": authority_id,
            "authority_version": authority_version,
            "entry_hash": entry_hash,
        }
    )


def _journal_entry(
    authority: ExecutorGenerationAuthorityV1,
    *,
    reason_code: str,
) -> ExecutorGenerationAuthorityJournalEntryV1:
    return ExecutorGenerationAuthorityJournalEntryV1(
        authority_id=authority.authority_id,
        candidate_id=authority.candidate_id,
        authority_version=authority.version,
        epoch=authority.epoch,
        transition=authority.status,
        candidate_version=authority.candidate_version,
        candidate_artifact_digest=authority.candidate_artifact_digest,
        candidate_canonical_digest=authority.candidate_canonical_digest,
        release_digest=authority.release_digest,
        scope=authority.scope,
        generation_id=authority.generation_id,
        base_generation_id=authority.base_generation_id,
        base_release_digest=authority.base_release_digest,
        promotion_journal_id=authority.promotion_journal_id,
        start_command_key=authority.start_command_key,
        reason_code=reason_code,
        authority_created_at=authority.created_at,
        created_at=authority.updated_at,
        revoked_at=authority.revoked_at,
        revocation_reason=authority.revocation_reason,
    )


def _decode_journal(row: sqlite3.Row) -> ExecutorGenerationAuthorityJournalRecord:
    raw = row["entry_json"]
    if not isinstance(raw, str):
        raise ExecutorGenerationAuthorityDecodeError("entry_json is not text")
    try:
        entry = ExecutorGenerationAuthorityJournalEntryV1.model_validate_json(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise ExecutorGenerationAuthorityDecodeError(
            "persisted authority journal violates the v1 contract"
        ) from exc
    if raw != canonical_json_bytes(entry).decode("utf-8"):
        raise ExecutorGenerationAuthorityDecodeError("entry_json is not canonical JSON")
    entry_hash = canonical_sha256(entry)
    if row["entry_hash"] != entry_hash:
        raise ExecutorGenerationAuthorityDecodeError("entry_hash does not match entry_json")
    journal_id = _journal_id(
        authority_id=entry.authority_id,
        authority_version=entry.authority_version,
        entry_hash=entry_hash,
    )
    try:
        transition = ExecutorGenerationAuthorityStatus(row["transition"])
        created_at = _decode_timestamp(row["created_at"], field="created_at")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ExecutorGenerationAuthorityDecodeError):
            raise
        raise ExecutorGenerationAuthorityDecodeError("journal columns are invalid") from exc
    if (
        row["journal_id"] != journal_id
        or row["authority_id"] != entry.authority_id
        or row["candidate_id"] != entry.candidate_id
        or row["authority_version"] != entry.authority_version
        or row["epoch"] != entry.epoch
        or transition is not entry.transition
        or row["candidate_version"] != entry.candidate_version
        or row["candidate_artifact_digest"] != entry.candidate_artifact_digest
        or row["candidate_canonical_digest"] != entry.candidate_canonical_digest
        or row["release_digest"] != entry.release_digest
        or row["scope"] != entry.scope
        or row["generation_id"] != entry.generation_id
        or row["promotion_journal_id"] != entry.promotion_journal_id
        or row["reason_code"] != entry.reason_code
        or created_at != entry.created_at
    ):
        raise ExecutorGenerationAuthorityDecodeError("journal columns do not match entry_json")
    return ExecutorGenerationAuthorityJournalRecord(
        journal_id=journal_id,
        entry=entry,
        entry_hash=entry_hash,
    )


def _validate_journal_chain(
    records: tuple[ExecutorGenerationAuthorityJournalRecord, ...],
) -> None:
    if not records:
        raise ExecutorGenerationAuthorityDecodeError("authority journal is missing")
    authorities = tuple(
        executor_generation_authority_from_journal(record.entry) for record in records
    )
    try:
        validate_executor_generation_authority_transition(None, authorities[0])
        for previous, current in zip(authorities, authorities[1:], strict=False):
            validate_executor_generation_authority_transition(previous, current)
    except ValueError as exc:
        raise ExecutorGenerationAuthorityDecodeError(
            "authority journal has an invalid lifecycle chain"
        ) from exc


def _verify_tail(
    authority: ExecutorGenerationAuthorityV1,
    records: tuple[ExecutorGenerationAuthorityJournalRecord, ...],
) -> ExecutorGenerationAuthorityJournalRecord:
    _validate_journal_chain(records)
    tail = records[-1]
    if executor_generation_authority_from_journal(tail.entry) != authority:
        raise ExecutorGenerationAuthorityDecodeError(
            "authority row does not match its journal tail"
        )
    return tail


class ExecutorGenerationAuthorityRepository:
    """Stateless primitives whose caller owns every SQLite write transaction."""

    def save(
        self,
        connection: sqlite3.Connection,
        authority: ExecutorGenerationAuthorityV1,
        *,
        expected_version: int,
        reason_code: str,
    ) -> ExecutorGenerationAuthorityV1:
        """Insert or CAS-transition authority, accepting only an exact replay."""

        _require_transaction(connection)
        if expected_version < 0:
            raise ValueError("expected_version must not be negative")
        if not reason_code.strip():
            raise ValueError("reason_code must not be blank")
        return _savepoint_call(
            connection,
            "executor_generation_authority_write",
            lambda: self._save_unlocked(
                connection,
                authority,
                expected_version=expected_version,
                reason_code=reason_code,
            ),
        )

    def _save_unlocked(
        self,
        connection: sqlite3.Connection,
        authority: ExecutorGenerationAuthorityV1,
        *,
        expected_version: int,
        reason_code: str,
    ) -> ExecutorGenerationAuthorityV1:
        current = self.get_current(connection, candidate_id=authority.candidate_id)
        if current == authority:
            if expected_version not in {authority.version - 1, authority.version}:
                raise ExecutorGenerationAuthorityConflict("authority replay version conflict")
            tail = self.verify_journal_tail(
                connection,
                candidate_id=authority.candidate_id,
            )
            if tail.entry.reason_code != reason_code:
                raise ExecutorGenerationAuthorityConflict("authority replay reason conflict")
            return current
        if current is None:
            if expected_version != 0:
                raise ExecutorGenerationAuthorityConflict("authority compare-and-swap conflict")
        elif current.version != expected_version:
            raise ExecutorGenerationAuthorityConflict("authority compare-and-swap conflict")
        try:
            validate_executor_generation_authority_transition(current, authority)
        except ValueError as exc:
            raise ExecutorGenerationAuthorityConflict("invalid authority transition") from exc
        self._require_generation_not_bound_elsewhere(connection, authority)
        if current is None or current.status is ExecutorGenerationAuthorityStatus.REVOKED:
            self._require_pending_bindings(connection, authority)
        try:
            if current is None:
                self._insert_authority(connection, authority)
            else:
                self._update_authority(
                    connection,
                    authority,
                    expected_version=expected_version,
                )
            self._append_journal(connection, authority, reason_code=reason_code)
        except sqlite3.IntegrityError as exc:
            raise ExecutorGenerationAuthorityConflict("authority identity conflict") from exc
        durable = self.get_current(connection, candidate_id=authority.candidate_id)
        if durable != authority:
            raise ExecutorGenerationAuthorityConflict("authority disappeared after write")
        return durable

    def get_current(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
    ) -> ExecutorGenerationAuthorityV1 | None:
        row = connection.execute(
            f"""SELECT {_AUTHORITY_COLUMNS}
                FROM executor_generation_authorities
                WHERE candidate_id=?""",
            (candidate_id,),
        ).fetchone()
        return self._decode_and_verify(connection, row) if row is not None else None

    def get_by_generation(
        self,
        connection: sqlite3.Connection,
        *,
        generation_id: str,
    ) -> ExecutorGenerationAuthorityV1 | None:
        row = connection.execute(
            f"""SELECT {_AUTHORITY_COLUMNS}
                FROM executor_generation_authorities
                WHERE generation_id=?""",
            (generation_id,),
        ).fetchone()
        return self._decode_and_verify(connection, row) if row is not None else None

    def list_recovery_roots(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[ExecutorGenerationAuthorityV1, ...]:
        """Return pending effects and live/revoking READY-generation roots."""

        return self._list_live_roots(connection)

    def list_retention_roots(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[ExecutorGenerationAuthorityV1, ...]:
        """Return generations that must not be collected while authority is live."""

        return self._list_live_roots(connection)

    def _list_live_roots(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[ExecutorGenerationAuthorityV1, ...]:
        placeholders = ",".join("?" for _ in _LIVE_ROOT_STATUSES)
        rows = connection.execute(
            f"""SELECT {_AUTHORITY_COLUMNS}
                FROM executor_generation_authorities
                WHERE status IN ({placeholders})
                ORDER BY scope, generation_id, candidate_id""",
            _LIVE_ROOT_STATUSES,
        ).fetchall()
        return tuple(self._decode_and_verify(connection, row) for row in rows)

    def list_journal(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
    ) -> tuple[ExecutorGenerationAuthorityJournalRecord, ...]:
        rows = connection.execute(
            f"""SELECT {_JOURNAL_COLUMNS}
                FROM executor_generation_authority_journal
                WHERE candidate_id=?
                ORDER BY authority_version""",
            (candidate_id,),
        ).fetchall()
        records = tuple(_decode_journal(row) for row in rows)
        if records:
            _validate_journal_chain(records)
        return records

    def verify_journal_tail(
        self,
        connection: sqlite3.Connection,
        *,
        candidate_id: str,
    ) -> ExecutorGenerationAuthorityJournalRecord:
        row = connection.execute(
            f"""SELECT {_AUTHORITY_COLUMNS}
                FROM executor_generation_authorities
                WHERE candidate_id=?""",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ExecutorGenerationAuthorityConflict("authority does not exist")
        authority = _decode_authority(row)
        tail = _verify_tail(
            authority,
            self.list_journal(connection, candidate_id=candidate_id),
        )
        _verify_promotion_intent(connection, authority)
        return tail

    def _decode_and_verify(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> ExecutorGenerationAuthorityV1:
        authority = _decode_authority(row)
        _verify_tail(
            authority,
            self.list_journal(connection, candidate_id=authority.candidate_id),
        )
        _verify_promotion_intent(connection, authority)
        return authority

    @staticmethod
    def _require_generation_not_bound_elsewhere(
        connection: sqlite3.Connection,
        authority: ExecutorGenerationAuthorityV1,
    ) -> None:
        row = connection.execute(
            """SELECT candidate_id
               FROM executor_generation_authority_journal
               WHERE generation_id=? AND candidate_id<>?
               LIMIT 1""",
            (authority.generation_id, authority.candidate_id),
        ).fetchone()
        if row is not None:
            raise ExecutorGenerationAuthorityConflict(
                "executor generation was already bound to another candidate"
            )

    @staticmethod
    def _require_pending_bindings(
        connection: sqlite3.Connection,
        authority: ExecutorGenerationAuthorityV1,
    ) -> None:
        candidate = EvolutionRepository().get_candidate(connection, authority.candidate_id)
        if (
            candidate is None
            or candidate.kind is not CandidateKind.EXECUTOR
            or candidate.subject_key != authority.scope
            or candidate.lifecycle is not CandidateLifecycle.READY
            or candidate.version != authority.candidate_version
            or candidate.candidate.artifact_digest != authority.candidate_artifact_digest
            or candidate.candidate.canonical_digest != authority.candidate_canonical_digest
        ):
            raise ExecutorGenerationAuthorityConflict(
                "authority does not match a ready executor candidate"
            )
        target = connection.execute(
            """SELECT state, release_digest
               FROM runtime_generations
               WHERE scope=? AND generation_id=?""",
            (authority.scope, authority.generation_id),
        ).fetchone()
        if (
            target is None
            or target["state"] != "staged"
            or target["release_digest"] != authority.release_digest
        ):
            raise ExecutorGenerationAuthorityConflict(
                "authority target is not the exact staged executor generation"
            )
        pointer = connection.execute(
            """SELECT pointer.active_generation_id, generation.release_digest,
                      generation.state
               FROM generation_pointers AS pointer
               JOIN runtime_generations AS generation
                 ON generation.scope = pointer.scope
                AND generation.generation_id = pointer.active_generation_id
               WHERE pointer.scope=?""",
            (authority.scope,),
        ).fetchone()
        if (
            pointer is None
            or pointer["active_generation_id"] != authority.base_generation_id
            or pointer["release_digest"] != authority.base_release_digest
            or pointer["state"] != "active"
        ):
            raise ExecutorGenerationAuthorityConflict(
                "authority base does not match the active generation pointer"
            )
        try:
            _verify_promotion_intent(connection, authority)
        except ExecutorGenerationAuthorityDecodeError as exc:
            raise ExecutorGenerationAuthorityConflict(
                "authority does not match the intended start-canary journal"
            ) from exc

    @staticmethod
    def _insert_authority(
        connection: sqlite3.Connection,
        authority: ExecutorGenerationAuthorityV1,
    ) -> None:
        connection.execute(
            """INSERT INTO executor_generation_authorities (
                   candidate_id, authority_id, schema_version, epoch, candidate_version,
                   candidate_artifact_digest, candidate_canonical_digest, release_digest,
                   scope, generation_id, promotion_journal_id, status, authority_json,
                   authority_hash, version, created_at, updated_at, revoked_at,
                   revocation_reason
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ExecutorGenerationAuthorityRepository._authority_parameters(authority),
        )

    @staticmethod
    def _update_authority(
        connection: sqlite3.Connection,
        authority: ExecutorGenerationAuthorityV1,
        *,
        expected_version: int,
    ) -> None:
        changed = connection.execute(
            """UPDATE executor_generation_authorities
               SET authority_id=?, schema_version=?, epoch=?, candidate_version=?,
                   candidate_artifact_digest=?, candidate_canonical_digest=?, release_digest=?,
                   scope=?, generation_id=?, promotion_journal_id=?, status=?, authority_json=?,
                   authority_hash=?, version=?, created_at=?, updated_at=?, revoked_at=?,
                   revocation_reason=?
               WHERE candidate_id=? AND version=?""",
            (
                authority.authority_id,
                authority.schema_version,
                authority.epoch,
                authority.candidate_version,
                authority.candidate_artifact_digest,
                authority.candidate_canonical_digest,
                authority.release_digest,
                authority.scope,
                authority.generation_id,
                authority.promotion_journal_id,
                authority.status.value,
                canonical_json_bytes(authority).decode("utf-8"),
                canonical_sha256(authority),
                authority.version,
                _timestamp(authority.created_at),
                _timestamp(authority.updated_at),
                _timestamp(authority.revoked_at) if authority.revoked_at is not None else None,
                authority.revocation_reason,
                authority.candidate_id,
                expected_version,
            ),
        ).rowcount
        if changed != 1:
            raise ExecutorGenerationAuthorityConflict("authority compare-and-swap conflict")

    @staticmethod
    def _authority_parameters(authority: ExecutorGenerationAuthorityV1) -> tuple[object, ...]:
        return (
            authority.candidate_id,
            authority.authority_id,
            authority.schema_version,
            authority.epoch,
            authority.candidate_version,
            authority.candidate_artifact_digest,
            authority.candidate_canonical_digest,
            authority.release_digest,
            authority.scope,
            authority.generation_id,
            authority.promotion_journal_id,
            authority.status.value,
            canonical_json_bytes(authority).decode("utf-8"),
            canonical_sha256(authority),
            authority.version,
            _timestamp(authority.created_at),
            _timestamp(authority.updated_at),
            _timestamp(authority.revoked_at) if authority.revoked_at is not None else None,
            authority.revocation_reason,
        )

    @staticmethod
    def _append_journal(
        connection: sqlite3.Connection,
        authority: ExecutorGenerationAuthorityV1,
        *,
        reason_code: str,
    ) -> None:
        entry = _journal_entry(authority, reason_code=reason_code)
        entry_hash = canonical_sha256(entry)
        journal_id = _journal_id(
            authority_id=entry.authority_id,
            authority_version=entry.authority_version,
            entry_hash=entry_hash,
        )
        connection.execute(
            """INSERT INTO executor_generation_authority_journal (
                   journal_id, authority_id, candidate_id, authority_version, epoch,
                   transition, candidate_version, candidate_artifact_digest,
                   candidate_canonical_digest, release_digest, scope, generation_id,
                   promotion_journal_id, reason_code, entry_json, entry_hash, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                journal_id,
                entry.authority_id,
                entry.candidate_id,
                entry.authority_version,
                entry.epoch,
                entry.transition.value,
                entry.candidate_version,
                entry.candidate_artifact_digest,
                entry.candidate_canonical_digest,
                entry.release_digest,
                entry.scope,
                entry.generation_id,
                entry.promotion_journal_id,
                entry.reason_code,
                canonical_json_bytes(entry).decode("utf-8"),
                entry_hash,
                _timestamp(entry.created_at),
            ),
        )


__all__ = [
    "ExecutorGenerationAuthorityConflict",
    "ExecutorGenerationAuthorityDecodeError",
    "ExecutorGenerationAuthorityJournalRecord",
    "ExecutorGenerationAuthorityRepository",
    "ExecutorGenerationAuthorityRepositoryError",
]
