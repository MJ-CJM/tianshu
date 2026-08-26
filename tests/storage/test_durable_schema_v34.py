"""V34 per-subject assignment schema and immutable replay contracts."""

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
    _RUN_SUBJECT_ASSIGNMENTS_OBJECT_NAMES,
    _RUN_SUBJECT_ASSIGNMENTS_STATEMENTS,
    MIGRATIONS,
)

_CREATED_AT = "2026-08-26T00:00:00+00:00"
_DIGEST = "a" * 64
_FROZEN_V1_V33_MANIFEST = (
    33,
    "f621a792706c2626f4aa5c2e2fa4928ae6cdf5f763e86d75a6f93581c19e5247",
)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _seed_memorial(connection: sqlite3.Connection, memorial_id: str = "memorial-1") -> None:
    connection.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES ('edict-1', 'test', ?)",
        (_CREATED_AT,),
    )
    connection.execute(
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, 'edict-1', 'pending', ?)",
        (memorial_id, _CREATED_AT),
    )


def _seed_candidate(connection: sqlite3.Connection, candidate_id: str = "candidate-1") -> None:
    connection.execute(
        """
        INSERT INTO evolution_candidates (
            candidate_id, schema_version, kind, subject_key,
            provenance_json, provenance_hash, base_json, candidate_ref_json,
            diff_artifact_digest, evolution_contract_json, evolution_contract_hash,
            gate_snapshot_version, evidence_bundle_ids_json, routing_json,
            rollback_json, lifecycle, version, created_at, updated_at
        ) VALUES (?, 1, 'skill', 'skill:reviewer', '{}', ?, '{}', '{}', ?, '{}', ?,
                  0, '[]', NULL, '{}', 'proposed', 1, ?, ?)
        """,
        (candidate_id, _DIGEST, _DIGEST, _DIGEST, _CREATED_AT, _CREATED_AT),
    )


def _insert_assignment_row(
    connection: sqlite3.Connection,
    *,
    assignment_id: str = "subject-assignment-1",
    kind: str = "skill",
    subject_key: str = "skill:reviewer",
    candidate_id: str | None = None,
    routing_version: int = 1,
    bucket: int = 42,
    overlay_digest: str = _DIGEST,
    assignment_hash: str = _DIGEST,
    assignment_set_hash: str = _DIGEST,
    assignment_set_size: int = 1,
) -> None:
    connection.execute(
        """
        INSERT INTO run_subject_assignments (
            assignment_id, memorial_id, kind, subject_key, candidate_id,
            routing_version, bucket, champion_ref_json, selected_ref_json,
            overlay_digest, assignment_json, assignment_hash,
            assignment_set_hash, assignment_set_size, created_at
        ) VALUES (?, 'memorial-1', ?, ?, ?, ?, ?, '{}', '{}', ?, '{}', ?, ?, ?, ?)
        """,
        (
            assignment_id,
            kind,
            subject_key,
            candidate_id,
            routing_version,
            bucket,
            overlay_digest,
            assignment_hash,
            assignment_set_hash,
            assignment_set_size,
            _CREATED_AT,
        ),
    )


def _unique_column_sets(connection: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    unique: set[tuple[str, ...]] = set()
    for index in connection.execute(f"PRAGMA index_list({table})"):
        if int(index["unique"]) != 1:
            continue
        unique.add(
            tuple(
                str(row["name"])
                for row in connection.execute(f"PRAGMA index_info({index['name']})")
            )
        )
    return unique


def test_v34_appends_exact_subject_assignment_objects_without_backfill() -> None:
    connection = _connection()
    assert apply_migrations(connection, MIGRATIONS[:33]) == tuple(range(1, 34))
    _seed_memorial(connection)
    connection.execute(
        """
        INSERT INTO run_evolution_assignments (
            assignment_id, memorial_id, candidate_id, routing_version, bucket,
            champion_ref_json, selected_ref_json, overlay_digest,
            assignment_json, assignment_hash, created_at
        ) VALUES ('legacy-assignment-1', 'memorial-1', NULL, 1, 0,
                  '{}', '{}', ?, '{}', ?, ?)
        """,
        (_DIGEST, _DIGEST, _CREATED_AT),
    )
    connection.commit()

    assert apply_migrations(connection, MIGRATIONS) == (34,)
    assert MIGRATIONS[33].name == "0034_run_subject_assignments"
    assert (
        MIGRATIONS[33].checksum
        == "2ef0237b22f47310bf1f5d48d20c0262998bba960f1c9418687e54860dd2172f"
    )
    columns = connection.execute("PRAGMA table_info(run_subject_assignments)").fetchall()
    assert [row["name"] for row in columns] == [
        "assignment_id",
        "memorial_id",
        "kind",
        "subject_key",
        "candidate_id",
        "routing_version",
        "bucket",
        "champion_ref_json",
        "selected_ref_json",
        "overlay_digest",
        "assignment_json",
        "assignment_hash",
        "assignment_set_hash",
        "assignment_set_size",
        "created_at",
    ]
    assert [row["pk"] for row in columns] == [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    assert [row["notnull"] for row in columns] == [
        0,
        1,
        1,
        1,
        0,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
        1,
    ]
    assert _unique_column_sets(connection, "run_subject_assignments") == {
        ("assignment_id",),
        ("memorial_id", "kind", "subject_key"),
    }
    assert {
        (str(row["from"]), str(row["table"]), str(row["to"]), str(row["on_delete"]))
        for row in connection.execute("PRAGMA foreign_key_list(run_subject_assignments)")
    } == {
        ("candidate_id", "evolution_candidates", "candidate_id", "RESTRICT"),
        ("memorial_id", "memorials", "id", "RESTRICT"),
    }
    expected = {
        name: " ".join(statement.split())
        for name, statement in zip(
            _RUN_SUBJECT_ASSIGNMENTS_OBJECT_NAMES,
            _RUN_SUBJECT_ASSIGNMENTS_STATEMENTS,
            strict=True,
        )
    }
    actual = {
        str(row["name"]): " ".join(str(row["sql"]).split())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN (?, ?, ?, ?)",
            _RUN_SUBJECT_ASSIGNMENTS_OBJECT_NAMES,
        )
    }
    assert actual == expected
    assert connection.execute("SELECT COUNT(*) FROM run_subject_assignments").fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize(
    (
        "kind",
        "subject_key",
        "routing_version",
        "bucket",
        "overlay_digest",
        "assignment_hash",
        "assignment_set_hash",
        "assignment_set_size",
    ),
    [
        ("unknown", "skill:reviewer", 1, 0, _DIGEST, _DIGEST, _DIGEST, 1),
        ("skill", " ", 1, 0, _DIGEST, _DIGEST, _DIGEST, 1),
        ("skill", "skill:reviewer", 0, 0, _DIGEST, _DIGEST, _DIGEST, 1),
        ("skill", "skill:reviewer", 1, -1, _DIGEST, _DIGEST, _DIGEST, 1),
        ("skill", "skill:reviewer", 1, 10_000, _DIGEST, _DIGEST, _DIGEST, 1),
        ("skill", "skill:reviewer", 1, 0, "a" * 63, _DIGEST, _DIGEST, 1),
        ("skill", "skill:reviewer", 1, 0, _DIGEST, "a" * 63, _DIGEST, 1),
        ("skill", "skill:reviewer", 1, 0, _DIGEST, _DIGEST, "a" * 63, 1),
        ("skill", "skill:reviewer", 1, 0, _DIGEST, _DIGEST, _DIGEST, 0),
        ("skill", "skill:reviewer", 1, 0, _DIGEST, _DIGEST, _DIGEST, 65),
    ],
)
def test_v34_checks_reject_invalid_rows(
    kind: str,
    subject_key: str,
    routing_version: int,
    bucket: int,
    overlay_digest: str,
    assignment_hash: str,
    assignment_set_hash: str,
    assignment_set_size: int,
) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    _seed_memorial(connection)

    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _insert_assignment_row(
            connection,
            kind=kind,
            subject_key=subject_key,
            routing_version=routing_version,
            bucket=bucket,
            overlay_digest=overlay_digest,
            assignment_hash=assignment_hash,
            assignment_set_hash=assignment_set_hash,
            assignment_set_size=assignment_set_size,
        )
    connection.close()


def test_v34_kind_superset_accepts_executor_without_candidate_row() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    _seed_memorial(connection)

    _insert_assignment_row(
        connection,
        kind="executor",
        subject_key="executor:keqing:pi",
    )
    assert connection.execute("SELECT kind FROM run_subject_assignments").fetchone()[0] == (
        "executor"
    )
    connection.close()


def test_v34_update_is_always_blocked_and_delete_only_protects_governed_rows() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    _seed_memorial(connection)
    _seed_candidate(connection)
    _insert_assignment_row(
        connection,
        assignment_id="placeholder",
        assignment_set_size=2,
    )
    _insert_assignment_row(
        connection,
        assignment_id="governed",
        subject_key="skill:other",
        candidate_id="candidate-1",
        assignment_set_size=2,
    )

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute(
            "UPDATE run_subject_assignments SET bucket=bucket WHERE assignment_id='placeholder'"
        )
    connection.execute("DELETE FROM run_subject_assignments WHERE assignment_id='placeholder'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        connection.execute("DELETE FROM run_subject_assignments WHERE assignment_id='governed'")
    connection.close()


def test_v34_seals_member_count_and_set_identity_after_the_declared_rows() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    _seed_memorial(connection)
    _insert_assignment_row(
        connection,
        assignment_id="first",
        subject_key="skill:first",
        assignment_set_size=2,
    )

    with pytest.raises(sqlite3.IntegrityError, match="sealed"):
        _insert_assignment_row(
            connection,
            assignment_id="wrong-set",
            subject_key="skill:wrong-set",
            assignment_set_hash="b" * 64,
            assignment_set_size=2,
        )

    _insert_assignment_row(
        connection,
        assignment_id="second",
        subject_key="skill:second",
        assignment_set_size=2,
    )
    with pytest.raises(sqlite3.IntegrityError, match="sealed"):
        _insert_assignment_row(
            connection,
            assignment_id="third",
            subject_key="skill:third",
            assignment_set_size=2,
        )
    assert connection.execute("SELECT COUNT(*) FROM run_subject_assignments").fetchone()[0] == 2
    connection.close()


def test_v34_exact_schema_replay_preserves_row_and_rowid() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:33])
    for statement in _RUN_SUBJECT_ASSIGNMENTS_STATEMENTS:
        connection.execute(statement)
    _seed_memorial(connection)
    _insert_assignment_row(connection)
    connection.commit()
    before = tuple(connection.execute("SELECT rowid, * FROM run_subject_assignments").fetchone())

    assert apply_migrations(connection, MIGRATIONS) == (34,)
    after = tuple(connection.execute("SELECT rowid, * FROM run_subject_assignments").fetchone())
    assert after == before
    connection.close()


@pytest.mark.parametrize("mode", ["partial", "drifted"])
def test_v34_rejects_partial_or_drifted_owned_schema_atomically(mode: str) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:33])
    if mode == "partial":
        connection.execute(_RUN_SUBJECT_ASSIGNMENTS_STATEMENTS[0])
    else:
        connection.execute(
            _RUN_SUBJECT_ASSIGNMENTS_STATEMENTS[0].replace(
                "bucket BETWEEN 0 AND 9999", "bucket BETWEEN 0 AND 10000"
            )
        )
        for statement in _RUN_SUBJECT_ASSIGNMENTS_STATEMENTS[1:]:
            connection.execute(statement)
    connection.commit()

    with pytest.raises(MigrationExecutionError, match="0034_run_subject_assignments"):
        apply_migrations(connection, MIGRATIONS)

    assert (
        connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=34").fetchone()[0]
        == 0
    )
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name='run_subject_assignments'"
        ).fetchone()
        is not None
    )
    connection.close()


def test_v34_freezes_the_complete_v1_v33_triplet_prefix() -> None:
    prefix = MIGRATIONS[:33]
    payload = "\n".join(
        f"{migration.version}:{migration.name}:{migration.checksum}" for migration in prefix
    )
    assert (len(prefix), hashlib.sha256(payload.encode()).hexdigest()) == (_FROZEN_V1_V33_MANIFEST)


def test_applied_v34_checksum_drift_is_rejected_without_writes() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    before = tuple(connection.execute("SELECT * FROM schema_migrations").fetchall())
    drifted = (
        *MIGRATIONS[:-1],
        Migration(
            version=34,
            name="0034_run_subject_assignments",
            checksum="0" * 64,
            upgrade=MIGRATIONS[-1].upgrade,
        ),
    )

    with pytest.raises(MigrationStateError, match="checksum"):
        apply_migrations(connection, drifted)
    assert tuple(connection.execute("SELECT * FROM schema_migrations").fetchall()) == before
    connection.close()
