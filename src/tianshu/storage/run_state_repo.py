"""Connection-level persistence for strict durable RunState snapshots."""

from __future__ import annotations

import json
import sqlite3

from pydantic import ValidationError

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.control_center import ControlRunSummaryV1
from tianshu.models.run_state import (
    AgentContinuationV1,
    RunPhase,
    RunStateV1,
    agent_plan_continuation,
)
from tianshu.security.sensitive_payload import contains_raw_sensitive_payload
from tianshu.storage.correlation import correlation_for_memorial


class RunStateRepositoryError(RuntimeError):
    """Base error for durable RunState persistence."""


class RunStateConflict(RunStateRepositoryError):
    """The RunState was missing, duplicated, or changed by another writer."""


class RunStateDecodeError(RunStateRepositoryError):
    """A persisted RunState row does not satisfy the v1 contract."""


class RunStateSecretError(RunStateRepositoryError):
    """A raw credential was detected in a snapshot before persistence."""


def _require_secret_free(state: RunStateV1) -> None:
    if contains_raw_sensitive_payload(state.model_dump(mode="python")):
        raise RunStateSecretError("raw secret is not allowed in durable RunState")


def _require_valid_plan_lineage(state: RunStateV1) -> None:
    continuation = agent_plan_continuation(state.continuation)
    if continuation is None:
        return
    try:
        AgentContinuationV1.model_validate(continuation.model_dump(mode="python"))
    except (ValidationError, TypeError, ValueError) as exc:
        raise RunStateConflict("invalid RunState plan revision lineage") from exc


def _require_immutable_plan_lineage(current: RunStateV1, candidate: RunStateV1) -> None:
    current_plan = agent_plan_continuation(current.continuation)
    candidate_plan = agent_plan_continuation(candidate.continuation)
    current_lineage = current_plan.plan_revisions if current_plan is not None else ()
    candidate_lineage = candidate_plan.plan_revisions if candidate_plan is not None else ()
    if (
        len(candidate_lineage) < len(current_lineage)
        or len(candidate_lineage) > len(current_lineage) + 1
        or candidate_lineage[: len(current_lineage)] != current_lineage
    ):
        raise RunStateConflict("RunState plan revision lineage is immutable")


def _require_immutable_scheduled_event_binding(
    current: RunStateV1,
    candidate: RunStateV1,
) -> None:
    current_continuation = agent_plan_continuation(current.continuation)
    candidate_continuation = agent_plan_continuation(candidate.continuation)
    current_binding = (
        (
            current_continuation.scheduled_event_id,
            current_continuation.scheduled_event_hash,
        )
        if current_continuation is not None
        else (None, None)
    )
    candidate_binding = (
        (
            candidate_continuation.scheduled_event_id,
            candidate_continuation.scheduled_event_hash,
        )
        if candidate_continuation is not None
        else (None, None)
    )
    if candidate_binding != current_binding:
        raise RunStateConflict("RunState scheduled event binding is immutable")


def _require_decision_binding(state: RunStateV1) -> None:
    pending = state.continuation.pending_decision_id
    resolved = state.continuation.resolved_decision_id
    if resolved is not None and not resolved.strip():
        raise RunStateConflict("invalid RunState decision binding")
    if pending is not None and (not pending.strip() or resolved is not None):
        raise RunStateConflict("invalid RunState decision binding")
    if state.phase is RunPhase.WAITING_DECISION:
        if pending is None:
            raise RunStateConflict("invalid RunState decision binding")
    elif pending is not None:
        raise RunStateConflict("invalid RunState decision binding")


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

    def list_active_for_submitter(
        self,
        connection: sqlite3.Connection,
        *,
        submitter: str,
        limit: int,
    ) -> list[ControlRunSummaryV1]:
        if not submitter.strip():
            raise ValueError("submitter must not be blank")
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = connection.execute(
            """
            SELECT state.*, COALESCE(NULLIF(edict.title, ''), edict.goal) AS edict_title
            FROM run_states AS state
            JOIN edicts AS edict ON edict.id = state.edict_id
            WHERE edict.submitter = ?
              AND state.phase NOT IN ('completed', 'failed')
            ORDER BY state.updated_at DESC, state.memorial_id
            LIMIT ?
            """,
            (submitter, limit),
        ).fetchall()
        summaries: list[ControlRunSummaryV1] = []
        for row in rows:
            state = _decode_state(row)
            summaries.append(
                ControlRunSummaryV1(
                    edict_id=state.edict_id,
                    edict_title=str(row["edict_title"]),
                    memorial_id=state.memorial_id,
                    phase=state.phase,
                    updated_at=state.updated_at,
                )
            )
        return summaries

    @staticmethod
    def count_active_for_submitter(
        connection: sqlite3.Connection,
        *,
        submitter: str,
    ) -> int:
        if not submitter.strip():
            raise ValueError("submitter must not be blank")
        row = connection.execute(
            """
            SELECT COUNT(*)
            FROM run_states AS state
            JOIN edicts AS edict ON edict.id = state.edict_id
            WHERE edict.submitter = ?
              AND state.phase NOT IN ('completed', 'failed')
            """,
            (submitter,),
        ).fetchone()
        return int(row[0])

    def create(self, connection: sqlite3.Connection, state: RunStateV1) -> RunStateV1:
        if state.version != 1:
            raise ValueError("new RunState must start at version 1")
        _require_decision_binding(state)
        _require_valid_plan_lineage(state)
        _require_memorial_binding(connection, state.memorial_id, state.edict_id)
        _require_secret_free(state)
        correlation_id = correlation_for_memorial(connection, state.memorial_id)
        try:
            connection.execute(
                """
                INSERT INTO run_states (
                    memorial_id, edict_id, schema_version, phase, continuation_kind,
                    continuation_json, checkpoint_ref, side_effect_cursor,
                    version, created_at, updated_at, correlation_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    correlation_id,
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
        _require_decision_binding(state)
        current = self.load(connection, state.memorial_id)
        if current is None:
            raise RunStateConflict("RunState compare-and-swap conflict")
        _require_immutable_scheduled_event_binding(current, state)
        _require_valid_plan_lineage(state)
        if current.edict_id != state.edict_id:
            raise RunStateConflict("RunState edict_id is immutable")
        _require_memorial_binding(connection, state.memorial_id, state.edict_id)
        if current.schema_version != state.schema_version:
            raise RunStateConflict("RunState schema_version is immutable")
        if current.created_at != state.created_at:
            raise RunStateConflict("RunState created_at is immutable")
        _require_immutable_plan_lineage(current, state)
        if state.updated_at < current.updated_at:
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
        durable = self.load(connection, state.memorial_id)
        if durable is None:  # pragma: no cover - successful CAS preserves the primary key
            raise RunStateConflict("RunState disappeared after compare-and-swap")
        return durable

    def recover_terminal_identity(
        self,
        connection: sqlite3.Connection,
        state: RunStateV1,
        *,
        expected_version: int,
    ) -> RunStateV1:
        """CAS a corrupted RunState to the root's canonical failed identity."""
        if state.phase is not RunPhase.FAILED:
            raise ValueError("RunState identity recovery requires a failed state")
        if state.version != expected_version:
            raise ValueError("RunState input version must equal expected_version")
        _require_decision_binding(state)
        current = self.load(connection, state.memorial_id)
        if current is None or current.version != expected_version:
            raise RunStateConflict("RunState recovery compare-and-swap conflict")
        _require_immutable_scheduled_event_binding(current, state)
        _require_valid_plan_lineage(state)
        if current.edict_id == state.edict_id:
            raise RunStateConflict("RunState recovery requires an identity mismatch")
        _require_memorial_binding(connection, state.memorial_id, state.edict_id)
        if current.schema_version != state.schema_version:
            raise RunStateConflict("RunState schema_version is immutable")
        if current.created_at != state.created_at:
            raise RunStateConflict("RunState created_at is immutable")
        _require_immutable_plan_lineage(current, state)
        if state.updated_at < current.updated_at:
            raise RunStateConflict("RunState updated_at must not move backwards")
        _require_secret_free(state)
        saved = state.model_copy(update={"version": expected_version + 1})
        cursor = connection.execute(
            """
            UPDATE run_states
            SET edict_id = ?, phase = ?, continuation_kind = ?,
                continuation_json = ?, checkpoint_ref = ?, side_effect_cursor = ?,
                version = ?, updated_at = ?
            WHERE memorial_id = ? AND version = ?
            """,
            (
                saved.edict_id,
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
            raise RunStateConflict("RunState recovery compare-and-swap conflict")
        durable = self.load(connection, state.memorial_id)
        if durable is None:  # pragma: no cover - successful CAS preserves the primary key
            raise RunStateConflict("RunState disappeared after recovery")
        return durable


__all__ = [
    "RunStateConflict",
    "RunStateDecodeError",
    "RunStateRepository",
    "RunStateRepositoryError",
    "RunStateSecretError",
]
