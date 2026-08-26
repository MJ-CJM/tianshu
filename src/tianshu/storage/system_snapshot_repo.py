"""Connection-level persistence for immutable system snapshot bindings."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import ValidationError

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.events import make_event
from tianshu.models.governance_contract import RequestedGovernanceContractV1
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage.correlation import correlation_for_memorial
from tianshu.storage.evolution_repo import EvolutionAssignmentConflict
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.system_audit_repo import _append_system_audit_unlocked

type SystemSnapshotEventAction = Literal[
    "system_snapshot_drift",
    "system_snapshot_binding_failed",
    "skills_view_drift",
    "skills_view_binding_failed",
    "skills_view_binding_recovered",
]

_ACTOR_DIGEST = hashlib.sha256(b"tianshu.system-snapshot-repository").hexdigest()
_BINDING_SAVEPOINT = "system_snapshot_binding_write"
_EVENT_SAVEPOINT = "system_snapshot_event_write"


class SystemSnapshotRepositoryError(RuntimeError):
    """Base error for system snapshot persistence."""


class SystemSnapshotRepositoryConflict(SystemSnapshotRepositoryError):
    """A persisted snapshot identity conflicts with the requested snapshot."""


class SystemSnapshotRepositoryDecodeError(SystemSnapshotRepositoryError):
    """A durable snapshot or binding violates the v1 contract."""


@dataclass(frozen=True, slots=True)
class SystemBinding:
    memorial_id: str
    attempt_id: str
    snapshot: SystemSnapshotV1
    generation_ids: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class SystemBindingWriteResult:
    binding: SystemBinding
    inserted: bool
    drifted: bool
    previous_snapshot_digest: str | None


@dataclass(frozen=True, slots=True)
class AttemptGenerationBinding:
    memorial_id: str
    attempt_id: str
    generation_ids: tuple[str, ...] | None
    created_at: str

    @property
    def resolved(self) -> bool:
        return self.generation_ids is not None


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("system snapshot writes require a caller-owned transaction")


def _generation_ids_json(generation_ids: tuple[str, ...]) -> str:
    if not isinstance(generation_ids, tuple) or any(
        not isinstance(generation_id, str) for generation_id in generation_ids
    ):
        raise TypeError("generation_ids must be a tuple of strings")
    return json.dumps(list(generation_ids), ensure_ascii=False, separators=(",", ":"))


def _decode_generation_ids(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, str):
        raise SystemSnapshotRepositoryDecodeError("generation_ids_json is not text")
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SystemSnapshotRepositoryDecodeError("generation_ids_json is not valid JSON") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SystemSnapshotRepositoryDecodeError(
            "generation_ids_json must contain an array of strings"
        )
    generation_ids = tuple(value)
    if raw != _generation_ids_json(generation_ids):
        raise SystemSnapshotRepositoryDecodeError("generation_ids_json is not canonical JSON")
    return generation_ids


def _decode_snapshot(row: sqlite3.Row) -> SystemSnapshotV1:
    raw = row["components_json"]
    if not isinstance(raw, str):
        raise SystemSnapshotRepositoryDecodeError("components_json is not text")
    try:
        components = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SystemSnapshotRepositoryDecodeError("components_json is not valid JSON") from exc
    if not isinstance(components, dict):
        raise SystemSnapshotRepositoryDecodeError("components_json must contain an object")
    try:
        snapshot = SystemSnapshotV1.model_validate(
            {
                "schema_version": row["snapshot_schema_version"],
                "components": components,
                "digest": row["snapshot_digest"],
            }
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise SystemSnapshotRepositoryDecodeError(
            "persisted system snapshot violates the v1 contract"
        ) from exc
    if raw != canonical_json_bytes(snapshot.components).decode("utf-8"):
        raise SystemSnapshotRepositoryDecodeError("components_json is not canonical JSON")
    first_seen_at = row["first_seen_at"]
    if not isinstance(first_seen_at, str) or not first_seen_at.strip():
        raise SystemSnapshotRepositoryDecodeError("first_seen_at is invalid")
    return snapshot


def _decode_binding(row: sqlite3.Row) -> SystemBinding:
    snapshot = _decode_snapshot(row)
    memorial_id = row["memorial_id"]
    attempt_id = row["attempt_id"]
    created_at = row["created_at"]
    if not isinstance(memorial_id, str) or not memorial_id.strip():
        raise SystemSnapshotRepositoryDecodeError("memorial_id is invalid")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise SystemSnapshotRepositoryDecodeError("attempt_id is invalid")
    if not isinstance(created_at, str) or not created_at.strip():
        raise SystemSnapshotRepositoryDecodeError("created_at is invalid")
    return SystemBinding(
        memorial_id=memorial_id,
        attempt_id=attempt_id,
        snapshot=snapshot,
        generation_ids=_decode_generation_ids(row["generation_ids_json"]),
        created_at=created_at,
    )


def _decode_attempt_generation_binding(row: sqlite3.Row) -> AttemptGenerationBinding:
    memorial_id = row["memorial_id"]
    attempt_id = row["attempt_id"]
    state = row["state"]
    created_at = row["created_at"]
    if not isinstance(memorial_id, str) or not memorial_id.strip():
        raise SystemSnapshotRepositoryDecodeError("generation binding memorial_id is invalid")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise SystemSnapshotRepositoryDecodeError("generation binding attempt_id is invalid")
    if not isinstance(created_at, str) or not created_at.strip():
        raise SystemSnapshotRepositoryDecodeError("generation binding created_at is invalid")
    if state == "bound":
        generation_ids = _decode_generation_ids(row["generation_ids_json"])
    elif state == "unresolved" and row["generation_ids_json"] is None:
        generation_ids = None
    else:
        raise SystemSnapshotRepositoryDecodeError("generation binding state is invalid")
    return AttemptGenerationBinding(
        memorial_id=memorial_id,
        attempt_id=attempt_id,
        generation_ids=generation_ids,
        created_at=created_at,
    )


def _rollback_savepoint(connection: sqlite3.Connection, name: str) -> None:
    connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
    connection.execute(f"RELEASE SAVEPOINT {name}")


class SystemSnapshotRepository:
    """Stateless primitives whose caller owns the SQLite transaction."""

    def get_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot_digest: str,
    ) -> SystemSnapshotV1 | None:
        """Read one immutable snapshot by its content digest."""

        return self._get_snapshot(connection, snapshot_digest)

    def insert_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: SystemSnapshotV1,
    ) -> None:
        """Insert one immutable snapshot, accepting only an exact replay."""

        existing = self._get_snapshot(connection, snapshot.digest)
        if existing is not None:
            if existing != snapshot:
                raise SystemSnapshotRepositoryConflict("system snapshot identity is immutable")
            return
        components_json = canonical_json_bytes(snapshot.components).decode("utf-8")
        try:
            connection.execute(
                """
                INSERT INTO system_snapshots (
                    snapshot_digest, schema_version, components_json, first_seen_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    snapshot.digest,
                    snapshot.schema_version,
                    components_json,
                    datetime.now(UTC).isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            replay = self._get_snapshot(connection, snapshot.digest)
            if replay == snapshot:
                return
            raise SystemSnapshotRepositoryConflict("system snapshot identity conflict") from exc
        durable = self._get_snapshot(connection, snapshot.digest)
        if durable != snapshot:
            raise SystemSnapshotRepositoryConflict("system snapshot disappeared")

    def insert_generation_binding(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str,
        generation_ids: tuple[str, ...],
    ) -> AttemptGenerationBinding:
        """Persist an exact generation selection, including an explicit empty tuple."""

        _require_transaction(connection)
        if not memorial_id.strip() or not attempt_id.strip():
            raise ValueError("generation binding identities must be non-blank")
        if not isinstance(generation_ids, tuple) or any(
            not isinstance(generation_id, str) or not generation_id.strip()
            for generation_id in generation_ids
        ):
            raise TypeError("generation_ids must be a tuple of non-blank strings")
        if len(set(generation_ids)) != len(generation_ids):
            raise ValueError("generation binding contains duplicate ids")
        existing = self.get_generation_binding(
            connection,
            memorial_id=memorial_id,
            attempt_id=attempt_id,
        )
        if existing is not None:
            if existing.generation_ids != generation_ids:
                raise SystemSnapshotRepositoryConflict("run generation binding is immutable")
            return existing
        created_at = datetime.now(UTC).isoformat()
        try:
            connection.execute(
                """
                INSERT INTO run_generation_bindings (
                    memorial_id, attempt_id, state, generation_ids_json, created_at
                ) VALUES (?, ?, 'bound', ?, ?)
                """,
                (
                    memorial_id,
                    attempt_id,
                    _generation_ids_json(generation_ids),
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            replay = self.get_generation_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
            )
            if replay is not None and replay.generation_ids == generation_ids:
                return replay
            raise SystemSnapshotRepositoryConflict(
                "run generation binding identity conflict"
            ) from exc
        durable = self.get_generation_binding(
            connection,
            memorial_id=memorial_id,
            attempt_id=attempt_id,
        )
        if durable is None or durable.generation_ids != generation_ids:
            raise SystemSnapshotRepositoryConflict("run generation binding disappeared")
        return durable

    def get_generation_binding(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str,
    ) -> AttemptGenerationBinding | None:
        row = connection.execute(
            """
            SELECT memorial_id, attempt_id, state, generation_ids_json, created_at
            FROM run_generation_bindings
            WHERE memorial_id=? AND attempt_id=?
            """,
            (memorial_id, attempt_id),
        ).fetchone()
        return _decode_attempt_generation_binding(row) if row is not None else None

    def inherit_generation_binding(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        source_attempt_id: str,
        target_attempt_id: str,
    ) -> AttemptGenerationBinding:
        """Copy one attempt's exact selection to its retry, failing closed if absent."""

        _require_transaction(connection)
        if (
            not memorial_id.strip()
            or not source_attempt_id.strip()
            or not target_attempt_id.strip()
        ):
            raise ValueError("generation binding identities must be non-blank")
        source = self.get_generation_binding(
            connection,
            memorial_id=memorial_id,
            attempt_id=source_attempt_id,
        )
        legacy_system_binding = (
            self.get_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=source_attempt_id,
            )
            if source is None
            else None
        )
        existing = self.get_generation_binding(
            connection,
            memorial_id=memorial_id,
            attempt_id=target_attempt_id,
        )
        expected_generation_ids = (
            source.generation_ids
            if source is not None
            else legacy_system_binding.generation_ids
            if legacy_system_binding is not None
            else self._legacy_retry_generation_ids(connection, memorial_id)
        )
        if existing is not None:
            if existing.generation_ids != expected_generation_ids:
                raise SystemSnapshotRepositoryConflict("retry generation binding is immutable")
            return existing
        created_at = datetime.now(UTC).isoformat()
        state = "bound" if expected_generation_ids is not None else "unresolved"
        generation_ids_json = (
            _generation_ids_json(expected_generation_ids)
            if expected_generation_ids is not None
            else None
        )
        try:
            connection.execute(
                """
                INSERT INTO run_generation_bindings (
                    memorial_id, attempt_id, state, generation_ids_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    memorial_id,
                    target_attempt_id,
                    state,
                    generation_ids_json,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            replay = self.get_generation_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=target_attempt_id,
            )
            if replay is not None and replay.generation_ids == expected_generation_ids:
                return replay
            raise SystemSnapshotRepositoryConflict(
                "retry generation binding identity conflict"
            ) from exc
        durable = self.get_generation_binding(
            connection,
            memorial_id=memorial_id,
            attempt_id=target_attempt_id,
        )
        if durable is None or durable.generation_ids != expected_generation_ids:
            raise SystemSnapshotRepositoryConflict("retry generation binding disappeared")
        return durable

    @staticmethod
    def _legacy_retry_generation_ids(
        connection: sqlite3.Connection,
        memorial_id: str,
    ) -> tuple[str, ...] | None:
        row = connection.execute(
            """
            SELECT memorial.runtime_override_json,
                   requested.contract_json, requested.contract_hash
            FROM memorials AS memorial
            LEFT JOIN requested_governance_contracts AS requested
              ON requested.edict_id = memorial.edict_id
            WHERE memorial.id = ?
            """,
            (memorial_id,),
        ).fetchone()
        if row is None or row["contract_json"] is None or row["contract_hash"] is None:
            raise SystemSnapshotRepositoryDecodeError(
                "retry requested governance contract is unavailable"
            )
        try:
            contract = RequestedGovernanceContractV1.model_validate_json(row["contract_json"])
        except (ValidationError, TypeError, ValueError) as exc:
            raise SystemSnapshotRepositoryDecodeError(
                "retry requested governance contract is invalid"
            ) from exc
        if contract.content_hash != row["contract_hash"]:
            raise SystemSnapshotRepositoryDecodeError(
                "retry requested governance contract hash mismatch"
            )
        adapter_id = contract.executor.adapter_id
        raw_override = row["runtime_override_json"]
        if raw_override is not None:
            try:
                runtime_override = json.loads(raw_override)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise SystemSnapshotRepositoryDecodeError(
                    "retry runtime override is invalid"
                ) from exc
            if not isinstance(runtime_override, dict):
                raise SystemSnapshotRepositoryDecodeError(
                    "retry runtime override must contain an object"
                )
            override_executor = runtime_override.get("executor")
            if override_executor is not None:
                if not isinstance(override_executor, str) or not override_executor.strip():
                    raise SystemSnapshotRepositoryDecodeError(
                        "retry runtime executor override is invalid"
                    )
                adapter_id = override_executor
        return None if adapter_id == "keqing:pi" else ()

    def insert_binding(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str,
        snapshot: SystemSnapshotV1,
        generation_ids: tuple[str, ...] = (),
    ) -> SystemBindingWriteResult:
        """Atomically bind a snapshot to one attempt inside the caller transaction."""

        _require_transaction(connection)
        generation_ids_json = _generation_ids_json(generation_ids)
        connection.execute(f"SAVEPOINT {_BINDING_SAVEPOINT}")
        try:
            self.insert_snapshot(connection, snapshot)
            previous = self.get_last_binding(connection, memorial_id)
            existing = self.get_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
            )
            if existing is not None:
                if existing.snapshot != snapshot or existing.generation_ids != generation_ids:
                    raise EvolutionAssignmentConflict("run system binding is immutable")
                result = SystemBindingWriteResult(
                    binding=existing,
                    inserted=False,
                    drifted=False,
                    previous_snapshot_digest=None,
                )
            else:
                created_at = datetime.now(UTC).isoformat()
                inserted = True
                try:
                    connection.execute(
                        """
                        INSERT INTO run_system_bindings (
                            memorial_id, attempt_id, snapshot_digest,
                            generation_ids_json, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            memorial_id,
                            attempt_id,
                            snapshot.digest,
                            generation_ids_json,
                            created_at,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    replay = self.get_binding(
                        connection,
                        memorial_id=memorial_id,
                        attempt_id=attempt_id,
                    )
                    if (
                        replay is None
                        or replay.snapshot != snapshot
                        or replay.generation_ids != generation_ids
                    ):
                        raise EvolutionAssignmentConflict(
                            "run system binding identity conflict"
                        ) from exc
                    inserted = False
                durable = self.get_binding(
                    connection,
                    memorial_id=memorial_id,
                    attempt_id=attempt_id,
                )
                if durable is None:
                    raise EvolutionAssignmentConflict("run system binding disappeared")
                previous_digest = previous.snapshot.digest if previous is not None else None
                drifted = bool(
                    inserted and previous_digest is not None and previous_digest != snapshot.digest
                )
                result = SystemBindingWriteResult(
                    binding=durable,
                    inserted=inserted,
                    drifted=drifted,
                    previous_snapshot_digest=previous_digest,
                )
            connection.execute(f"RELEASE SAVEPOINT {_BINDING_SAVEPOINT}")
        except Exception:
            _rollback_savepoint(connection, _BINDING_SAVEPOINT)
            raise

        if result.drifted:
            self.record_event(
                connection,
                action="system_snapshot_drift",
                memorial_id=memorial_id,
                attempt_id=attempt_id,
                snapshot_digest=snapshot.digest,
                previous_snapshot_digest=result.previous_snapshot_digest,
            )
        return result

    def try_insert_binding(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str,
        snapshot: SystemSnapshotV1,
        generation_ids: tuple[str, ...] = (),
    ) -> SystemBindingWriteResult | None:
        """Best-effort shadow write; failures never escape into run execution."""

        try:
            return self.insert_binding(
                connection,
                memorial_id=memorial_id,
                attempt_id=attempt_id,
                snapshot=snapshot,
                generation_ids=generation_ids,
            )
        except Exception:
            self.record_event(
                connection,
                action="system_snapshot_binding_failed",
                memorial_id=memorial_id,
                attempt_id=attempt_id,
                snapshot_digest=snapshot.digest,
            )
            return None

    def record_event(
        self,
        connection: sqlite3.Connection,
        *,
        action: SystemSnapshotEventAction,
        memorial_id: str,
        attempt_id: str,
        snapshot_digest: str | None = None,
        previous_snapshot_digest: str | None = None,
    ) -> bool:
        """Best-effort atomic audit/outbox append with no error-detail leakage."""

        if action not in {
            "system_snapshot_drift",
            "system_snapshot_binding_failed",
            "skills_view_drift",
            "skills_view_binding_failed",
            "skills_view_binding_recovered",
        }:
            return False
        if not connection.in_transaction:
            return False
        try:
            connection.execute(f"SAVEPOINT {_EVENT_SAVEPOINT}")
            correlation_id = correlation_for_memorial(connection, memorial_id)
            subject_digest = (
                snapshot_digest
                or hashlib.sha256(f"{memorial_id}:{attempt_id}".encode()).hexdigest()
            )
            _append_system_audit_unlocked(
                connection,
                AppendSystemAuditRequest(
                    correlation_id=correlation_id,
                    actor_digest=_ACTOR_DIGEST,
                    action=action,
                    outcome=(
                        "succeeded"
                        if action
                        in {
                            "system_snapshot_drift",
                            "skills_view_drift",
                            "skills_view_binding_recovered",
                        }
                        else "failed"
                    ),
                    reason_code=action,
                    subject_kind=(
                        "skills_view"
                        if action
                        in {
                            "skills_view_drift",
                            "skills_view_binding_failed",
                            "skills_view_binding_recovered",
                        }
                        else "system_snapshot_binding"
                    ),
                    subject_digest=subject_digest,
                ),
            )
            payload: dict[str, object] = {
                "attempt_id": attempt_id,
                "correlation_id": correlation_id,
            }
            if action in {
                "skills_view_drift",
                "skills_view_binding_failed",
                "skills_view_binding_recovered",
            }:
                if snapshot_digest is not None:
                    payload["skills_digest"] = snapshot_digest
                if previous_snapshot_digest is not None:
                    payload["previous_skills_digest"] = previous_snapshot_digest
            else:
                if snapshot_digest is not None:
                    payload["snapshot_digest"] = snapshot_digest
                if previous_snapshot_digest is not None:
                    payload["previous_snapshot_digest"] = previous_snapshot_digest
            OutboxRepository().add(
                connection,
                make_event(
                    event_type=action,
                    memorial_id=memorial_id,
                    producer="system_snapshot_repository",
                    payload=payload,
                ),
            )
            connection.execute(f"RELEASE SAVEPOINT {_EVENT_SAVEPOINT}")
        except Exception:
            with suppress(Exception):
                _rollback_savepoint(connection, _EVENT_SAVEPOINT)
            return False
        return True

    def get_last_binding(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
    ) -> SystemBinding | None:
        row = connection.execute(
            self._binding_select()
            + " WHERE b.memorial_id = ? ORDER BY b.created_at DESC, b.attempt_id DESC LIMIT 1",
            (memorial_id,),
        ).fetchone()
        return _decode_binding(row) if row is not None else None

    def get_continuity_generation_ids(
        self,
        connection: sqlite3.Connection,
        memorial_id: str,
    ) -> tuple[str, ...] | None:
        """Return generation ids from the nearest applicable parent binding.

        An explicit one-turn override from generated Pi to a static executor writes
        an exact empty binding for that turn. It does not erase the dormant Pi
        continuity promised to later follow-ups, so traversal skips only that
        strictly identifiable scope gap. Legacy empty bindings remain boundaries.
        """

        if not isinstance(memorial_id, str) or not memorial_id.strip():
            raise ValueError("memorial_id must be non-blank")
        current = memorial_id
        seen: set[str] = set()
        while True:
            if current in seen:
                raise SystemSnapshotRepositoryDecodeError("memorial parent chain contains a cycle")
            seen.add(current)
            row = connection.execute(
                """
                SELECT memorial.parent_memorial_id, memorial.runtime_override_json,
                       requested.contract_json, requested.contract_hash
                FROM memorials AS memorial
                LEFT JOIN requested_governance_contracts AS requested
                  ON requested.edict_id = memorial.edict_id
                WHERE memorial.id = ?
                """,
                (current,),
            ).fetchone()
            if row is None:
                raise SystemSnapshotRepositoryDecodeError(
                    "memorial parent chain references a missing memorial"
                )
            generation_binding_row = connection.execute(
                """
                SELECT memorial_id, attempt_id, state, generation_ids_json, created_at
                FROM run_generation_bindings
                WHERE memorial_id = ?
                ORDER BY created_at DESC, attempt_id DESC
                LIMIT 1
                """,
                (current,),
            ).fetchone()
            system_binding_row = connection.execute(
                """
                SELECT attempt_id, generation_ids_json
                FROM run_system_bindings
                WHERE memorial_id = ?
                ORDER BY created_at DESC, attempt_id DESC
                LIMIT 1
                """,
                (current,),
            ).fetchone()
            if generation_binding_row is not None:
                generation_binding = _decode_attempt_generation_binding(generation_binding_row)
                if not generation_binding.resolved:
                    raise SystemSnapshotRepositoryDecodeError(
                        "continuity generation binding is unresolved"
                    )
                generation_ids = generation_binding.generation_ids
                assert generation_ids is not None
                exact_system_binding = connection.execute(
                    """
                    SELECT generation_ids_json
                    FROM run_system_bindings
                    WHERE memorial_id = ? AND attempt_id = ?
                    """,
                    (current, generation_binding.attempt_id),
                ).fetchone()
                if (
                    exact_system_binding is not None
                    and _decode_generation_ids(exact_system_binding["generation_ids_json"])
                    != generation_ids
                ):
                    raise SystemSnapshotRepositoryDecodeError(
                        "generation and system continuity bindings conflict"
                    )
                if generation_ids or not self._is_transient_pi_scope_gap(row):
                    return generation_ids
            elif system_binding_row is not None:
                generation_ids = _decode_generation_ids(system_binding_row["generation_ids_json"])
                if generation_ids or not self._is_transient_pi_scope_gap(row):
                    return generation_ids
            parent = row["parent_memorial_id"]
            if parent is None:
                return None
            if not isinstance(parent, str) or not parent.strip():
                raise SystemSnapshotRepositoryDecodeError("memorial parent identity is invalid")
            current = parent

    @staticmethod
    def _is_transient_pi_scope_gap(row: sqlite3.Row) -> bool:
        if row["parent_memorial_id"] is None or row["runtime_override_json"] is None:
            return False
        try:
            runtime_override = json.loads(row["runtime_override_json"])
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise SystemSnapshotRepositoryDecodeError("runtime override is invalid") from exc
        if not isinstance(runtime_override, dict):
            raise SystemSnapshotRepositoryDecodeError("runtime override must contain an object")
        override_executor = runtime_override.get("executor")
        if override_executor is None:
            return False
        if not isinstance(override_executor, str) or not override_executor.strip():
            raise SystemSnapshotRepositoryDecodeError("runtime executor override is invalid")
        raw_contract = row["contract_json"]
        contract_hash = row["contract_hash"]
        if not isinstance(raw_contract, str) or not isinstance(contract_hash, str):
            raise SystemSnapshotRepositoryDecodeError("requested governance contract is missing")
        try:
            contract = RequestedGovernanceContractV1.model_validate_json(raw_contract)
        except ValidationError as exc:
            raise SystemSnapshotRepositoryDecodeError(
                "requested governance contract is invalid"
            ) from exc
        if contract.content_hash != contract_hash:
            raise SystemSnapshotRepositoryDecodeError("requested governance contract hash mismatch")
        return contract.executor.adapter_id == "keqing:pi" and override_executor != "keqing:pi"

    def get_binding(
        self,
        connection: sqlite3.Connection,
        *,
        memorial_id: str,
        attempt_id: str,
    ) -> SystemBinding | None:
        row = connection.execute(
            self._binding_select() + " WHERE b.memorial_id = ? AND b.attempt_id = ?",
            (memorial_id, attempt_id),
        ).fetchone()
        return _decode_binding(row) if row is not None else None

    @staticmethod
    def _binding_select() -> str:
        return """
            SELECT b.memorial_id, b.attempt_id, b.snapshot_digest,
                   b.generation_ids_json, b.created_at,
                   s.schema_version AS snapshot_schema_version,
                   s.components_json, s.first_seen_at
            FROM run_system_bindings AS b
            LEFT JOIN system_snapshots AS s
              ON s.snapshot_digest = b.snapshot_digest
        """

    @staticmethod
    def _get_snapshot(
        connection: sqlite3.Connection,
        snapshot_digest: str,
    ) -> SystemSnapshotV1 | None:
        row = connection.execute(
            """
            SELECT snapshot_digest, schema_version AS snapshot_schema_version,
                   components_json, first_seen_at
            FROM system_snapshots
            WHERE snapshot_digest = ?
            """,
            (snapshot_digest,),
        ).fetchone()
        return _decode_snapshot(cast(sqlite3.Row, row)) if row is not None else None


__all__ = [
    "AttemptGenerationBinding",
    "SystemBinding",
    "SystemBindingWriteResult",
    "SystemSnapshotRepository",
    "SystemSnapshotRepositoryConflict",
    "SystemSnapshotRepositoryDecodeError",
    "SystemSnapshotRepositoryError",
]
