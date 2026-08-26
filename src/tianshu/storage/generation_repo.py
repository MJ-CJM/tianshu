"""Caller-owned SQLite persistence for runtime generations and continuity roots."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import ValidationError

from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.models.events import make_event
from tianshu.models.runtime_generation import (
    GenerationPointerV1,
    RuntimeGenerationState,
    RuntimeGenerationV1,
    RuntimeReleaseV1,
    validate_last_good_generation_transition,
    validate_regular_generation_transition,
)
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.storage.correlation import correlation_for_memorial
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked
from tianshu.storage.system_snapshot_repo import (
    SystemSnapshotRepository,
    SystemSnapshotRepositoryError,
)

_PRE_ACTIVATION_STATES = frozenset(
    {
        RuntimeGenerationState.STAGED,
        RuntimeGenerationState.WARMING,
        RuntimeGenerationState.READY,
    }
)
_RECOVERY_STATES = frozenset(
    {
        RuntimeGenerationState.STAGED,
        RuntimeGenerationState.WARMING,
        RuntimeGenerationState.READY,
        RuntimeGenerationState.ACTIVE,
        RuntimeGenerationState.DRAINING,
    }
)
_RETAINED_ATTEMPT_STATES = ("claimable", "claimed", "suspended")
_GENERATION_EVENT_SAVEPOINT = "runtime_generation_event_write"
_GENERATION_ACTOR_DIGEST = hashlib.sha256(b"tianshu.generation-repository").hexdigest()


class GenerationRepositoryError(RuntimeError):
    """Base error for runtime generation persistence."""


class GenerationRepositoryConflict(GenerationRepositoryError):
    """A durable generation identity, version, or pointer conflicts."""


class GenerationRepositoryDecodeError(GenerationRepositoryError):
    """Durable runtime generation data violates its canonical contract."""


@dataclass(frozen=True, slots=True)
class GenerationJournalEntry:
    journal_id: str
    generation_id: str
    generation_version: int
    from_state: RuntimeGenerationState | None
    to_state: RuntimeGenerationState
    entry: Mapping[str, JsonValue]
    entry_hash: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GenerationActivationResult:
    pointer: GenerationPointerV1
    activated: RuntimeGenerationV1
    draining: RuntimeGenerationV1 | None


@dataclass(frozen=True, slots=True)
class GenerationRollbackResult:
    pointer: GenerationPointerV1
    activated: RuntimeGenerationV1
    draining: RuntimeGenerationV1


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("runtime generation writes require a caller-owned transaction")


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


def _utc_now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _utc_now(value).isoformat()


def _decode_timestamp(raw: object, *, field: str) -> datetime:
    if not isinstance(raw, str):
        raise GenerationRepositoryDecodeError(f"{field} is not text")
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise GenerationRepositoryDecodeError(f"{field} is not an ISO timestamp") from exc
    try:
        normalized = _utc_now(value)
    except ValueError as exc:
        raise GenerationRepositoryDecodeError(f"{field} is not timezone-aware") from exc
    if raw != normalized.isoformat():
        raise GenerationRepositoryDecodeError(f"{field} is not canonical UTC")
    return normalized


def _decode_release(row: sqlite3.Row) -> RuntimeReleaseV1:
    raw = row["release_json"]
    if not isinstance(raw, str):
        raise GenerationRepositoryDecodeError("release_json is not text")
    try:
        release = RuntimeReleaseV1.model_validate_json(raw)
    except (ValidationError, TypeError, ValueError) as exc:
        raise GenerationRepositoryDecodeError(
            "persisted runtime release violates the v1 contract"
        ) from exc
    if raw != canonical_json_bytes(release).decode("utf-8"):
        raise GenerationRepositoryDecodeError("release_json is not canonical JSON")
    if (
        row["release_digest"] != release.release_digest
        or row["schema_version"] != release.schema_version
        or row["scope"] != release.scope
    ):
        raise GenerationRepositoryDecodeError("runtime release columns do not match release_json")
    _decode_timestamp(row["first_seen_at"], field="first_seen_at")
    return release


def _decode_generation(row: sqlite3.Row) -> RuntimeGenerationV1:
    try:
        generation = RuntimeGenerationV1(
            schema_version=row["schema_version"],
            generation_id=row["generation_id"],
            scope=row["scope"],
            release_digest=row["release_digest"],
            state=RuntimeGenerationState(row["state"]),
            version=row["version"],
            created_at=_decode_timestamp(row["created_at"], field="created_at"),
            activated_at=(
                _decode_timestamp(row["activated_at"], field="activated_at")
                if row["activated_at"] is not None
                else None
            ),
            updated_at=_decode_timestamp(row["updated_at"], field="updated_at"),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, GenerationRepositoryDecodeError):
            raise
        raise GenerationRepositoryDecodeError(
            "persisted runtime generation violates the v1 contract"
        ) from exc
    return generation


def _decode_pointer(row: sqlite3.Row) -> GenerationPointerV1:
    try:
        return GenerationPointerV1(
            scope=row["scope"],
            active_generation_id=row["active_generation_id"],
            last_good_generation_id=row["last_good_generation_id"],
            version=row["version"],
            updated_at=_decode_timestamp(row["updated_at"], field="updated_at"),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, GenerationRepositoryDecodeError):
            raise
        raise GenerationRepositoryDecodeError(
            "persisted generation pointer violates the v1 contract"
        ) from exc


def _generation_ids_json(generation_ids: tuple[str, ...]) -> str:
    return json.dumps(list(generation_ids), ensure_ascii=False, separators=(",", ":"))


def _decode_generation_ids(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str):
        raise GenerationRepositoryDecodeError("generation_ids_json is not text")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise GenerationRepositoryDecodeError("generation_ids_json is not valid JSON") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise GenerationRepositoryDecodeError(
            "generation_ids_json must contain an array of strings"
        )
    generation_ids = tuple(value)
    if raw != _generation_ids_json(generation_ids):
        raise GenerationRepositoryDecodeError("generation_ids_json is not canonical JSON")
    return generation_ids


def _journal_material(
    *,
    generation_id: str,
    generation_version: int,
    from_state: RuntimeGenerationState | None,
    to_state: RuntimeGenerationState,
    created_at: datetime,
) -> dict[str, JsonValue]:
    return {
        "created_at": _timestamp(created_at),
        "from_state": from_state.value if from_state is not None else None,
        "generation_id": generation_id,
        "generation_version": generation_version,
        "schema_version": 1,
        "to_state": to_state.value,
    }


def _journal_id(*, generation_id: str, generation_version: int, entry_hash: str) -> str:
    return canonical_sha256(
        {
            "entry_hash": entry_hash,
            "generation_id": generation_id,
            "generation_version": generation_version,
        }
    )


def _decode_journal_row(row: sqlite3.Row) -> GenerationJournalEntry:
    raw = row["entry_json"]
    if not isinstance(raw, str):
        raise GenerationRepositoryDecodeError("entry_json is not text")
    try:
        entry = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise GenerationRepositoryDecodeError("entry_json is not valid JSON") from exc
    if not isinstance(entry, dict) or set(entry) != {
        "created_at",
        "from_state",
        "generation_id",
        "generation_version",
        "schema_version",
        "to_state",
    }:
        raise GenerationRepositoryDecodeError("entry_json has an invalid shape")
    try:
        generation_id = str(row["generation_id"])
        generation_version = int(row["generation_version"])
        from_state = (
            RuntimeGenerationState(row["from_state"]) if row["from_state"] is not None else None
        )
        to_state = RuntimeGenerationState(row["to_state"])
        created_at = _decode_timestamp(row["created_at"], field="created_at")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, GenerationRepositoryDecodeError):
            raise
        raise GenerationRepositoryDecodeError("journal columns are invalid") from exc
    expected = _journal_material(
        generation_id=generation_id,
        generation_version=generation_version,
        from_state=from_state,
        to_state=to_state,
        created_at=created_at,
    )
    if entry != expected or raw != canonical_json_bytes(expected).decode("utf-8"):
        raise GenerationRepositoryDecodeError("journal columns do not match canonical entry_json")
    entry_hash = canonical_sha256(expected)
    if row["entry_hash"] != entry_hash:
        raise GenerationRepositoryDecodeError("journal entry_hash does not match entry_json")
    journal_id = _journal_id(
        generation_id=generation_id,
        generation_version=generation_version,
        entry_hash=entry_hash,
    )
    if row["journal_id"] != journal_id:
        raise GenerationRepositoryDecodeError("journal_id does not match journal identity")
    return GenerationJournalEntry(
        journal_id=journal_id,
        generation_id=generation_id,
        generation_version=generation_version,
        from_state=from_state,
        to_state=to_state,
        entry=entry,
        entry_hash=entry_hash,
        created_at=created_at,
    )


class GenerationRepository:
    """Stateless primitives whose caller owns every SQLite write transaction."""

    def record_retired(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str,
    ) -> bool:
        """Best-effort SystemAudit/outbox record for a fail-closed retired pin."""

        if not connection.in_transaction:
            return False
        try:
            connection.execute(f"SAVEPOINT {_GENERATION_EVENT_SAVEPOINT}")
            correlation_id = correlation_for_memorial(connection, memorial_id)
            subject_digest = hashlib.sha256(f"{memorial_id}:{attempt_id}".encode()).hexdigest()
            _append_system_audit_unlocked(
                connection,
                AppendSystemAuditRequest(
                    correlation_id=correlation_id,
                    actor_digest=_GENERATION_ACTOR_DIGEST,
                    action="generation_retired",
                    outcome="failed",
                    reason_code="generation_retired",
                    subject_kind="runtime_generation_binding",
                    subject_digest=subject_digest,
                ),
            )
            OutboxRepository().add(
                connection,
                make_event(
                    event_type="generation_retired",
                    memorial_id=memorial_id,
                    producer="generation_repository",
                    payload={
                        "attempt_id": attempt_id,
                        "correlation_id": correlation_id,
                    },
                ),
            )
            connection.execute(f"RELEASE SAVEPOINT {_GENERATION_EVENT_SAVEPOINT}")
        except Exception:
            with suppress(Exception):
                connection.execute(f"ROLLBACK TO SAVEPOINT {_GENERATION_EVENT_SAVEPOINT}")
                connection.execute(f"RELEASE SAVEPOINT {_GENERATION_EVENT_SAVEPOINT}")
            return False
        return True

    def insert_release(
        self,
        connection: sqlite3.Connection,
        release: RuntimeReleaseV1,
        *,
        first_seen_at: datetime | None = None,
    ) -> RuntimeReleaseV1:
        """Insert immutable release material, accepting only an exact replay."""

        _require_transaction(connection)
        existing = self.get_release(
            connection,
            scope=release.scope,
            release_digest=release.release_digest,
        )
        if existing is not None:
            if existing != release:
                raise GenerationRepositoryConflict("runtime release identity is immutable")
            return existing
        try:
            connection.execute(
                """
                INSERT INTO runtime_generation_releases (
                    release_digest, schema_version, scope, release_json, first_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    release.release_digest,
                    release.schema_version,
                    release.scope,
                    canonical_json_bytes(release).decode("utf-8"),
                    _timestamp(_utc_now(first_seen_at)),
                ),
            )
        except sqlite3.IntegrityError as exc:
            replay = self.get_release(
                connection,
                scope=release.scope,
                release_digest=release.release_digest,
            )
            if replay == release:
                return replay
            raise GenerationRepositoryConflict("runtime release identity conflict") from exc
        durable = self.get_release(
            connection,
            scope=release.scope,
            release_digest=release.release_digest,
        )
        if durable != release:
            raise GenerationRepositoryConflict("runtime release disappeared")
        return durable

    def get_release(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        release_digest: str,
    ) -> RuntimeReleaseV1 | None:
        row = connection.execute(
            """
            SELECT release_digest, schema_version, scope, release_json, first_seen_at
            FROM runtime_generation_releases
            WHERE release_digest = ?
            """,
            (release_digest,),
        ).fetchone()
        if row is None:
            return None
        release = _decode_release(row)
        if release.scope != scope:
            raise GenerationRepositoryConflict("runtime release belongs to another scope")
        return release

    def insert_staged(
        self,
        connection: sqlite3.Connection,
        generation: RuntimeGenerationV1,
    ) -> RuntimeGenerationV1:
        """Insert a version-one staged generation and its initial journal row."""

        _require_transaction(connection)
        return _savepoint_call(
            connection,
            "runtime_generation_insert",
            lambda: self._insert_staged_unlocked(connection, generation),
        )

    def _insert_staged_unlocked(
        self,
        connection: sqlite3.Connection,
        generation: RuntimeGenerationV1,
    ) -> RuntimeGenerationV1:
        _require_transaction(connection)
        if (
            generation.state is not RuntimeGenerationState.STAGED
            or generation.version != 1
            or generation.activated_at is not None
        ):
            raise ValueError("new runtime generations must be staged at version 1")
        release = self.get_release(
            connection,
            scope=generation.scope,
            release_digest=generation.release_digest,
        )
        if release is None:
            raise GenerationRepositoryConflict("runtime generation release does not exist")
        existing = self._get_generation_by_id(connection, generation.generation_id)
        if existing is not None:
            if existing != generation:
                raise GenerationRepositoryConflict("runtime generation identity is immutable")
            self.list_journal(connection, generation_id=generation.generation_id)
            return existing
        try:
            connection.execute(
                """
                INSERT INTO runtime_generations (
                    generation_id, schema_version, scope, release_digest, state, version,
                    created_at, activated_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    generation.generation_id,
                    generation.schema_version,
                    generation.scope,
                    generation.release_digest,
                    generation.state.value,
                    generation.version,
                    _timestamp(generation.created_at),
                    None,
                    _timestamp(generation.updated_at),
                ),
            )
            self._append_journal(
                connection,
                generation=generation,
                from_state=None,
            )
        except sqlite3.IntegrityError as exc:
            replay = self._get_generation_by_id(connection, generation.generation_id)
            if replay == generation:
                self.list_journal(connection, generation_id=generation.generation_id)
                return replay
            raise GenerationRepositoryConflict("runtime generation identity conflict") from exc
        durable = self.get_generation(
            connection,
            scope=generation.scope,
            generation_id=generation.generation_id,
        )
        if durable != generation:
            raise GenerationRepositoryConflict("runtime generation disappeared")
        return durable

    def get_generation(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        generation_id: str,
    ) -> RuntimeGenerationV1 | None:
        generation = self._get_generation_by_id(connection, generation_id)
        if generation is None:
            return None
        if generation.scope != scope:
            raise GenerationRepositoryConflict("runtime generation belongs to another scope")
        return generation

    def list_by_scope(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
    ) -> tuple[RuntimeGenerationV1, ...]:
        rows = connection.execute(
            """
            SELECT generation_id, schema_version, scope, release_digest, state, version,
                   created_at, activated_at, updated_at
            FROM runtime_generations
            WHERE scope = ?
            ORDER BY created_at, generation_id
            """,
            (scope,),
        ).fetchall()
        return tuple(_decode_generation(row) for row in rows)

    def list_recovery_candidates(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str | None = None,
    ) -> tuple[RuntimeGenerationV1, ...]:
        if scope is not None and not scope.strip():
            raise ValueError("scope must be non-blank")
        placeholders = ",".join("?" for _ in _RECOVERY_STATES)
        scope_clause = " AND scope = ?" if scope is not None else ""
        parameters: tuple[object, ...] = tuple(
            state.value for state in sorted(_RECOVERY_STATES, key=lambda state: state.value)
        )
        if scope is not None:
            parameters += (scope,)
        rows = connection.execute(
            f"""
            SELECT generation_id, schema_version, scope, release_digest, state, version,
                   created_at, activated_at, updated_at
            FROM runtime_generations
            WHERE state IN ({placeholders}){scope_clause}
            ORDER BY scope, created_at, generation_id
            """,
            parameters,
        ).fetchall()
        generations = tuple(_decode_generation(row) for row in rows)
        for generation in generations:
            self.list_journal(connection, generation_id=generation.generation_id)
        return generations

    def get_pointer(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
    ) -> GenerationPointerV1 | None:
        row = connection.execute(
            """
            SELECT scope, active_generation_id, last_good_generation_id, version, updated_at
            FROM generation_pointers
            WHERE scope = ?
            """,
            (scope,),
        ).fetchone()
        return _decode_pointer(row) if row is not None else None

    def transition_pre_activation(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        generation_id: str,
        target_state: RuntimeGenerationState,
        expected_version: int,
        updated_at: datetime | None = None,
    ) -> RuntimeGenerationV1:
        """CAS one staged/warming/ready generation without activating it."""

        _require_transaction(connection)
        return _savepoint_call(
            connection,
            "runtime_generation_pre_activation",
            lambda: self._transition_pre_activation_unlocked(
                connection,
                scope=scope,
                generation_id=generation_id,
                target_state=target_state,
                expected_version=expected_version,
                updated_at=updated_at,
            ),
        )

    def _transition_pre_activation_unlocked(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        generation_id: str,
        target_state: RuntimeGenerationState,
        expected_version: int,
        updated_at: datetime | None = None,
    ) -> RuntimeGenerationV1:
        _require_transaction(connection)
        current = self._require_generation(connection, scope=scope, generation_id=generation_id)
        if current.state not in _PRE_ACTIVATION_STATES or target_state not in (
            _PRE_ACTIVATION_STATES | {RuntimeGenerationState.FAILED}
        ):
            raise GenerationRepositoryConflict("transition is not pre-activation")
        return self._transition(
            connection,
            current=current,
            target_state=target_state,
            expected_version=expected_version,
            updated_at=_utc_now(updated_at),
            last_good=False,
        )

    def activate(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        target_generation_id: str,
        expected_generation_version: int,
        expected_pointer_version: int | None,
        updated_at: datetime | None = None,
    ) -> GenerationActivationResult:
        """Atomically drain the old active, activate a ready target, then CAS its pointer."""

        _require_transaction(connection)
        connection.execute("SAVEPOINT runtime_generation_activate")
        try:
            result = self._activate_unlocked(
                connection,
                scope=scope,
                target_generation_id=target_generation_id,
                expected_generation_version=expected_generation_version,
                expected_pointer_version=expected_pointer_version,
                updated_at=updated_at,
            )
        except sqlite3.IntegrityError as exc:
            connection.execute("ROLLBACK TO SAVEPOINT runtime_generation_activate")
            connection.execute("RELEASE SAVEPOINT runtime_generation_activate")
            raise GenerationRepositoryConflict("atomic generation activation failed") from exc
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT runtime_generation_activate")
            connection.execute("RELEASE SAVEPOINT runtime_generation_activate")
            raise
        connection.execute("RELEASE SAVEPOINT runtime_generation_activate")
        return result

    def _activate_unlocked(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        target_generation_id: str,
        expected_generation_version: int,
        expected_pointer_version: int | None,
        updated_at: datetime | None = None,
    ) -> GenerationActivationResult:
        _require_transaction(connection)
        now = _utc_now(updated_at)
        target = self._require_generation(
            connection,
            scope=scope,
            generation_id=target_generation_id,
        )
        if target.state is not RuntimeGenerationState.READY:
            raise GenerationRepositoryConflict("activation target is not ready")
        if target.version != expected_generation_version:
            raise GenerationRepositoryConflict("runtime generation version conflict")
        pointer = self.get_pointer(connection, scope=scope)
        draining: RuntimeGenerationV1 | None = None
        if pointer is None:
            if expected_pointer_version is not None:
                raise GenerationRepositoryConflict("generation pointer does not exist")
            activated = self._transition(
                connection,
                current=target,
                target_state=RuntimeGenerationState.ACTIVE,
                expected_version=expected_generation_version,
                updated_at=now,
                last_good=False,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO generation_pointers (
                        scope, active_generation_id, last_good_generation_id, version, updated_at
                    ) VALUES (?, ?, ?, 1, ?)
                    """,
                    (scope, activated.generation_id, activated.generation_id, _timestamp(now)),
                )
            except sqlite3.IntegrityError as exc:
                raise GenerationRepositoryConflict("generation pointer identity conflict") from exc
        else:
            if expected_pointer_version is None or pointer.version != expected_pointer_version:
                raise GenerationRepositoryConflict("generation pointer version conflict")
            current_active = self._require_generation(
                connection,
                scope=scope,
                generation_id=pointer.active_generation_id,
            )
            if current_active.state is not RuntimeGenerationState.ACTIVE:
                raise GenerationRepositoryConflict(
                    "generation pointer does not reference active state"
                )
            draining = self._transition(
                connection,
                current=current_active,
                target_state=RuntimeGenerationState.DRAINING,
                expected_version=current_active.version,
                updated_at=now,
                last_good=False,
            )
            activated = self._transition(
                connection,
                current=target,
                target_state=RuntimeGenerationState.ACTIVE,
                expected_version=expected_generation_version,
                updated_at=now,
                last_good=False,
            )
            changed = connection.execute(
                """
                UPDATE generation_pointers
                SET active_generation_id = ?, last_good_generation_id = ?,
                    version = version + 1, updated_at = ?
                WHERE scope = ? AND version = ? AND active_generation_id = ?
                """,
                (
                    activated.generation_id,
                    draining.generation_id,
                    _timestamp(now),
                    scope,
                    expected_pointer_version,
                    current_active.generation_id,
                ),
            ).rowcount
            if changed != 1:
                raise GenerationRepositoryConflict("generation pointer CAS conflict")
        durable_pointer = self.get_pointer(connection, scope=scope)
        if durable_pointer is None:
            raise GenerationRepositoryConflict("generation pointer disappeared")
        return GenerationActivationResult(
            pointer=durable_pointer,
            activated=activated,
            draining=draining,
        )

    def rollback_to_last_good(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        expected_pointer_version: int,
        updated_at: datetime | None = None,
    ) -> GenerationRollbackResult:
        """Atomically re-activate only the retained last-good generation."""

        _require_transaction(connection)
        connection.execute("SAVEPOINT runtime_generation_rollback")
        try:
            result = self._rollback_to_last_good_unlocked(
                connection,
                scope=scope,
                expected_pointer_version=expected_pointer_version,
                updated_at=updated_at,
            )
        except sqlite3.IntegrityError as exc:
            connection.execute("ROLLBACK TO SAVEPOINT runtime_generation_rollback")
            connection.execute("RELEASE SAVEPOINT runtime_generation_rollback")
            raise GenerationRepositoryConflict("atomic generation rollback failed") from exc
        except BaseException:
            connection.execute("ROLLBACK TO SAVEPOINT runtime_generation_rollback")
            connection.execute("RELEASE SAVEPOINT runtime_generation_rollback")
            raise
        connection.execute("RELEASE SAVEPOINT runtime_generation_rollback")
        return result

    def _rollback_to_last_good_unlocked(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        expected_pointer_version: int,
        updated_at: datetime | None = None,
    ) -> GenerationRollbackResult:
        _require_transaction(connection)
        now = _utc_now(updated_at)
        pointer = self.get_pointer(connection, scope=scope)
        if pointer is None or pointer.version != expected_pointer_version:
            raise GenerationRepositoryConflict("generation pointer version conflict")
        if pointer.active_generation_id == pointer.last_good_generation_id:
            raise GenerationRepositoryConflict("generation pointer has no rollback target")
        current_active = self._require_generation(
            connection,
            scope=scope,
            generation_id=pointer.active_generation_id,
        )
        last_good = self._require_generation(
            connection,
            scope=scope,
            generation_id=pointer.last_good_generation_id,
        )
        if current_active.state is not RuntimeGenerationState.ACTIVE:
            raise GenerationRepositoryConflict("generation pointer does not reference active state")
        if last_good.state is not RuntimeGenerationState.DRAINING:
            raise GenerationRepositoryConflict("last-good generation is not retained for rollback")
        draining = self._transition(
            connection,
            current=current_active,
            target_state=RuntimeGenerationState.DRAINING,
            expected_version=current_active.version,
            updated_at=now,
            last_good=False,
        )
        activated = self._transition(
            connection,
            current=last_good,
            target_state=RuntimeGenerationState.ACTIVE,
            expected_version=last_good.version,
            updated_at=now,
            last_good=True,
        )
        changed = connection.execute(
            """
            UPDATE generation_pointers
            SET active_generation_id = ?, version = version + 1, updated_at = ?
            WHERE scope = ? AND version = ?
              AND active_generation_id = ? AND last_good_generation_id = ?
            """,
            (
                activated.generation_id,
                _timestamp(now),
                scope,
                expected_pointer_version,
                current_active.generation_id,
                last_good.generation_id,
            ),
        ).rowcount
        if changed != 1:
            raise GenerationRepositoryConflict("generation pointer CAS conflict")
        durable_pointer = self.get_pointer(connection, scope=scope)
        if durable_pointer is None:
            raise GenerationRepositoryConflict("generation pointer disappeared")
        return GenerationRollbackResult(
            pointer=durable_pointer,
            activated=activated,
            draining=draining,
        )

    def validate_generation_ids(
        self,
        connection: sqlite3.Connection,
        generation_ids: tuple[str, ...],
        *,
        expected_scopes: tuple[str, ...] | None = None,
    ) -> tuple[RuntimeGenerationV1, ...]:
        """Validate a canonical one-generation-per-scope binding tuple."""

        if not isinstance(generation_ids, tuple) or any(
            not isinstance(generation_id, str) for generation_id in generation_ids
        ):
            raise TypeError("generation_ids must be a tuple of strings")
        if len(generation_ids) != len(set(generation_ids)):
            raise GenerationRepositoryConflict("generation_ids contains duplicate identities")
        generations: list[RuntimeGenerationV1] = []
        seen_scopes: set[str] = set()
        for generation_id in generation_ids:
            generation = self._get_generation_by_id(connection, generation_id)
            if generation is None:
                raise GenerationRepositoryConflict("generation_ids contains an unknown identity")
            self.list_journal(connection, generation_id=generation_id)
            if generation.scope in seen_scopes:
                raise GenerationRepositoryConflict("generation_ids contains duplicate scopes")
            if generation.state in {
                RuntimeGenerationState.FAILED,
                RuntimeGenerationState.DISPOSED,
            }:
                raise GenerationRepositoryConflict("generation_ids contains an unusable generation")
            if (
                self.get_release(
                    connection,
                    scope=generation.scope,
                    release_digest=generation.release_digest,
                )
                is None
            ):
                raise GenerationRepositoryConflict("generation release does not exist")
            seen_scopes.add(generation.scope)
            generations.append(generation)
        canonical = tuple(sorted(generations, key=lambda item: (item.scope, item.generation_id)))
        if tuple(item.generation_id for item in canonical) != generation_ids:
            raise GenerationRepositoryConflict("generation_ids is not in canonical scope order")
        if expected_scopes is not None:
            if len(expected_scopes) != len(set(expected_scopes)):
                raise ValueError("expected_scopes must be unique")
            if tuple(sorted(expected_scopes)) != tuple(item.scope for item in canonical):
                raise GenerationRepositoryConflict("generation_ids does not match expected scopes")
        return canonical

    def retained_generation_ids(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str | None = None,
    ) -> frozenset[str]:
        """Derive durable roots without maintaining a persisted refcount."""

        if scope is not None and not scope.strip():
            raise ValueError("scope must be non-blank")
        generation_ids: set[str] = set()
        if scope is None:
            pointer_rows = connection.execute(
                """
                SELECT scope, active_generation_id, last_good_generation_id, version, updated_at
                FROM generation_pointers
                """
            ).fetchall()
        else:
            pointer_rows = connection.execute(
                """
                SELECT scope, active_generation_id, last_good_generation_id, version, updated_at
                FROM generation_pointers
                WHERE scope = ?
                """,
                (scope,),
            ).fetchall()
        for row in pointer_rows:
            pointer = _decode_pointer(row)
            active = self._require_generation(
                connection,
                scope=pointer.scope,
                generation_id=pointer.active_generation_id,
            )
            last_good = self._require_generation(
                connection,
                scope=pointer.scope,
                generation_id=pointer.last_good_generation_id,
            )
            if active.state is not RuntimeGenerationState.ACTIVE:
                raise GenerationRepositoryDecodeError("pointer active root is not active")
            expected_last_good_state = (
                RuntimeGenerationState.ACTIVE
                if last_good.generation_id == active.generation_id
                else RuntimeGenerationState.DRAINING
            )
            if last_good.state is not expected_last_good_state:
                raise GenerationRepositoryDecodeError("pointer last-good root has an invalid state")
            for generation in (active, last_good):
                if (
                    self.get_release(
                        connection,
                        scope=generation.scope,
                        release_digest=generation.release_digest,
                    )
                    is None
                ):
                    raise GenerationRepositoryDecodeError("pointer release does not exist")
                generation_ids.add(generation.generation_id)

        attempt_rows = connection.execute(
            """
            SELECT exact_binding.state,
                   exact_binding.generation_ids_json AS exact_generation_ids_json,
                   legacy_binding.generation_ids_json AS legacy_generation_ids_json
            FROM execution_attempts AS attempt
            LEFT JOIN run_generation_bindings AS exact_binding
              ON exact_binding.memorial_id = attempt.memorial_id
             AND exact_binding.attempt_id = attempt.attempt_id
            LEFT JOIN run_system_bindings AS legacy_binding
              ON legacy_binding.memorial_id = attempt.memorial_id
             AND legacy_binding.attempt_id = attempt.attempt_id
            WHERE attempt.status IN (?, ?, ?)
            """,
            _RETAINED_ATTEMPT_STATES,
        ).fetchall()
        for row in attempt_rows:
            exact_json = row["exact_generation_ids_json"]
            legacy_json = row["legacy_generation_ids_json"]
            if row["state"] == "unresolved":
                raise GenerationRepositoryDecodeError("attempt generation binding is unresolved")
            if row["state"] == "bound":
                selected_ids = _decode_generation_ids(exact_json)
                if legacy_json is not None and _decode_generation_ids(legacy_json) != selected_ids:
                    raise GenerationRepositoryDecodeError(
                        "attempt generation and system bindings conflict"
                    )
            elif row["state"] is None and legacy_json is not None:
                selected_ids = _decode_generation_ids(legacy_json)
            elif row["state"] is None:
                continue
            else:
                raise GenerationRepositoryDecodeError("attempt generation binding state is invalid")
            generations = self.validate_generation_ids(
                connection,
                selected_ids,
            )
            generation_ids.update(
                item.generation_id for item in generations if scope is None or item.scope == scope
            )

        retry_rows = connection.execute(
            """
            SELECT attempt.memorial_id
            FROM execution_attempts AS attempt
            JOIN memorials AS memorial ON memorial.id = attempt.memorial_id
            WHERE attempt.status IN (?, ?, ?)
              AND NOT EXISTS (
                  SELECT 1
                  FROM run_generation_bindings AS exact_generation_binding
                  WHERE exact_generation_binding.memorial_id = attempt.memorial_id
                    AND exact_generation_binding.attempt_id = attempt.attempt_id
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM run_system_bindings AS exact_binding
                  WHERE exact_binding.memorial_id = attempt.memorial_id
                    AND exact_binding.attempt_id = attempt.attempt_id
              )
            """,
            _RETAINED_ATTEMPT_STATES,
        ).fetchall()
        snapshot_repository = SystemSnapshotRepository()
        for row in retry_rows:
            try:
                retry_generation_ids = snapshot_repository.get_continuity_generation_ids(
                    connection,
                    str(row["memorial_id"]),
                )
            except SystemSnapshotRepositoryError as exc:
                raise GenerationRepositoryDecodeError(
                    "attempt continuity binding is invalid"
                ) from exc
            if retry_generation_ids is None:
                continue
            generations = self.validate_generation_ids(
                connection,
                retry_generation_ids,
            )
            generation_ids.update(
                item.generation_id for item in generations if scope is None or item.scope == scope
            )

        continuity_rows = connection.execute(
            """
            WITH ranked_roots AS (
                SELECT edict.id AS edict_id,
                       memorial.id AS memorial_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY edict.id
                           ORDER BY memorial.created_at DESC, memorial.id DESC
                       ) AS rank
                FROM edicts AS edict
                JOIN memorials AS memorial ON memorial.edict_id = edict.id
                WHERE edict.status = 'open' AND memorial.dag_node_id IS NULL
            )
            SELECT memorial_id
            FROM ranked_roots
            WHERE rank = 1
            """
        ).fetchall()
        for row in continuity_rows:
            try:
                continuity_generation_ids = snapshot_repository.get_continuity_generation_ids(
                    connection,
                    str(row["memorial_id"]),
                )
            except SystemSnapshotRepositoryError as exc:
                raise GenerationRepositoryDecodeError("open continuity binding is invalid") from exc
            if continuity_generation_ids is None:
                continue
            generations = self.validate_generation_ids(
                connection,
                continuity_generation_ids,
            )
            generation_ids.update(
                item.generation_id for item in generations if scope is None or item.scope == scope
            )
        return frozenset(generation_ids)

    def dispose_if_unreferenced(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        generation_id: str,
        expected_version: int,
        updated_at: datetime | None = None,
    ) -> RuntimeGenerationV1 | None:
        """Dispose a draining generation only after rechecking all durable roots."""

        _require_transaction(connection)
        return _savepoint_call(
            connection,
            "runtime_generation_dispose",
            lambda: self._dispose_if_unreferenced_unlocked(
                connection,
                scope=scope,
                generation_id=generation_id,
                expected_version=expected_version,
                updated_at=updated_at,
            ),
        )

    def _dispose_if_unreferenced_unlocked(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        generation_id: str,
        expected_version: int,
        updated_at: datetime | None = None,
    ) -> RuntimeGenerationV1 | None:
        _require_transaction(connection)
        generation = self._require_generation(
            connection,
            scope=scope,
            generation_id=generation_id,
        )
        if generation.version != expected_version:
            raise GenerationRepositoryConflict("runtime generation version conflict")
        if generation.state is not RuntimeGenerationState.DRAINING:
            raise GenerationRepositoryConflict("only draining generations can be disposed")
        if generation_id in self.retained_generation_ids(connection, scope=scope):
            return None
        return self._transition(
            connection,
            current=generation,
            target_state=RuntimeGenerationState.DISPOSED,
            expected_version=expected_version,
            updated_at=_utc_now(updated_at),
            last_good=False,
        )

    def list_journal(
        self,
        connection: sqlite3.Connection,
        *,
        generation_id: str,
    ) -> tuple[GenerationJournalEntry, ...]:
        rows = connection.execute(
            """
            SELECT journal_id, generation_id, generation_version, from_state, to_state,
                   entry_json, entry_hash, created_at
            FROM runtime_generation_journal
            WHERE generation_id = ?
            ORDER BY generation_version
            """,
            (generation_id,),
        ).fetchall()
        entries = tuple(_decode_journal_row(row) for row in rows)
        generation = self._get_generation_by_id(connection, generation_id)
        if generation is None:
            if entries:
                raise GenerationRepositoryDecodeError("journal references an unknown generation")
            return ()
        if not entries:
            raise GenerationRepositoryDecodeError("runtime generation journal is missing")
        previous: GenerationJournalEntry | None = None
        for expected_version, entry in enumerate(entries, start=1):
            if entry.generation_version != expected_version:
                raise GenerationRepositoryDecodeError(
                    "runtime generation journal is not contiguous"
                )
            if previous is None:
                if (
                    entry.from_state is not None
                    or entry.to_state is not RuntimeGenerationState.STAGED
                ):
                    raise GenerationRepositoryDecodeError(
                        "runtime generation journal has no genesis"
                    )
            elif entry.from_state is not previous.to_state:
                raise GenerationRepositoryDecodeError("runtime generation journal chain is broken")
            else:
                assert entry.from_state is not None
                try:
                    if (
                        entry.from_state is RuntimeGenerationState.DRAINING
                        and entry.to_state is RuntimeGenerationState.ACTIVE
                    ):
                        validate_last_good_generation_transition(
                            entry.from_state,
                            entry.to_state,
                        )
                    else:
                        validate_regular_generation_transition(
                            entry.from_state,
                            entry.to_state,
                        )
                except ValueError as exc:
                    raise GenerationRepositoryDecodeError(
                        "runtime generation journal contains an illegal transition"
                    ) from exc
                if entry.created_at < previous.created_at:
                    raise GenerationRepositoryDecodeError(
                        "runtime generation journal timestamps are not monotonic"
                    )
            previous = entry
        last = entries[-1]
        if (
            last.generation_version != generation.version
            or last.to_state is not generation.state
            or last.created_at != generation.updated_at
        ):
            raise GenerationRepositoryDecodeError("journal tail does not match runtime generation")
        return entries

    def _get_generation_by_id(
        self,
        connection: sqlite3.Connection,
        generation_id: str,
    ) -> RuntimeGenerationV1 | None:
        row = connection.execute(
            """
            SELECT generation_id, schema_version, scope, release_digest, state, version,
                   created_at, activated_at, updated_at
            FROM runtime_generations
            WHERE generation_id = ?
            """,
            (generation_id,),
        ).fetchone()
        return _decode_generation(row) if row is not None else None

    def _require_generation(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str,
        generation_id: str,
    ) -> RuntimeGenerationV1:
        generation = self.get_generation(
            connection,
            scope=scope,
            generation_id=generation_id,
        )
        if generation is None:
            raise GenerationRepositoryConflict("runtime generation does not exist")
        return generation

    def _transition(
        self,
        connection: sqlite3.Connection,
        *,
        current: RuntimeGenerationV1,
        target_state: RuntimeGenerationState,
        expected_version: int,
        updated_at: datetime,
        last_good: bool,
    ) -> RuntimeGenerationV1:
        self.list_journal(connection, generation_id=current.generation_id)
        if current.version != expected_version:
            raise GenerationRepositoryConflict("runtime generation version conflict")
        if updated_at < current.updated_at:
            raise GenerationRepositoryConflict("runtime generation timestamps must be monotonic")
        validator = (
            validate_last_good_generation_transition
            if last_good
            else validate_regular_generation_transition
        )
        try:
            validator(current.state, target_state)
        except ValueError as exc:
            raise GenerationRepositoryConflict(str(exc)) from exc
        activated_at = current.activated_at
        if target_state is RuntimeGenerationState.ACTIVE and activated_at is None:
            activated_at = updated_at
        transitioned = RuntimeGenerationV1(
            generation_id=current.generation_id,
            scope=current.scope,
            release_digest=current.release_digest,
            state=target_state,
            version=current.version + 1,
            created_at=current.created_at,
            activated_at=activated_at,
            updated_at=updated_at,
        )
        changed = connection.execute(
            """
            UPDATE runtime_generations
            SET state = ?, version = ?, activated_at = ?, updated_at = ?
            WHERE generation_id = ? AND scope = ? AND version = ? AND state = ?
            """,
            (
                transitioned.state.value,
                transitioned.version,
                (
                    _timestamp(transitioned.activated_at)
                    if transitioned.activated_at is not None
                    else None
                ),
                _timestamp(transitioned.updated_at),
                transitioned.generation_id,
                transitioned.scope,
                expected_version,
                current.state.value,
            ),
        ).rowcount
        if changed != 1:
            raise GenerationRepositoryConflict("runtime generation CAS conflict")
        self._append_journal(
            connection,
            generation=transitioned,
            from_state=current.state,
        )
        return transitioned

    def _append_journal(
        self,
        connection: sqlite3.Connection,
        *,
        generation: RuntimeGenerationV1,
        from_state: RuntimeGenerationState | None,
    ) -> None:
        entry = _journal_material(
            generation_id=generation.generation_id,
            generation_version=generation.version,
            from_state=from_state,
            to_state=generation.state,
            created_at=generation.updated_at,
        )
        entry_hash = canonical_sha256(entry)
        journal_id = _journal_id(
            generation_id=generation.generation_id,
            generation_version=generation.version,
            entry_hash=entry_hash,
        )
        try:
            connection.execute(
                """
                INSERT INTO runtime_generation_journal (
                    journal_id, generation_id, generation_version, from_state, to_state,
                    entry_json, entry_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    journal_id,
                    generation.generation_id,
                    generation.version,
                    from_state.value if from_state is not None else None,
                    generation.state.value,
                    canonical_json_bytes(entry).decode("utf-8"),
                    entry_hash,
                    _timestamp(generation.updated_at),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise GenerationRepositoryConflict("runtime generation journal conflict") from exc


__all__ = [
    "GenerationActivationResult",
    "GenerationJournalEntry",
    "GenerationRepository",
    "GenerationRepositoryConflict",
    "GenerationRepositoryDecodeError",
    "GenerationRepositoryError",
    "GenerationRollbackResult",
]
