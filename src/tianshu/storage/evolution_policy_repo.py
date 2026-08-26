"""Caller-owned SQLite persistence for per-subject evolution policies."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import ValidationError

from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.evolution_policy import EvolutionPolicyMode, EvolutionPolicyV1

type EvolutionPolicyConflictReason = Literal[
    "evolution_policy_version_conflict",
    "evolution_policy_kind_conflict",
    "evolution_policy_promotion_in_progress",
]


class EvolutionPolicyRepositoryError(RuntimeError):
    """Base error for durable evolution policy access."""


class EvolutionPolicyConflict(EvolutionPolicyRepositoryError):
    """A policy CAS, immutable identity, or promotion exclusion conflicts."""

    def __init__(self, reason_code: EvolutionPolicyConflictReason) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class EvolutionPolicyDecodeError(EvolutionPolicyRepositoryError):
    """A durable policy row violates the strict V1 contract."""

    reason_code = "evolution_policy_decode_error"

    def __init__(self) -> None:
        super().__init__(self.reason_code)


def default_mode_for(kind: CandidateKind) -> EvolutionPolicyMode:
    """Preserve existing skill canaries while defaulting every other kind closed."""

    return "canary" if kind is CandidateKind.SKILL else "manual"


def _require_transaction(connection: sqlite3.Connection) -> None:
    if not connection.in_transaction:
        raise RuntimeError("evolution policy writes require a caller-owned transaction")


def _decode_timestamp(raw: object) -> datetime:
    if not isinstance(raw, str):
        raise EvolutionPolicyDecodeError
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise EvolutionPolicyDecodeError from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvolutionPolicyDecodeError
    normalized = value.astimezone(UTC)
    if raw != normalized.isoformat():
        raise EvolutionPolicyDecodeError
    return normalized


def _decode_policy(row: sqlite3.Row) -> EvolutionPolicyV1:
    if (
        not isinstance(row["subject_key"], str)
        or not isinstance(row["kind"], str)
        or not isinstance(row["mode"], str)
        or row["mode"] not in {"frozen", "manual", "canary"}
        or type(row["max_canary_basis_points"]) is not int
        or type(row["version"]) is not int
    ):
        raise EvolutionPolicyDecodeError
    try:
        policy = EvolutionPolicyV1(
            subject_key=row["subject_key"],
            kind=CandidateKind(row["kind"]),
            mode=cast(EvolutionPolicyMode, row["mode"]),
            max_canary_basis_points=row["max_canary_basis_points"],
            version=row["version"],
            updated_at=_decode_timestamp(row["updated_at"]),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, EvolutionPolicyDecodeError):
            raise
        raise EvolutionPolicyDecodeError from exc
    if (
        row["kind"] != policy.kind.value
        or row["mode"] != policy.mode
        or row["updated_at"] != policy.updated_at.isoformat()
    ):
        raise EvolutionPolicyDecodeError
    return policy


def _require_no_promote_in_progress(
    connection: sqlite3.Connection,
    *,
    subject_key: str,
) -> None:
    row = connection.execute(
        """
        SELECT 1
        FROM evolution_promotion_journal AS journal
        JOIN evolution_candidates AS candidate
          ON candidate.candidate_id = journal.candidate_id
        WHERE candidate.subject_key = ?
          AND journal.action IN ('start_canary', 'promote')
          AND journal.status IN ('intended', 'applied')
          AND NOT EXISTS (
              SELECT 1
              FROM evolution_promotion_journal AS terminal
              WHERE terminal.command_key = journal.command_key
                AND terminal.action = journal.action
                AND terminal.status IN ('completed', 'failed')
          )
        LIMIT 1
        """,
        (subject_key,),
    ).fetchone()
    if row is not None:
        raise EvolutionPolicyConflict("evolution_policy_promotion_in_progress")


class EvolutionPolicyRepository:
    """Stateless policy primitives whose caller owns every write transaction."""

    def get_policy(
        self,
        connection: sqlite3.Connection,
        subject_key: str,
    ) -> EvolutionPolicyV1 | None:
        row = connection.execute(
            """
            SELECT subject_key, kind, mode, max_canary_basis_points, version, updated_at
            FROM evolution_policies
            WHERE subject_key = ?
            """,
            (subject_key,),
        ).fetchone()
        return _decode_policy(row) if row is not None else None

    def upsert_policy(
        self,
        connection: sqlite3.Connection,
        policy: EvolutionPolicyV1,
        *,
        expected_version: int | None,
    ) -> EvolutionPolicyV1:
        """Insert version one or CAS-update exactly once without committing."""

        _require_transaction(connection)
        current = self.get_policy(connection, policy.subject_key)
        if current is None:
            if expected_version is not None or policy.version != 1:
                raise EvolutionPolicyConflict("evolution_policy_version_conflict")
            _require_no_promote_in_progress(connection, subject_key=policy.subject_key)
            try:
                connection.execute(
                    """
                    INSERT INTO evolution_policies (
                        subject_key, kind, mode, max_canary_basis_points, version, updated_at
                    ) VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        policy.subject_key,
                        policy.kind.value,
                        policy.mode,
                        policy.max_canary_basis_points,
                        policy.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise EvolutionPolicyConflict("evolution_policy_version_conflict") from exc
            durable = self.get_policy(connection, policy.subject_key)
            if durable is None:  # pragma: no cover - successful insert preserves the PK
                raise EvolutionPolicyConflict("evolution_policy_version_conflict")
            return durable

        if (
            expected_version is None
            or current.version != expected_version
            or policy.version != expected_version
        ):
            raise EvolutionPolicyConflict("evolution_policy_version_conflict")
        if current.kind is not policy.kind:
            raise EvolutionPolicyConflict("evolution_policy_kind_conflict")
        _require_no_promote_in_progress(connection, subject_key=policy.subject_key)

        saved = policy.model_copy(update={"version": expected_version + 1})
        cursor = connection.execute(
            """
            UPDATE evolution_policies
            SET mode = ?, max_canary_basis_points = ?, version = ?, updated_at = ?
            WHERE subject_key = ? AND kind = ? AND version = ?
            """,
            (
                saved.mode,
                saved.max_canary_basis_points,
                saved.version,
                saved.updated_at.isoformat(),
                saved.subject_key,
                saved.kind.value,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise EvolutionPolicyConflict("evolution_policy_version_conflict")
        durable = self.get_policy(connection, saved.subject_key)
        if durable is None:  # pragma: no cover - successful update preserves the PK
            raise EvolutionPolicyConflict("evolution_policy_version_conflict")
        return durable


__all__ = [
    "EvolutionPolicyConflict",
    "EvolutionPolicyConflictReason",
    "EvolutionPolicyDecodeError",
    "EvolutionPolicyRepository",
    "EvolutionPolicyRepositoryError",
    "default_mode_for",
]
