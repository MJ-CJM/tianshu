"""V32 immutable runtime generation schema and migration replay contracts."""

from __future__ import annotations

import sqlite3

import pytest

from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.governance_contract import (
    ExecutorSelectionV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.storage.migration_ledger import MigrationExecutionError, apply_migrations
from tianshu.storage.migrations import (
    _RUNTIME_GENERATIONS_OBJECT_NAMES,
    _RUNTIME_GENERATIONS_STATEMENTS,
    MIGRATIONS,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_GENERATION_A = "rg-" + "1" * 32
_GENERATION_B = "rg-" + "2" * 32
_CREATED_AT = "2026-08-26T00:00:00+00:00"


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _insert_release(
    connection: sqlite3.Connection,
    *,
    digest: str = _DIGEST_A,
    scope: str = "executor:keqing:pi",
) -> None:
    connection.execute(
        "INSERT INTO runtime_generation_releases VALUES (?, 1, ?, '{}', ?)",
        (digest, scope, _CREATED_AT),
    )


def _insert_generation(
    connection: sqlite3.Connection,
    *,
    generation_id: str = _GENERATION_A,
    digest: str = _DIGEST_A,
    scope: str = "executor:keqing:pi",
    state: str = "staged",
) -> None:
    connection.execute(
        """
        INSERT INTO runtime_generations VALUES (?, 1, ?, ?, ?, 1, ?, ?, ?)
        """,
        (
            generation_id,
            scope,
            digest,
            state,
            _CREATED_AT,
            _CREATED_AT if state in {"active", "draining", "disposed"} else None,
            _CREATED_AT,
        ),
    )


def _insert_journal(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO runtime_generation_journal VALUES (?, ?, 1, NULL, 'staged', '{}', ?, ?)
        """,
        (_DIGEST_B, _GENERATION_A, _DIGEST_A, _CREATED_AT),
    )


def _insert_native_attempt(connection: sqlite3.Connection, *, suffix: str) -> tuple[str, str]:
    edict_id = f"native-{suffix}"
    memorial_id = f"native-{suffix}-memorial"
    attempt_id = f"native-{suffix}-attempt"
    contract = RequestedGovernanceContractV1(objective=ObjectiveV1(goal=edict_id))
    connection.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        (edict_id, edict_id, _CREATED_AT),
    )
    connection.execute(
        """
        INSERT INTO requested_governance_contracts (
            edict_id, schema_version, contract_json, contract_hash, source, created_at
        ) VALUES (?, '1', ?, ?, 'explicit', ?)
        """,
        (
            edict_id,
            canonical_json_bytes(contract).decode("utf-8"),
            contract.content_hash,
            _CREATED_AT,
        ),
    )
    connection.execute(
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, 'submitted', ?)",
        (memorial_id, edict_id, _CREATED_AT),
    )
    connection.execute(
        """
        INSERT INTO execution_attempts (
            attempt_id, schema_version, memorial_id, attempt_no, status,
            available_at, max_attempts, version, created_at, updated_at
        ) VALUES (?, 1, ?, 1, 'claimable', ?, 1, 1, ?, ?)
        """,
        (attempt_id, memorial_id, _CREATED_AT, _CREATED_AT, _CREATED_AT),
    )
    return memorial_id, attempt_id


def test_v32_appends_exact_five_tables_index_triggers_and_composite_foreign_keys() -> None:
    connection = _connection()

    assert apply_migrations(connection, MIGRATIONS[:31]) == tuple(range(1, 32))
    assert apply_migrations(connection, MIGRATIONS[:32]) == (32,)
    assert MIGRATIONS[31].name == "0032_runtime_generations"
    assert (
        MIGRATIONS[31].checksum
        == "e8926305465f9372891379fb73298fbbb7b0e490032543abdbbafd29a1258142"
    )

    assert [
        row["name"] for row in connection.execute("PRAGMA table_info(runtime_generation_releases)")
    ] == ["release_digest", "schema_version", "scope", "release_json", "first_seen_at"]
    assert [
        row["name"] for row in connection.execute("PRAGMA table_info(runtime_generations)")
    ] == [
        "generation_id",
        "schema_version",
        "scope",
        "release_digest",
        "state",
        "version",
        "created_at",
        "activated_at",
        "updated_at",
    ]
    assert [
        row["name"] for row in connection.execute("PRAGMA table_info(runtime_generation_journal)")
    ] == [
        "journal_id",
        "generation_id",
        "generation_version",
        "from_state",
        "to_state",
        "entry_json",
        "entry_hash",
        "created_at",
    ]
    assert [
        row["name"] for row in connection.execute("PRAGMA table_info(generation_pointers)")
    ] == [
        "scope",
        "active_generation_id",
        "last_good_generation_id",
        "version",
        "updated_at",
    ]
    assert [
        row["name"] for row in connection.execute("PRAGMA table_info(run_generation_bindings)")
    ] == [
        "memorial_id",
        "attempt_id",
        "state",
        "generation_ids_json",
        "created_at",
    ]

    generation_fks = connection.execute("PRAGMA foreign_key_list(runtime_generations)").fetchall()
    assert {(row["table"], row["from"], row["to"], row["on_delete"]) for row in generation_fks} == {
        ("runtime_generation_releases", "release_digest", "release_digest", "RESTRICT"),
        ("runtime_generation_releases", "scope", "scope", "RESTRICT"),
    }
    pointer_fks = connection.execute("PRAGMA foreign_key_list(generation_pointers)").fetchall()
    assert {(row["table"], row["from"], row["to"], row["on_delete"]) for row in pointer_fks} == {
        ("runtime_generations", "scope", "scope", "RESTRICT"),
        (
            "runtime_generations",
            "active_generation_id",
            "generation_id",
            "RESTRICT",
        ),
        (
            "runtime_generations",
            "last_good_generation_id",
            "generation_id",
            "RESTRICT",
        ),
    }
    index_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='idx_runtime_generations_active'"
    ).fetchone()[0]
    assert "UNIQUE INDEX" in index_sql
    assert "WHERE state = 'active'" in index_sql

    objects = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN ({})".format(
                ",".join("?" for _ in _RUNTIME_GENERATIONS_OBJECT_NAMES)
            ),
            _RUNTIME_GENERATIONS_OBJECT_NAMES,
        ).fetchall()
    }
    assert objects == set(_RUNTIME_GENERATIONS_OBJECT_NAMES)
    connection.close()


def test_v32_composite_scope_foreign_keys_and_partial_unique_active_are_enforced() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    _insert_release(connection)

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        _insert_generation(connection, scope="executor:keqing:other")

    _insert_generation(connection, state="active")
    _insert_release(connection, digest=_DIGEST_B)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        _insert_generation(
            connection,
            generation_id=_GENERATION_B,
            digest=_DIGEST_B,
            state="active",
        )
    connection.close()


@pytest.mark.parametrize(
    ("statement", "parameters"),
    [
        (
            "INSERT INTO runtime_generation_releases VALUES (NULL, 1, ?, '{}', ?)",
            ("executor:keqing:pi", _CREATED_AT),
        ),
        (
            "INSERT INTO runtime_generations VALUES (NULL, 1, ?, ?, 'staged', 1, ?, NULL, ?)",
            ("executor:keqing:pi", _DIGEST_A, _CREATED_AT, _CREATED_AT),
        ),
        (
            "INSERT INTO runtime_generation_journal VALUES "
            "(NULL, ?, 1, NULL, 'staged', '{}', ?, ?)",
            (_GENERATION_B, _DIGEST_A, _CREATED_AT),
        ),
        (
            "INSERT INTO generation_pointers VALUES (NULL, ?, ?, 1, ?)",
            (_GENERATION_A, _GENERATION_A, _CREATED_AT),
        ),
        (
            "INSERT INTO run_generation_bindings VALUES (NULL, ?, 'bound', '[]', ?)",
            ("attempt-1", _CREATED_AT),
        ),
    ],
)
def test_v32_text_primary_keys_reject_null_without_bypassing_foreign_keys(
    statement: str,
    parameters: tuple[str, ...],
) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    _insert_release(connection)
    _insert_generation(connection)
    _insert_release(connection, digest=_DIGEST_B)
    _insert_generation(connection, generation_id=_GENERATION_B, digest=_DIGEST_B)
    _insert_journal(connection)

    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        connection.execute(statement, parameters)

    connection.close()


def test_v32_no_replace_and_immutable_material_guards_reject_mutation() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS)
    _insert_release(connection)
    _insert_generation(connection)
    _insert_journal(connection)
    connection.execute(
        "INSERT INTO generation_pointers VALUES (?, ?, ?, 1, ?)",
        ("executor:keqing:pi", _GENERATION_A, _GENERATION_A, _CREATED_AT),
    )
    connection.execute(
        "INSERT INTO run_generation_bindings VALUES (?, ?, 'bound', '[]', ?)",
        ("memorial-1", "attempt-1", _CREATED_AT),
    )

    with pytest.raises(sqlite3.IntegrityError, match="release is immutable"):
        connection.execute(
            "INSERT OR REPLACE INTO runtime_generation_releases VALUES (?, 1, ?, '{}', ?)",
            (_DIGEST_A, "executor:keqing:pi", _CREATED_AT),
        )
    with pytest.raises(sqlite3.IntegrityError, match="identity already exists"):
        connection.execute(
            """
            INSERT OR REPLACE INTO runtime_generations VALUES (?, 1, ?, ?, 'staged', 1, ?, NULL, ?)
            """,
            (
                _GENERATION_A,
                "executor:keqing:pi",
                _DIGEST_A,
                _CREATED_AT,
                _CREATED_AT,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="journal is immutable"):
        connection.execute(
            "UPDATE runtime_generation_journal SET entry_hash=? WHERE journal_id=?",
            (_DIGEST_B, _DIGEST_B),
        )
    with pytest.raises(sqlite3.IntegrityError, match="identity already exists"):
        connection.execute(
            "INSERT OR REPLACE INTO generation_pointers VALUES (?, ?, ?, 2, ?)",
            ("executor:keqing:pi", _GENERATION_A, _GENERATION_A, _CREATED_AT),
        )
    with pytest.raises(sqlite3.IntegrityError, match="material is immutable"):
        connection.execute(
            "UPDATE runtime_generations SET release_digest=? WHERE generation_id=?",
            (_DIGEST_B, _GENERATION_A),
        )
    with pytest.raises(sqlite3.IntegrityError, match="generation binding is immutable"):
        connection.execute(
            "INSERT OR REPLACE INTO run_generation_bindings VALUES (?, ?, 'bound', '[]', ?)",
            ("memorial-1", "attempt-1", _CREATED_AT),
        )
    with pytest.raises(sqlite3.IntegrityError, match="generation binding is immutable"):
        connection.execute(
            "UPDATE run_generation_bindings SET generation_ids_json='[\"changed\"]' "
            "WHERE memorial_id='memorial-1' AND attempt_id='attempt-1'"
        )
    connection.close()


def test_v32_backfills_provable_attempt_truth_and_marks_unknown_pi_unresolved() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:31])

    contracts = {
        "native": RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="native"),
        ),
        "pi": RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="pi"),
            executor=ExecutorSelectionV1(adapter_id="keqing:pi"),
        ),
    }
    for edict_id, contract in contracts.items():
        connection.execute(
            "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
            (edict_id, edict_id, _CREATED_AT),
        )
        connection.execute(
            """
            INSERT INTO requested_governance_contracts (
                edict_id, schema_version, contract_json, contract_hash, source, created_at
            ) VALUES (?, '1', ?, ?, 'explicit', ?)
            """,
            (
                edict_id,
                canonical_json_bytes(contract).decode("utf-8"),
                contract.content_hash,
                _CREATED_AT,
            ),
        )

    attempts = (
        ("native-memorial", "native", "native-attempt"),
        ("pi-bound-memorial", "pi", "pi-bound-attempt"),
        ("pi-unknown-memorial", "pi", "pi-unknown-attempt"),
    )
    for memorial_id, edict_id, attempt_id in attempts:
        connection.execute(
            "INSERT INTO memorials (id, edict_id, status, created_at) "
            "VALUES (?, ?, 'submitted', ?)",
            (memorial_id, edict_id, _CREATED_AT),
        )
        connection.execute(
            """
            INSERT INTO execution_attempts (
                attempt_id, schema_version, memorial_id, attempt_no, status,
                available_at, max_attempts, version, created_at, updated_at
            ) VALUES (?, 1, ?, 1, 'claimable', ?, 1, 1, ?, ?)
            """,
            (attempt_id, memorial_id, _CREATED_AT, _CREATED_AT, _CREATED_AT),
        )
    connection.execute(
        "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
        (_DIGEST_A, _CREATED_AT),
    )
    connection.execute(
        "INSERT INTO run_system_bindings VALUES (?, ?, ?, ?, ?)",
        (
            "pi-bound-memorial",
            "pi-bound-attempt",
            _DIGEST_A,
            '["rg-historical"]',
            _CREATED_AT,
        ),
    )
    connection.commit()

    assert apply_migrations(connection, MIGRATIONS[:32]) == (32,)

    rows = connection.execute(
        "SELECT attempt_id, state, generation_ids_json "
        "FROM run_generation_bindings ORDER BY attempt_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("native-attempt", "bound", "[]"),
        ("pi-bound-attempt", "bound", '["rg-historical"]'),
        ("pi-unknown-attempt", "unresolved", None),
    ]
    connection.close()


def test_v32_exact_schema_replay_preserves_existing_row_and_rowid() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:31])
    memorial_id, attempt_id = _insert_native_attempt(connection, suffix="exact")
    for statement in _RUNTIME_GENERATIONS_STATEMENTS:
        connection.execute(statement)
    _insert_release(connection)
    connection.commit()
    before = connection.execute(
        "SELECT rowid, * FROM runtime_generation_releases WHERE release_digest=?",
        (_DIGEST_A,),
    ).fetchone()

    assert apply_migrations(connection, MIGRATIONS[:32]) == (32,)
    after = connection.execute(
        "SELECT rowid, * FROM runtime_generation_releases WHERE release_digest=?",
        (_DIGEST_A,),
    ).fetchone()
    assert tuple(after) == tuple(before)
    marker = connection.execute(
        "SELECT state, generation_ids_json FROM run_generation_bindings "
        "WHERE memorial_id=? AND attempt_id=?",
        (memorial_id, attempt_id),
    ).fetchone()
    assert tuple(marker) == ("bound", "[]")
    connection.close()


def test_v32_exact_schema_replay_rejects_conflicting_historical_marker() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:31])
    memorial_id, attempt_id = _insert_native_attempt(connection, suffix="conflict")
    for statement in _RUNTIME_GENERATIONS_STATEMENTS:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO run_generation_bindings VALUES (?, ?, 'unresolved', NULL, ?)",
        (memorial_id, attempt_id, _CREATED_AT),
    )
    connection.commit()

    with pytest.raises(MigrationExecutionError, match="0032_runtime_generations"):
        apply_migrations(connection, MIGRATIONS[:32])

    assert (
        connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=32").fetchone()[0]
        == 0
    )
    assert tuple(
        connection.execute(
            "SELECT state, generation_ids_json FROM run_generation_bindings"
        ).fetchone()
    ) == ("unresolved", None)
    connection.close()


@pytest.mark.parametrize("mode", ["partial", "drifted"])
def test_v32_rejects_partial_or_drifted_owned_schema_atomically(mode: str) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:31])
    statement = _RUNTIME_GENERATIONS_STATEMENTS[0]
    if mode == "drifted":
        statement = statement.replace("first_seen_at TEXT NOT NULL", "first_seen_at TEXT")
    connection.execute(statement)
    connection.commit()

    with pytest.raises(MigrationExecutionError, match="0032_runtime_generations"):
        apply_migrations(connection, MIGRATIONS[:32])

    assert (
        connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=32").fetchone()[0]
        == 0
    )
    remaining = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN ({})".format(
                ",".join("?" for _ in _RUNTIME_GENERATIONS_OBJECT_NAMES)
            ),
            _RUNTIME_GENERATIONS_OBJECT_NAMES,
        ).fetchall()
    }
    assert remaining == {"runtime_generation_releases"}
    connection.close()
