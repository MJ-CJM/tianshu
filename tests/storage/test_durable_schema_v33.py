"""V33 evolution policy schema and immutable migration replay contracts."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from tianshu.storage.migration_ledger import (
    Migration,
    MigrationExecutionError,
    MigrationStateError,
    apply_migrations,
)
from tianshu.storage.migrations import (
    _EVOLUTION_POLICIES_OBJECT_NAMES,
    _EVOLUTION_POLICIES_STATEMENTS,
    MIGRATIONS,
)

_CREATED_AT = "2026-08-26T00:00:00+00:00"
_FROZEN_V1_V32_MANIFEST = (
    32,
    "f185fca0feb284ca98fe8fb0dd4bd18d71d039a039996ea304a81b19236b5a3b",
)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _insert_candidate(
    connection: sqlite3.Connection,
    candidate_id: str,
    *,
    kind: str = "skill",
    subject_key: str = "skill:reviewer",
    lifecycle: str = "canary",
) -> None:
    digest = "a" * 64
    connection.execute(
        """
        INSERT INTO evolution_candidates (
            candidate_id, schema_version, kind, subject_key,
            provenance_json, provenance_hash, base_json, candidate_ref_json,
            diff_artifact_digest, evolution_contract_json, evolution_contract_hash,
            gate_snapshot_version, evidence_bundle_ids_json, routing_json,
            rollback_json, lifecycle, version, created_at, updated_at
        ) VALUES (?, 1, ?, ?, '{}', ?, '{}', '{}', ?, '{}', ?, 0, '[]', NULL,
                  '{}', ?, 1, ?, ?)
        """,
        (
            candidate_id,
            kind,
            subject_key,
            digest,
            digest,
            digest,
            lifecycle,
            _CREATED_AT,
            _CREATED_AT,
        ),
    )


def test_v33_appends_exact_policy_table_and_subject_canary_index() -> None:
    connection = _connection()

    assert apply_migrations(connection, MIGRATIONS[:32]) == tuple(range(1, 33))
    assert apply_migrations(connection, MIGRATIONS[:33]) == (33,)
    assert MIGRATIONS[32].name == "0033_evolution_policies"
    assert (
        MIGRATIONS[32].checksum
        == "725e801902e3e8e321a369164d3a5728adb40f96a8c77f2644820a6f69671fc7"
    )

    columns = connection.execute("PRAGMA table_info(evolution_policies)").fetchall()
    assert [row["name"] for row in columns] == [
        "subject_key",
        "kind",
        "mode",
        "max_canary_basis_points",
        "version",
        "updated_at",
    ]
    assert [row["pk"] for row in columns] == [1, 0, 0, 0, 0, 0]
    assert [row["notnull"] for row in columns] == [1, 1, 1, 1, 1, 1]

    expected = {
        name: " ".join(statement.split())
        for name, statement in zip(
            _EVOLUTION_POLICIES_OBJECT_NAMES,
            _EVOLUTION_POLICIES_STATEMENTS,
            strict=True,
        )
    }
    actual = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN (?, ?)",
            _EVOLUTION_POLICIES_OBJECT_NAMES,
        )
    }
    assert actual == expected
    assert connection.execute("SELECT COUNT(*) FROM evolution_policies").fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize(
    ("kind", "mode", "basis_points", "version"),
    [
        ("skill", "auto", 100, 1),
        ("skill", "canary", 0, 1),
        ("skill", "canary", 1_001, 1),
        ("skill", "manual", -1, 1),
        ("unknown", "manual", 0, 1),
        ("skill", "manual", 0, 0),
    ],
)
def test_v33_policy_checks_reject_invalid_rows(
    kind: str,
    mode: str,
    basis_points: int,
    version: int,
) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        connection.execute(
            "INSERT INTO evolution_policies VALUES (?, ?, ?, ?, ?, ?)",
            ("subject:test", kind, mode, basis_points, version, _CREATED_AT),
        )
    connection.close()


def test_v33_db_kind_superset_accepts_executor_and_canary_zero_is_only_non_canary() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)

    connection.execute(
        "INSERT INTO evolution_policies VALUES (?, 'executor', 'manual', 0, 1, ?)",
        ("executor:keqing:pi", _CREATED_AT),
    )
    connection.execute(
        "INSERT INTO evolution_policies VALUES (?, 'skill', 'frozen', 0, 1, ?)",
        ("skill:frozen", _CREATED_AT),
    )
    assert connection.execute("SELECT COUNT(*) FROM evolution_policies").fetchone()[0] == 2
    connection.close()


def test_v33_partial_unique_index_is_scoped_by_kind_and_subject() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    _insert_candidate(connection, "candidate-1")

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert_candidate(connection, "candidate-2")
    _insert_candidate(connection, "candidate-3", subject_key="skill:other")
    _insert_candidate(connection, "candidate-4", kind="policy")
    _insert_candidate(
        connection,
        "candidate-5",
        lifecycle="ready",
    )
    connection.close()


def test_v33_duplicate_preflight_fails_atomically_before_creating_objects() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:32])
    _insert_candidate(connection, "candidate-1")
    _insert_candidate(connection, "candidate-2")
    connection.commit()

    with pytest.raises(MigrationExecutionError, match="0033_evolution_policies"):
        apply_migrations(connection, MIGRATIONS)

    assert (
        connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=33").fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM evolution_candidates WHERE lifecycle='canary'"
        ).fetchone()[0]
        == 2
    )
    assert not {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN (?, ?)",
            _EVOLUTION_POLICIES_OBJECT_NAMES,
        )
    }
    connection.close()


def test_v33_exact_schema_replay_preserves_policy_row_and_rowid() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:32])
    for statement in _EVOLUTION_POLICIES_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO evolution_policies VALUES (?, 'skill', 'canary', 100, 1, ?)",
        ("skill:reviewer", _CREATED_AT),
    )
    connection.commit()
    before = tuple(connection.execute("SELECT rowid, * FROM evolution_policies").fetchone())

    assert apply_migrations(connection, MIGRATIONS) == (33,)
    after = tuple(connection.execute("SELECT rowid, * FROM evolution_policies").fetchone())
    assert after == before
    connection.close()


@pytest.mark.parametrize("mode", ["partial", "drifted"])
def test_v33_rejects_partial_or_drifted_owned_schema_atomically(mode: str) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:32])
    statement = _EVOLUTION_POLICIES_STATEMENTS[0]
    if mode == "drifted":
        statement = statement.replace(
            "mode IN ('frozen','manual','canary')",
            "mode IN ('frozen','manual','canary','auto')",
        )
    connection.execute(statement)
    connection.commit()

    with pytest.raises(MigrationExecutionError, match="0033_evolution_policies"):
        apply_migrations(connection, MIGRATIONS)

    assert (
        connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=33").fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name='evolution_policies'"
        ).fetchone()
        is not None
    )
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name='idx_evolution_candidates_subject_canary'"
        ).fetchone()
        is None
    )
    connection.close()


def test_v33_freezes_the_complete_v1_v32_triplet_prefix() -> None:
    prefix = MIGRATIONS[:32]
    payload = "\n".join(
        f"{migration.version}:{migration.name}:{migration.checksum}" for migration in prefix
    )
    assert (len(prefix), hashlib.sha256(payload.encode()).hexdigest()) == (_FROZEN_V1_V32_MANIFEST)


def test_applied_v33_checksum_drift_is_rejected_without_writes() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    before = tuple(connection.execute("SELECT * FROM schema_migrations").fetchall())
    drifted = (
        *MIGRATIONS[:-1],
        Migration(
            version=33,
            name="0033_evolution_policies",
            checksum="0" * 64,
            upgrade=MIGRATIONS[-1].upgrade,
        ),
    )

    with pytest.raises(MigrationStateError, match="checksum"):
        apply_migrations(connection, drifted)
    assert tuple(connection.execute("SELECT * FROM schema_migrations").fetchall()) == before
    connection.close()
