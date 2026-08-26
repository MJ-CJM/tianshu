"""V35 executor-candidate rebuild and generation-authority contracts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

import pytest

from tianshu.storage.migration_ledger import (
    Migration,
    MigrationConnection,
    MigrationExecutionError,
    MigrationStateError,
    apply_migrations,
)
from tianshu.storage.migrations import (
    _EXECUTOR_CANDIDATE_AUTHORITY_OBJECT_NAMES,
    _EXECUTOR_CANDIDATE_BASE_OBJECT_NAMES,
    _EXECUTOR_CANDIDATE_REBUILD_TABLES,
    _EXECUTOR_CANDIDATE_SOURCE_INBOUND_FOREIGN_KEYS,
    _EXECUTOR_CANDIDATE_TARGET_INBOUND_FOREIGN_KEYS,
    _EXECUTOR_CANDIDATE_TARGET_OBJECTS,
    _EXECUTOR_CANDIDATE_TEMP_TABLES,
    _EXECUTOR_CANDIDATE_VERSION,
    _RESERVED_TEMP_TABLES,
    MIGRATIONS,
    run_migrations,
)

_NOW = "2026-08-26T00:00:00+00:00"
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_DIGEST_C = "c" * 64
_DIGEST_D = "d" * 64
_DIGEST_E = "e" * 64
_GENERATION_A = "rg-" + "1" * 32
_GENERATION_B = "rg-" + "2" * 32


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _placeholders(values: Iterable[object]) -> str:
    return ",".join("?" for _ in values)


def _seed_v34_rows(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES ('edict-v35', 'v35', ?)",
        (_NOW,),
    )
    connection.execute(
        """
        INSERT INTO memorials (id, edict_id, status, created_at)
        VALUES ('memorial-v35', 'edict-v35', 'submitted', ?)
        """,
        (_NOW,),
    )
    connection.execute(
        """
        INSERT INTO evolution_candidates (
            rowid, candidate_id, schema_version, kind, subject_key,
            provenance_json, provenance_hash, base_json, candidate_ref_json,
            diff_artifact_digest, evolution_contract_json, evolution_contract_hash,
            gate_snapshot_version, evidence_bundle_ids_json, routing_json,
            rollback_json, lifecycle, version, created_at, updated_at
        ) VALUES (
            101, 'candidate-v35', 1, 'skill', 'skill:v35',
            '{}', ?, '{}', '{}', ?, '{}', ?, 1, '[]', '{}',
            '{}', 'proposed', 1, ?, ?
        )
        """,
        (_DIGEST_A, _DIGEST_B, _DIGEST_C, _NOW, _NOW),
    )
    connection.execute(
        """
        INSERT INTO evolution_gate_snapshots (
            rowid, gate_snapshot_id, candidate_id, candidate_version,
            gate_snapshot_version, snapshot_json, snapshot_hash,
            evidence_bundle_ids_json, created_at
        ) VALUES (102, 'gate-v35', 'candidate-v35', 1, 1, '{}', ?, '[]', ?)
        """,
        (_DIGEST_A, _NOW),
    )
    connection.execute(
        """
        INSERT INTO evolution_lifecycle_journal (
            rowid, journal_id, candidate_id, candidate_version, from_lifecycle,
            to_lifecycle, decision_request_id, entry_json, entry_hash, created_at
        ) VALUES (
            103, 'lifecycle-v35', 'candidate-v35', 1, NULL,
            'proposed', NULL, '{}', ?, ?
        )
        """,
        (_DIGEST_A, _NOW),
    )
    connection.execute(
        """
        INSERT INTO evolution_promotion_journal (
            rowid, promotion_journal_id, command_key, candidate_id, candidate_version,
            gate_snapshot_version, action, status, decision_request_id,
            entry_json, entry_hash, created_at
        ) VALUES (
            104, 'promotion-v35', 'command-v35', 'candidate-v35', 1,
            1, 'start_canary', 'intended', NULL, '{}', ?, ?
        )
        """,
        (_DIGEST_A, _NOW),
    )
    connection.execute(
        """
        INSERT INTO evolution_routing_allocations (
            rowid, candidate_id, routing_version, allocation_basis_points,
            allocation_seed_id, routing_json, routing_hash, version,
            created_at, updated_at
        ) VALUES (105, 'candidate-v35', 1, 100, 'seed-v35', '{}', ?, 1, ?, ?)
        """,
        (_DIGEST_A, _NOW, _NOW),
    )
    connection.execute(
        """
        INSERT INTO run_evolution_assignments (
            rowid, assignment_id, memorial_id, candidate_id, routing_version, bucket,
            champion_ref_json, selected_ref_json, overlay_digest,
            assignment_json, assignment_hash, created_at
        ) VALUES (
            106, 'assignment-v35', 'memorial-v35', 'candidate-v35', 1, 42,
            '{}', '{}', ?, '{}', ?, ?
        )
        """,
        (_DIGEST_A, _DIGEST_B, _NOW),
    )
    connection.execute(
        """
        INSERT INTO run_subject_assignments (
            rowid, assignment_id, memorial_id, kind, subject_key, candidate_id,
            routing_version, bucket, champion_ref_json, selected_ref_json,
            overlay_digest, assignment_json, assignment_hash,
            assignment_set_hash, assignment_set_size, created_at
        ) VALUES (
            107, 'subject-assignment-v35', 'memorial-v35', 'skill', 'skill:v35',
            'candidate-v35', 1, 43, '{}', '{}', ?, '{}', ?, ?, 1, ?
        )
        """,
        (_DIGEST_A, _DIGEST_B, _DIGEST_C, _NOW),
    )


def _row_snapshot(connection: sqlite3.Connection) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        table: tuple(tuple(row) for row in connection.execute(f'SELECT rowid, * FROM "{table}"'))
        for table in _EXECUTOR_CANDIDATE_REBUILD_TABLES
    }


def _authority_row_snapshot(
    connection: sqlite3.Connection,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        table: tuple(tuple(row) for row in connection.execute(f'SELECT rowid, * FROM "{table}"'))
        for table in (
            "executor_generation_authorities",
            "executor_generation_authority_journal",
        )
    }


def _object_snapshot(connection: sqlite3.Connection) -> dict[str, tuple[str, str, int]]:
    return {
        str(row["name"]): (str(row["type"]), str(row["sql"]), int(row["rootpage"]))
        for row in connection.execute(
            f"""
            SELECT type, name, sql, rootpage FROM sqlite_master
            WHERE name IN ({_placeholders(_EXECUTOR_CANDIDATE_TARGET_OBJECTS)}) ORDER BY name
            """,
            tuple(_EXECUTOR_CANDIDATE_TARGET_OBJECTS),
        )
    }


def _inbound_foreign_keys(
    connection: sqlite3.Connection,
) -> frozenset[tuple[str, str, str, str, str, str, str]]:
    parents = {
        *_EXECUTOR_CANDIDATE_REBUILD_TABLES,
        "executor_generation_authorities",
        "executor_generation_authority_journal",
    }
    incoming: set[tuple[str, str, str, str, str, str, str]] = set()
    for table_row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        child = str(table_row["name"])
        for row in connection.execute(f'PRAGMA foreign_key_list("{child}")'):
            parent = str(row["table"])
            if parent in parents:
                incoming.add(
                    (
                        child,
                        parent,
                        str(row["from"]),
                        str(row["to"]),
                        str(row["on_update"]),
                        str(row["on_delete"]),
                        str(row["match"]),
                    )
                )
    return frozenset(incoming)


def _seed_authority_dependencies(connection: sqlite3.Connection) -> None:
    _seed_v34_rows(connection)
    connection.execute(
        "INSERT INTO runtime_generation_releases VALUES (?, 1, ?, '{}', ?)",
        (_DIGEST_A, "executor:keqing:pi", _NOW),
    )
    for generation_id in (_GENERATION_A, _GENERATION_B):
        connection.execute(
            """
            INSERT INTO runtime_generations (
                generation_id, schema_version, scope, release_digest, state,
                version, created_at, activated_at, updated_at
            ) VALUES (?, 1, ?, ?, 'ready', 1, ?, NULL, ?)
            """,
            (generation_id, "executor:keqing:pi", _DIGEST_A, _NOW, _NOW),
        )


def _insert_pending_authority(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO executor_generation_authorities (
            candidate_id, authority_id, schema_version, epoch, candidate_version,
            candidate_artifact_digest, candidate_canonical_digest, release_digest,
            scope, generation_id, promotion_journal_id, status, authority_json,
            authority_hash, version, created_at, updated_at, revoked_at,
            revocation_reason
        ) VALUES (
            'candidate-v35', ?, 1, 1, 1, ?, ?, ?, ?, ?, 'promotion-v35',
            'pending', '{}', ?, 1, ?, ?, NULL, NULL
        )
        """,
        (
            _DIGEST_D,
            _DIGEST_B,
            _DIGEST_C,
            _DIGEST_A,
            "executor:keqing:pi",
            _GENERATION_A,
            _DIGEST_E,
            _NOW,
            _NOW,
        ),
    )


def test_v35_is_the_live_tail_and_creates_authority_objects() -> None:
    connection = _connection()
    try:
        assert apply_migrations(connection, MIGRATIONS) == tuple(range(1, 36))
        assert _EXECUTOR_CANDIDATE_VERSION == 35
        assert MIGRATIONS[-1].name == "0035_executor_candidate_kind"
        assert (
            MIGRATIONS[-1].checksum
            == "14402935160ab156af4deeec680986703941e4107db324f9ffcc1f587daf506e"
        )
        actual = {
            str(row["name"])
            for row in connection.execute(
                f"SELECT name FROM sqlite_master WHERE name IN "
                f"({_placeholders(_EXECUTOR_CANDIDATE_AUTHORITY_OBJECT_NAMES)})",
                _EXECUTOR_CANDIDATE_AUTHORITY_OBJECT_NAMES,
            )
        }
        assert actual == set(_EXECUTOR_CANDIDATE_AUTHORITY_OBJECT_NAMES)
    finally:
        connection.close()


def test_v35_authority_tables_lock_composite_runtime_and_promotion_foreign_keys() -> None:
    connection = _connection()
    try:
        apply_migrations(connection, MIGRATIONS)
        expected = {
            (
                "evolution_candidates",
                "candidate_id",
                "candidate_id",
                "RESTRICT",
            ),
            (
                "runtime_generation_releases",
                "release_digest",
                "release_digest",
                "RESTRICT",
            ),
            ("runtime_generation_releases", "scope", "scope", "RESTRICT"),
            ("runtime_generations", "scope", "scope", "RESTRICT"),
            (
                "runtime_generations",
                "generation_id",
                "generation_id",
                "RESTRICT",
            ),
            (
                "evolution_promotion_journal",
                "promotion_journal_id",
                "promotion_journal_id",
                "RESTRICT",
            ),
        }
        for table in (
            "executor_generation_authorities",
            "executor_generation_authority_journal",
        ):
            assert {
                (
                    str(row["table"]),
                    str(row["from"]),
                    str(row["to"]),
                    str(row["on_delete"]),
                )
                for row in connection.execute(f'PRAGMA foreign_key_list("{table}")')
            } == expected
    finally:
        connection.close()


def test_v35_migrates_all_six_fk_children_without_changing_rows_or_rowids() -> None:
    connection = _connection()
    try:
        assert apply_migrations(connection, MIGRATIONS[:-1]) == tuple(range(1, 35))
        assert _inbound_foreign_keys(connection) == _EXECUTOR_CANDIDATE_SOURCE_INBOUND_FOREIGN_KEYS
        _seed_v34_rows(connection)
        connection.commit()
        rows_before = _row_snapshot(connection)

        assert apply_migrations(connection, MIGRATIONS) == (35,)

        assert _row_snapshot(connection) == rows_before
        assert _inbound_foreign_keys(connection) == _EXECUTOR_CANDIDATE_TARGET_INBOUND_FOREIGN_KEYS
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert not (
            {
                str(row["name"])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            & _EXECUTOR_CANDIDATE_TEMP_TABLES
        )
        actual_sql = {
            str(row["name"]): " ".join(str(row["sql"]).split())
            for row in connection.execute(
                f"SELECT name, sql FROM sqlite_master WHERE name IN "
                f"({_placeholders(_EXECUTOR_CANDIDATE_TARGET_OBJECTS)})",
                tuple(_EXECUTOR_CANDIDATE_TARGET_OBJECTS),
            )
        }
        assert actual_sql == _EXECUTOR_CANDIDATE_TARGET_OBJECTS
        assert len(_EXECUTOR_CANDIDATE_BASE_OBJECT_NAMES) == 20
        assert sum(name.startswith("idx_") for name in _EXECUTOR_CANDIDATE_BASE_OBJECT_NAMES) == 2
        assert (
            sum(
                "_no_" in name or name.endswith("sealed_insert")
                for name in _EXECUTOR_CANDIDATE_BASE_OBJECT_NAMES
            )
            == 11
        )

        connection.execute(
            """
            INSERT INTO evolution_candidates (
                candidate_id, schema_version, kind, subject_key, provenance_json,
                provenance_hash, base_json, candidate_ref_json, diff_artifact_digest,
                evolution_contract_json, evolution_contract_hash,
                evidence_bundle_ids_json, rollback_json, lifecycle, version,
                created_at, updated_at
            ) VALUES (
                'executor-candidate', 1, 'executor', 'executor:keqing:pi', '{}',
                ?, '{}', '{}', ?, '{}', ?, '[]', '{}', 'proposed', 1, ?, ?
            )
            """,
            (_DIGEST_A, _DIGEST_B, _DIGEST_C, _NOW, _NOW),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO evolution_candidates (
                    candidate_id, schema_version, kind, subject_key, provenance_json,
                    provenance_hash, base_json, candidate_ref_json, diff_artifact_digest,
                    evolution_contract_json, evolution_contract_hash,
                    evidence_bundle_ids_json, rollback_json, lifecycle, version,
                    created_at, updated_at
                ) VALUES (
                    'unknown-candidate', 1, 'unknown', 'unknown:v35', '{}',
                    ?, '{}', '{}', ?, '{}', ?, '[]', '{}', 'proposed', 1, ?, ?
                )
                """,
                (_DIGEST_A, _DIGEST_B, _DIGEST_C, _NOW, _NOW),
            )
    finally:
        connection.close()


def test_v35_exact_target_replays_without_recreating_schema() -> None:
    connection = _connection()
    try:
        apply_migrations(connection, MIGRATIONS)
        _seed_authority_dependencies(connection)
        _insert_pending_authority(connection)
        connection.commit()
        rows_before = _row_snapshot(connection)
        authority_before = _authority_row_snapshot(connection)
        objects_before = _object_snapshot(connection)
        connection.execute("DELETE FROM schema_migrations WHERE version = 35")
        connection.commit()

        assert apply_migrations(connection, MIGRATIONS) == (35,)
        assert _row_snapshot(connection) == rows_before
        assert _authority_row_snapshot(connection) == authority_before
        assert _object_snapshot(connection) == objects_before
        assert apply_migrations(connection, MIGRATIONS) == ()
    finally:
        connection.close()


def test_v35_canonical_no_ledger_database_is_adopted_in_place() -> None:
    connection = _connection()
    try:
        apply_migrations(connection, MIGRATIONS)
        _seed_authority_dependencies(connection)
        _insert_pending_authority(connection)
        connection.commit()
        rows_before = _row_snapshot(connection)
        authority_before = _authority_row_snapshot(connection)
        objects_before = _object_snapshot(connection)
        connection.execute("DROP TABLE schema_migrations")
        connection.commit()

        assert run_migrations(connection) == tuple(range(1, 36))
        assert _row_snapshot(connection) == rows_before
        assert _authority_row_snapshot(connection) == authority_before
        assert _object_snapshot(connection) == objects_before
    finally:
        connection.close()


@pytest.mark.parametrize("mode", ["reserved-temp", "unknown-inbound", "drifted-object"])
def test_v35_reserved_temp_or_unknown_inbound_fk_fails_before_rebuild(mode: str) -> None:
    connection = _connection()
    try:
        apply_migrations(connection, MIGRATIONS[:-1])
        _seed_v34_rows(connection)
        if mode == "reserved-temp":
            connection.execute("CREATE TABLE _evolution_candidates_v35 (sentinel TEXT)")
        elif mode == "unknown-inbound":
            connection.execute(
                """
                CREATE TABLE extension_candidate_reference (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT REFERENCES evolution_candidates(candidate_id)
                        ON DELETE RESTRICT
                )
                """
            )
        else:
            connection.execute("DROP INDEX idx_evolution_candidates_lifecycle")
            connection.execute(
                "CREATE INDEX idx_evolution_candidates_lifecycle ON evolution_candidates(kind)"
            )
        connection.commit()
        rows_before = _row_snapshot(connection)
        source_objects_before = {
            str(row["name"]): str(row["sql"])
            for row in connection.execute(
                f"SELECT name, sql FROM sqlite_master WHERE name IN "
                f"({_placeholders(_EXECUTOR_CANDIDATE_BASE_OBJECT_NAMES)})",
                _EXECUTOR_CANDIDATE_BASE_OBJECT_NAMES,
            )
        }

        with pytest.raises(MigrationExecutionError, match="0035_executor_candidate_kind"):
            apply_migrations(connection, MIGRATIONS)

        assert _row_snapshot(connection) == rows_before
        assert {
            str(row["name"]): str(row["sql"])
            for row in connection.execute(
                f"SELECT name, sql FROM sqlite_master WHERE name IN "
                f"({_placeholders(_EXECUTOR_CANDIDATE_BASE_OBJECT_NAMES)})",
                _EXECUTOR_CANDIDATE_BASE_OBJECT_NAMES,
            )
        } == source_objects_before
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=35"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "fault_marker",
    [
        'ALTER TABLE "evolution_candidates" RENAME',
        'INSERT INTO "evolution_promotion_journal"',
        'DROP TABLE "_evolution_candidates_v35"',
        "CREATE TRIGGER run_subject_assignments_no_delete",
    ],
)
def test_v35_fault_injection_rolls_back_every_rebuild_phase(
    monkeypatch: pytest.MonkeyPatch,
    fault_marker: str,
) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:-1])
    _seed_v34_rows(connection)
    connection.commit()
    rows_before = _row_snapshot(connection)
    inbound_before = _inbound_foreign_keys(connection)
    original_execute = MigrationConnection.execute
    tripped = False

    def fail_once(
        active: MigrationConnection,
        sql: str,
        parameters=(),  # type: ignore[no-untyped-def]
        /,
    ):
        nonlocal tripped
        if not tripped and fault_marker in " ".join(sql.split()):
            tripped = True
            raise RuntimeError("injected V35 migration failure")
        return original_execute(active, sql, parameters)

    monkeypatch.setattr(MigrationConnection, "execute", fail_once)
    try:
        with pytest.raises(MigrationExecutionError, match="0035_executor_candidate_kind"):
            apply_migrations(connection, MIGRATIONS)
        assert tripped
        assert _row_snapshot(connection) == rows_before
        assert _inbound_foreign_keys(connection) == inbound_before
        assert not (
            {
                str(row["name"])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            & _EXECUTOR_CANDIDATE_TEMP_TABLES
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=35"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


@pytest.mark.parametrize("copy_fault", ["short", "changed"])
def test_v35_bidirectional_payload_verification_rolls_back_bad_copy(
    monkeypatch: pytest.MonkeyPatch,
    copy_fault: str,
) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:-1])
    _seed_v34_rows(connection)
    connection.commit()
    rows_before = _row_snapshot(connection)
    original_execute = MigrationConnection.execute

    def shorten_copy(
        active: MigrationConnection,
        sql: str,
        parameters=(),  # type: ignore[no-untyped-def]
        /,
    ):
        normalized = " ".join(sql.split())
        if normalized.startswith('INSERT INTO "evolution_routing_allocations" (rowid'):
            if copy_fault == "short":
                sql = sql.replace(" ORDER BY rowid", " WHERE 0 ORDER BY rowid")
            else:
                insert, separator, select = sql.partition(" SELECT ")
                sql = (
                    insert
                    + separator
                    + select.replace(
                        '"allocation_basis_points"',
                        '"allocation_basis_points" + 1',
                        1,
                    )
                )
        return original_execute(active, sql, parameters)

    monkeypatch.setattr(MigrationConnection, "execute", shorten_copy)
    try:
        with pytest.raises(MigrationExecutionError, match="0035_executor_candidate_kind") as error:
            apply_migrations(connection, MIGRATIONS)
        assert "did not preserve evolution_routing_allocations" in str(error.value.__cause__)
        assert _row_snapshot(connection) == rows_before
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE version=35"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_v35_temp_names_are_reserved_by_canonical_adoption() -> None:
    assert _EXECUTOR_CANDIDATE_TEMP_TABLES.issubset(_RESERVED_TEMP_TABLES)


def test_applied_v35_checksum_drift_is_rejected_without_writes() -> None:
    connection = _connection()
    try:
        apply_migrations(connection, MIGRATIONS)
        ledger_before = tuple(connection.execute("SELECT * FROM schema_migrations"))
        drifted = (
            *MIGRATIONS[:-1],
            Migration(
                version=35,
                name="0035_executor_candidate_kind",
                checksum="0" * 64,
                upgrade=MIGRATIONS[-1].upgrade,
            ),
        )

        with pytest.raises(MigrationStateError, match="checksum"):
            apply_migrations(connection, drifted)
        assert tuple(connection.execute("SELECT * FROM schema_migrations")) == ledger_before
    finally:
        connection.close()


def test_v35_authority_transition_guard_enforces_epoch_identity_and_drain_states() -> None:
    connection = _connection()
    try:
        apply_migrations(connection, MIGRATIONS)
        _seed_authority_dependencies(connection)
        _insert_pending_authority(connection)

        with pytest.raises(sqlite3.IntegrityError, match="invalid executor generation"):
            connection.execute(
                """
                UPDATE executor_generation_authorities
                SET status='authorized', generation_id=?, version=2, updated_at=?
                WHERE candidate_id='candidate-v35'
                """,
                (_GENERATION_B, _NOW),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE executor_generation_authorities
                SET status='revoking', version=2, updated_at=?
                WHERE candidate_id='candidate-v35'
                """,
                (_NOW,),
            )

        connection.execute(
            """
            UPDATE executor_generation_authorities
            SET status='authorized', authority_json='{"status":"authorized"}',
                authority_hash=?, version=2, updated_at=?
            WHERE candidate_id='candidate-v35'
            """,
            (_DIGEST_A, _NOW),
        )
        connection.execute(
            """
            UPDATE executor_generation_authorities
            SET status='revoking', revoked_at=?, revocation_reason='draining',
                authority_json='{"status":"revoking"}', authority_hash=?,
                version=3, updated_at=?
            WHERE candidate_id='candidate-v35'
            """,
            (_NOW, _DIGEST_B, _NOW),
        )
        connection.execute(
            """
            UPDATE executor_generation_authorities
            SET status='revoked', authority_json='{"status":"revoked"}',
                authority_hash=?, version=4, updated_at=?
            WHERE candidate_id='candidate-v35'
            """,
            (_DIGEST_C, _NOW),
        )
        connection.execute(
            """
            UPDATE executor_generation_authorities
            SET status='pending', epoch=2, authority_id=?, generation_id=?,
                authority_json='{}', authority_hash=?, version=5,
                created_at=?, updated_at=?, revoked_at=NULL, revocation_reason=NULL
            WHERE candidate_id='candidate-v35'
            """,
            (_DIGEST_E, _GENERATION_B, _DIGEST_D, _NOW, _NOW),
        )
        connection.execute(
            """
            UPDATE executor_generation_authorities
            SET status='authorized', authority_hash=?, version=6, updated_at=?
            WHERE candidate_id='candidate-v35'
            """,
            (_DIGEST_A, _NOW),
        )
        connection.execute(
            """
            UPDATE executor_generation_authorities
            SET status='revoked', revoked_at=?, revocation_reason='promoted rollback',
                authority_hash=?, version=7, updated_at=?
            WHERE candidate_id='candidate-v35'
            """,
            (_NOW, _DIGEST_B, _NOW),
        )
        row = connection.execute(
            "SELECT status, epoch, authority_id, generation_id, version "
            "FROM executor_generation_authorities"
        ).fetchone()
        assert tuple(row) == ("revoked", 2, _DIGEST_E, _GENERATION_B, 7)
        with pytest.raises(sqlite3.IntegrityError, match="retained"):
            connection.execute(
                "DELETE FROM executor_generation_authorities WHERE candidate_id='candidate-v35'"
            )
    finally:
        connection.close()


def test_v35_authority_journal_is_append_only_and_rejects_replace() -> None:
    connection = _connection()
    try:
        apply_migrations(connection, MIGRATIONS)
        _seed_authority_dependencies(connection)
        _insert_pending_authority(connection)
        connection.execute(
            """
            INSERT INTO executor_generation_authority_journal (
                journal_id, authority_id, candidate_id, authority_version, epoch,
                transition, candidate_version, candidate_artifact_digest,
                candidate_canonical_digest, release_digest, scope, generation_id,
                promotion_journal_id, reason_code, entry_json, entry_hash, created_at
            ) VALUES (
                ?, ?, 'candidate-v35', 1, 1, 'pending', 1, ?, ?, ?, ?, ?,
                'promotion-v35', 'candidate_ready', '{}', ?, ?
            )
            """,
            (
                _DIGEST_A,
                _DIGEST_D,
                _DIGEST_B,
                _DIGEST_C,
                _DIGEST_A,
                "executor:keqing:pi",
                _GENERATION_A,
                _DIGEST_E,
                _NOW,
            ),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE executor_generation_authority_journal SET reason_code='changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM executor_generation_authority_journal")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                """
                INSERT OR REPLACE INTO executor_generation_authority_journal (
                    journal_id, authority_id, candidate_id, authority_version, epoch,
                    transition, candidate_version, candidate_artifact_digest,
                    candidate_canonical_digest, release_digest, scope, generation_id,
                    promotion_journal_id, reason_code, entry_json, entry_hash, created_at
                ) SELECT ?, authority_id, candidate_id, authority_version, epoch,
                    transition, candidate_version, candidate_artifact_digest,
                    candidate_canonical_digest, release_digest, scope, generation_id,
                    promotion_journal_id, reason_code, entry_json, entry_hash, created_at
                FROM executor_generation_authority_journal
                """,
                (_DIGEST_B,),
            )
    finally:
        connection.close()
