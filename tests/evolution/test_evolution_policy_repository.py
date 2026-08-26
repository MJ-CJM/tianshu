"""Strict model, defaulting, CAS, and promotion-exclusion policy contracts."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.evolution_policy import EvolutionPolicyMode, EvolutionPolicyV1
from tianshu.storage.evolution_policy_repo import (
    EvolutionPolicyConflict,
    EvolutionPolicyDecodeError,
    EvolutionPolicyRepository,
    default_mode_for,
)
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS

_NOW = datetime(2026, 8, 26, 0, 0, tzinfo=UTC)
_DIGEST = "a" * 64


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    apply_migrations(connection, MIGRATIONS)
    return connection


def _policy(
    *,
    subject_key: str = "skill:reviewer",
    kind: CandidateKind = CandidateKind.SKILL,
    mode: EvolutionPolicyMode = "canary",
    max_canary_basis_points: int = 100,
    version: int = 1,
    updated_at: datetime = _NOW,
) -> EvolutionPolicyV1:
    return EvolutionPolicyV1(
        subject_key=subject_key,
        kind=kind,
        mode=mode,
        max_canary_basis_points=max_canary_basis_points,
        version=version,
        updated_at=updated_at,
    )


def _insert_candidate(
    connection: sqlite3.Connection,
    *,
    candidate_id: str = "candidate-1",
    subject_key: str = "skill:reviewer",
) -> None:
    connection.execute(
        """
        INSERT INTO evolution_candidates (
            candidate_id, schema_version, kind, subject_key,
            provenance_json, provenance_hash, base_json, candidate_ref_json,
            diff_artifact_digest, evolution_contract_json, evolution_contract_hash,
            gate_snapshot_version, evidence_bundle_ids_json, routing_json,
            rollback_json, lifecycle, version, created_at, updated_at
        ) VALUES (?, 1, 'skill', ?, '{}', ?, '{}', '{}', ?, '{}', ?, 0, '[]',
                  NULL, '{}', 'ready', 1, ?, ?)
        """,
        (
            candidate_id,
            subject_key,
            _DIGEST,
            _DIGEST,
            _DIGEST,
            _NOW.isoformat(),
            _NOW.isoformat(),
        ),
    )


def _append_promote_journal(
    connection: sqlite3.Connection,
    *,
    status: str,
    action: str = "promote",
    command_key: str = "promote-command",
    candidate_id: str = "candidate-1",
) -> None:
    connection.execute(
        """
        INSERT INTO evolution_promotion_journal (
            promotion_journal_id, command_key, candidate_id, candidate_version,
            gate_snapshot_version, action, status, decision_request_id,
            entry_json, entry_hash, created_at
        ) VALUES (?, ?, ?, 1, 1, ?, ?, NULL, '{}', ?, ?)
        """,
        (
            f"journal-{command_key}-{status}",
            command_key,
            candidate_id,
            action,
            status,
            _DIGEST,
            _NOW.isoformat(),
        ),
    )


def _assert_conflict(
    reason_code: str,
    operation: Callable[[], object],
) -> None:
    with pytest.raises(EvolutionPolicyConflict) as captured:
        operation()
    assert captured.value.reason_code == reason_code
    assert str(captured.value) == reason_code


def test_policy_model_is_frozen_strict_and_auto_is_unrepresentable() -> None:
    policy = _policy()
    assert policy.model_config["frozen"] is True
    assert policy.updated_at == _NOW

    with pytest.raises(ValidationError):
        _policy(mode="auto")
    with pytest.raises(ValidationError):
        _policy(mode="canary", max_canary_basis_points=0)
    with pytest.raises(ValidationError):
        _policy(subject_key="   ")
    with pytest.raises(ValidationError):
        _policy(subject_key="x" * 513)
    with pytest.raises(ValidationError):
        _policy(updated_at=datetime(2026, 8, 26))
    with pytest.raises(ValidationError):
        EvolutionPolicyV1.model_validate(
            policy.model_dump() | {"kind": "skill", "unexpected": True}
        )


def test_policy_model_normalizes_timezone_to_utc() -> None:
    policy = _policy(
        updated_at=datetime(
            2026,
            8,
            26,
            8,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        )
    )
    assert policy.updated_at == _NOW


def test_default_mode_grandfathers_only_skills() -> None:
    assert default_mode_for(CandidateKind.SKILL) == "canary"
    assert {
        kind: default_mode_for(kind) for kind in CandidateKind if kind is not CandidateKind.SKILL
    } == {
        CandidateKind.MEMORY: "manual",
        CandidateKind.POLICY: "manual",
        CandidateKind.PERSONA: "manual",
        CandidateKind.CODE: "manual",
        CandidateKind.EXECUTOR: "manual",
    }


def test_get_missing_never_synthesizes_a_default_policy() -> None:
    connection = _connection()
    assert EvolutionPolicyRepository().get_policy(connection, "skill:missing") is None
    assert connection.execute("SELECT COUNT(*) FROM evolution_policies").fetchone()[0] == 0
    connection.close()


def test_upsert_requires_caller_transaction_and_never_commits() -> None:
    connection = _connection()
    repository = EvolutionPolicyRepository()

    with pytest.raises(RuntimeError, match="caller-owned transaction"):
        repository.upsert_policy(connection, _policy(), expected_version=None)

    connection.execute("BEGIN IMMEDIATE")
    assert repository.upsert_policy(connection, _policy(), expected_version=None).version == 1
    assert connection.in_transaction
    connection.rollback()
    assert repository.get_policy(connection, "skill:reviewer") is None
    connection.close()


def test_upsert_enforces_the_complete_strict_cas_matrix() -> None:
    connection = _connection()
    repository = EvolutionPolicyRepository()
    connection.execute("BEGIN IMMEDIATE")

    inserted = repository.upsert_policy(connection, _policy(), expected_version=None)
    assert inserted.version == 1
    _assert_conflict(
        "evolution_policy_version_conflict",
        lambda: repository.upsert_policy(connection, _policy(), expected_version=None),
    )
    _assert_conflict(
        "evolution_policy_version_conflict",
        lambda: repository.upsert_policy(
            connection,
            _policy(subject_key="skill:missing"),
            expected_version=1,
        ),
    )
    _assert_conflict(
        "evolution_policy_version_conflict",
        lambda: repository.upsert_policy(
            connection,
            _policy(subject_key="skill:new", version=2),
            expected_version=None,
        ),
    )

    updated = repository.upsert_policy(
        connection,
        _policy(mode="manual", max_canary_basis_points=0, updated_at=_NOW + timedelta(seconds=1)),
        expected_version=1,
    )
    assert updated.version == 2
    assert updated.mode == "manual"
    _assert_conflict(
        "evolution_policy_version_conflict",
        lambda: repository.upsert_policy(
            connection,
            _policy(mode="frozen", max_canary_basis_points=0),
            expected_version=1,
        ),
    )
    connection.rollback()
    connection.close()


def test_upsert_keeps_kind_immutable_after_an_exact_cas_read() -> None:
    connection = _connection()
    repository = EvolutionPolicyRepository()
    connection.execute("BEGIN IMMEDIATE")
    repository.upsert_policy(connection, _policy(), expected_version=None)

    _assert_conflict(
        "evolution_policy_kind_conflict",
        lambda: repository.upsert_policy(
            connection,
            _policy(kind=CandidateKind.POLICY),
            expected_version=1,
        ),
    )
    assert repository.get_policy(connection, "skill:reviewer") == _policy()
    connection.rollback()
    connection.close()


def test_get_policy_fails_closed_on_noncanonical_or_invalid_durable_data() -> None:
    connection = _connection()
    connection.execute(
        "INSERT INTO evolution_policies VALUES (?, 'skill', 'canary', 100, 1, ?)",
        ("skill:reviewer", "2026-08-26T00:00:00Z"),
    )

    with pytest.raises(EvolutionPolicyDecodeError) as captured:
        EvolutionPolicyRepository().get_policy(connection, "skill:reviewer")
    assert captured.value.reason_code == "evolution_policy_decode_error"
    connection.close()


@pytest.mark.parametrize("action", ["start_canary", "promote"])
@pytest.mark.parametrize("status", ["intended", "applied"])
def test_policy_mutation_is_blocked_while_command_is_in_progress(
    action: str,
    status: str,
) -> None:
    connection = _connection()
    repository = EvolutionPolicyRepository()
    connection.execute("BEGIN IMMEDIATE")
    _insert_candidate(connection)
    _append_promote_journal(connection, action=action, status=status)

    _assert_conflict(
        "evolution_policy_promotion_in_progress",
        lambda: repository.upsert_policy(connection, _policy(), expected_version=None),
    )
    connection.rollback()
    connection.close()


@pytest.mark.parametrize("action", ["start_canary", "promote"])
@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
def test_policy_mutation_is_unblocked_by_terminal_command_status(
    action: str,
    terminal_status: str,
) -> None:
    connection = _connection()
    repository = EvolutionPolicyRepository()
    connection.execute("BEGIN IMMEDIATE")
    _insert_candidate(connection)
    _append_promote_journal(connection, action=action, status="intended")
    _append_promote_journal(connection, action=action, status=terminal_status)

    assert repository.upsert_policy(connection, _policy(), expected_version=None).version == 1
    connection.rollback()
    connection.close()


def test_policy_guard_ignores_unresolved_promote_for_another_subject() -> None:
    connection = _connection()
    repository = EvolutionPolicyRepository()
    connection.execute("BEGIN IMMEDIATE")
    _insert_candidate(connection, candidate_id="candidate-other", subject_key="skill:other")
    _append_promote_journal(
        connection,
        status="intended",
        command_key="other-command",
        candidate_id="candidate-other",
    )
    assert repository.upsert_policy(connection, _policy(), expected_version=None).version == 1
    connection.rollback()
    connection.close()
