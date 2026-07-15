"""Connection-level persistence for strict durable RunState snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import ValidationError

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.run_state import RunStateV1
from tianshu.security.redact import redact_text


class RunStateRepositoryError(RuntimeError):
    """Base error for durable RunState persistence."""


class RunStateConflict(RunStateRepositoryError):
    """The RunState was missing, duplicated, or changed by another writer."""


class RunStateDecodeError(RunStateRepositoryError):
    """A persisted RunState row does not satisfy the v1 contract."""


class RunStateSecretError(RunStateRepositoryError):
    """A raw credential was detected in a snapshot before persistence."""


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "password",
    "private_key",
    "secret",
    "token",
)


def _is_safe_secret_reference(value: str) -> bool:
    return value.startswith("[REDACTED") or value.startswith(("settings:", "secret:"))


def _contains_secret(value: object, key: str | None = None) -> bool:
    if isinstance(value, str):
        if "[REDACTED" in redact_text(value) and "[REDACTED" not in value:
            return True
        normalized_key = key.lower().replace("-", "_") if key is not None else ""
        return (
            any(part in normalized_key for part in _SENSITIVE_KEY_PARTS)
            and bool(value)
            and not _is_safe_secret_reference(value)
        )
    if isinstance(value, Mapping):
        return any(_contains_secret(item, str(item_key)) for item_key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_secret(item) for item in value)
    return False


def _require_secret_free(state: RunStateV1) -> None:
    if _contains_secret(state.model_dump(mode="python")):
        raise RunStateSecretError("raw secret is not allowed in durable RunState")


def _require_memorial_binding(
    connection: sqlite3.Connection, memorial_id: str, edict_id: str
) -> None:
    row = connection.execute(
        "SELECT edict_id FROM memorials WHERE id = ?", (memorial_id,)
    ).fetchone()
    if row is None or str(row[0]) != edict_id:
        raise RunStateConflict("memorial does not belong to the RunState edict")


def _decode_state(row: sqlite3.Row) -> RunStateV1:
    if int(row["schema_version"]) != 1:
        raise RunStateDecodeError("unsupported RunState schema_version")
    try:
        continuation = json.loads(row["continuation_json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise RunStateDecodeError("continuation_json is invalid") from exc
    if not isinstance(continuation, dict):
        raise RunStateDecodeError("continuation_json is not an object")
    if continuation.get("kind") != row["continuation_kind"]:
        raise RunStateDecodeError("continuation kind does not match continuation_kind")
    data = {
        "memorial_id": row["memorial_id"],
        "edict_id": row["edict_id"],
        "schema_version": row["schema_version"],
        "phase": row["phase"],
        "continuation": continuation,
        "checkpoint_ref": row["checkpoint_ref"],
        "side_effect_cursor": row["side_effect_cursor"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    try:
        return RunStateV1.model_validate_json(json.dumps(data))
    except (ValidationError, TypeError, ValueError) as exc:
        raise RunStateDecodeError("persisted RunState violates the v1 contract") from exc


class RunStateRepository:
    """Stateless repository whose caller owns the SQLite transaction."""

    def load(self, connection: sqlite3.Connection, memorial_id: str) -> RunStateV1 | None:
        row = connection.execute(
            "SELECT * FROM run_states WHERE memorial_id = ?", (memorial_id,)
        ).fetchone()
        return _decode_state(row) if row is not None else None

    def create(self, connection: sqlite3.Connection, state: RunStateV1) -> RunStateV1:
        if state.version != 1:
            raise ValueError("new RunState must start at version 1")
        _require_memorial_binding(connection, state.memorial_id, state.edict_id)
        _require_secret_free(state)
        try:
            connection.execute(
                """
                INSERT INTO run_states (
                    memorial_id, edict_id, schema_version, phase, continuation_kind,
                    continuation_json, checkpoint_ref, side_effect_cursor,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.memorial_id,
                    state.edict_id,
                    state.schema_version,
                    state.phase.value,
                    state.continuation.kind,
                    canonical_json_bytes(state.continuation).decode("utf-8"),
                    state.checkpoint_ref,
                    state.side_effect_cursor,
                    state.version,
                    state.created_at.isoformat(),
                    state.updated_at.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise RunStateConflict("RunState identity conflict") from exc
        return state

    def compare_and_swap(
        self,
        connection: sqlite3.Connection,
        state: RunStateV1,
        *,
        expected_version: int,
    ) -> RunStateV1:
        if state.version != expected_version:
            raise ValueError("RunState input version must equal expected_version")
        current = connection.execute(
            """
            SELECT edict_id, schema_version, updated_at
            FROM run_states WHERE memorial_id = ?
            """,
            (state.memorial_id,),
        ).fetchone()
        if current is None:
            raise RunStateConflict("RunState compare-and-swap conflict")
        if str(current["edict_id"]) != state.edict_id:
            raise RunStateConflict("RunState edict_id is immutable")
        _require_memorial_binding(connection, state.memorial_id, state.edict_id)
        if int(current["schema_version"]) != state.schema_version:
            raise RunStateConflict("RunState schema_version is immutable")
        if state.updated_at < datetime.fromisoformat(str(current["updated_at"])):
            raise RunStateConflict("RunState updated_at must not move backwards")
        _require_secret_free(state)
        saved = state.model_copy(update={"version": expected_version + 1})
        cursor = connection.execute(
            """
            UPDATE run_states
            SET phase = ?, continuation_kind = ?,
                continuation_json = ?, checkpoint_ref = ?, side_effect_cursor = ?,
                version = ?, updated_at = ?
            WHERE memorial_id = ? AND version = ?
            """,
            (
                saved.phase.value,
                saved.continuation.kind,
                canonical_json_bytes(saved.continuation).decode("utf-8"),
                saved.checkpoint_ref,
                saved.side_effect_cursor,
                saved.version,
                saved.updated_at.isoformat(),
                saved.memorial_id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise RunStateConflict("RunState compare-and-swap conflict")
        return saved


__all__ = [
    "RunStateConflict",
    "RunStateDecodeError",
    "RunStateRepository",
    "RunStateRepositoryError",
    "RunStateSecretError",
]
