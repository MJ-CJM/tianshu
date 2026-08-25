"""V31 immutable system snapshot and run-binding migration contracts."""

from __future__ import annotations

import sqlite3

import pytest

from tianshu.storage.migration_ledger import MigrationExecutionError, apply_migrations
from tianshu.storage.migrations import (
    _SYSTEM_SNAPSHOTS_OBJECT_NAMES,
    _SYSTEM_SNAPSHOTS_STATEMENTS,
    MIGRATIONS,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64
_CREATED_AT = "2026-08-25T00:00:00+00:00"
_FROZEN_V1_V30 = (
    (
        1,
        "0001_adopt_v042_baseline",
        "9672603c12dd858ea714b291d6ed94f1a27cb373bfcff97665b6316b4aa552a6",
    ),
    (2, "0002_auth_tokens", "a2bbf753e0c3244fccc86be2d4588af2c926399f6dfa0dba0af5d0c060179c5a"),
    (
        3,
        "0003_governance_contracts",
        "07cb59c354035674fbcabcf1a037b4b273ae43b4e1e4dd8427cf90361bff2ff8",
    ),
    (
        4,
        "0004_workspace_foundation",
        "1c0a028e0ea16475b9de5eb0c843f81aa275ddf62c0aca3c067bf8408dd9bee5",
    ),
    (
        5,
        "0005_governed_apply_bindings",
        "c73294984096ea15e32d6ce80294f82323408cda12e82efea645ad8f35c5abc6",
    ),
    (
        6,
        "0006_seed_default_personas",
        "596e672919bbe16b111fe3793e183b17666c7c5cad588d5532d7b2875501fca1",
    ),
    (
        7,
        "0007_system_audit_events",
        "b24d3152f2b5aaa2d7dbf5776a5c865d336e025e861f8ca110e8be0c6a42e10b",
    ),
    (
        8,
        "0008_encrypt_mcp_secret_mappings",
        "f03ad9148472267b754f6e4f1f03cefc947795c2a6717e0b89206b38244706ad",
    ),
    (
        9,
        "0009_durable_edict_ingress",
        "114d0d4daab66202b32a4f9e4eb4290e2e06602ecf9465ce4d5beae03aac0a98",
    ),
    (
        10,
        "0010_telegram_seen_instance_identity",
        "d0587f036178e5f36e25277df16528925823905cd35d8bba30e7a3a8ab680f67",
    ),
    (
        11,
        "0011_decisions_run_state",
        "b5c8d33e52ba3132ebad7d5d730c9df7423a5c3ed86d1d404a06b590f48e075e",
    ),
    (
        12,
        "0012_decision_run_state_guards",
        "a9dcd2be1056f40b76a97f658939bd3f66eb0fe625056c3b55fb521bc3821d3d",
    ),
    (
        13,
        "0013_governed_apply_decision_binding",
        "e3d72d6b4558437d0a2fd7d3a6fba8c1e4261f56c4ef4168b1f9eb3049da412e",
    ),
    (
        14,
        "0014_execution_attempt_ledger",
        "988e8d7b4577869d8b7a36fe73d66ea0902b5690ab4382ab27e5c99d67e2eae0",
    ),
    (
        15,
        "0015_side_effect_journal",
        "69b6659ac54a5ac99477224a07dc647b451928b8a44c0c72f16f3fc98acf6c8c",
    ),
    (
        16,
        "0016_artifacts_evidence",
        "829e2782d2a2df7ba6fcd989f9589c846695fc88f776726f88e7a2c51577eaf3",
    ),
    (
        17,
        "0017_internal_notification_delivery",
        "a5d5d80051af6657c072fc91a8fec4011c00ba6beb5c3f7fabd6ae1555aa77dd",
    ),
    (
        18,
        "0018_governed_evolution_candidates",
        "150b2e9e278ff76cc45ba3c3726f0ca818ba9d01fd1d48d2a0c3679331ce7c69",
    ),
    (
        19,
        "0019_model_providers",
        "b8c7d4dbe2cb0148b0ae309416a17ba19751240189942c840f815ddbe2ade09a",
    ),
    (
        20,
        "0020_encrypt_llm_config_keys",
        "0d1a74e644738a5786e54f432cc4d33a4a291e4b7adf6aeb423620717015bbdd",
    ),
    (21, "0021_app_settings", "ca45142104dcb900464566b12030c8defa3d30ea528ae9dad052a8afec3a5e88"),
    (
        22,
        "0022_legacy_assignment_cleanup",
        "d5f7a0c0656861c26d20427e6564ca059600e5bfc9c1ccc2ce1554760c1f4bfa",
    ),
    (
        23,
        "0023_cost_cache_read_tokens",
        "10b3da2cfe59da3cfec63afee6736ab6df666f4d7430824486b476f66fcc66b5",
    ),
    (
        24,
        "0024_notification_channel_progress",
        "6a404cb0214ef65a29a6895bd8dfec5a0ddaa962a0e8b1f02b341ee3226d0fa8",
    ),
    (
        25,
        "0025_persona_allowed_paths",
        "500ff66a26edc430ce39c34eb99c6f3d0696378d2a6e52b1b2838845b8e3b02c",
    ),
    (
        26,
        "0026_persona_tier_enforcement",
        "e24091d7064b2ce94e8b3f2f02a2683cf1952ad37b5fc50a0c41d82f576a8dd3",
    ),
    (
        27,
        "0027_persona_workspace_dir",
        "a7d85d39d7a2fc9592a6ae0eb4403a758e09b9e83c1e720e4161e1165b1bf886",
    ),
    (28, "0028_consultations", "2d4fdb05e5012174e41e18bfe9fb451ebad777bbe9b6c0cc5fceafa91cc49feb"),
    (
        29,
        "0029_consultation_synthesizer",
        "8d5998181edd9f4b241adc53829f587a1fd6b09d2d9454a93c762e523549e49e",
    ),
    (
        30,
        "0030_consultation_rounds",
        "e2a58cde197cc0801bc6c965ca9ce03afad031d821e5cfaa9bcd89566198ac13",
    ),
)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _insert_snapshot(connection: sqlite3.Connection, digest: str = _DIGEST_A) -> None:
    connection.execute(
        "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
        (digest, _CREATED_AT),
    )


def test_v31_appends_exact_tables_guards_and_foreign_key_without_prefix_drift() -> None:
    connection = _connection()

    assert apply_migrations(connection, MIGRATIONS[:30]) == tuple(range(1, 31))
    assert apply_migrations(connection, MIGRATIONS[:31]) == (31,)
    assert (
        tuple((item.version, item.name, item.checksum) for item in MIGRATIONS[:30])
        == _FROZEN_V1_V30
    )
    assert MIGRATIONS[30].name == "0031_system_snapshots"
    assert (
        MIGRATIONS[30].checksum
        == "847ce32541b7196604dbaae43c0c49fce08d2f5eef2a98a81088cab4be434cb7"
    )

    snapshot_columns = connection.execute("PRAGMA table_info(system_snapshots)").fetchall()
    assert [row["name"] for row in snapshot_columns] == [
        "snapshot_digest",
        "schema_version",
        "components_json",
        "first_seen_at",
    ]
    assert [row["pk"] for row in snapshot_columns] == [1, 0, 0, 0]

    binding_columns = connection.execute("PRAGMA table_info(run_system_bindings)").fetchall()
    assert [row["name"] for row in binding_columns] == [
        "memorial_id",
        "attempt_id",
        "snapshot_digest",
        "generation_ids_json",
        "created_at",
    ]
    assert [row["pk"] for row in binding_columns] == [1, 2, 0, 0, 0]
    assert binding_columns[3]["dflt_value"] == "'[]'"

    foreign_keys = connection.execute("PRAGMA foreign_key_list(run_system_bindings)").fetchall()
    assert [(row["table"], row["from"], row["to"], row["on_delete"]) for row in foreign_keys] == [
        ("system_snapshots", "snapshot_digest", "snapshot_digest", "RESTRICT")
    ]

    objects = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN ({})".format(
                ",".join("?" for _ in _SYSTEM_SNAPSHOTS_OBJECT_NAMES)
            ),
            _SYSTEM_SNAPSHOTS_OBJECT_NAMES,
        ).fetchall()
    }
    assert objects == set(_SYSTEM_SNAPSHOTS_OBJECT_NAMES)
    connection.close()


@pytest.mark.parametrize(
    ("sql", "params"),
    [
        (
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
            ("a" * 63, _CREATED_AT),
        ),
        (
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
            ("A" * 64, _CREATED_AT),
        ),
        (
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', ?)",
            ("z" * 64, _CREATED_AT),
        ),
        (
            "INSERT INTO system_snapshots VALUES (?, 1, '[]', ?)",
            (_DIGEST_A, _CREATED_AT),
        ),
        (
            "INSERT INTO system_snapshots VALUES (?, 1, '{}', '   ')",
            (_DIGEST_A,),
        ),
    ],
)
def test_v31_snapshot_checks_reject_invalid_rows(
    sql: str,
    params: tuple[str, ...],
) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:31])
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(sql, params)
    connection.close()


@pytest.mark.parametrize(
    ("memorial_id", "attempt_id", "generation_ids_json", "created_at"),
    [
        ("   ", "attempt-1", "[]", _CREATED_AT),
        ("memorial-1", "   ", "[]", _CREATED_AT),
        ("memorial-1", "attempt-1", "{}", _CREATED_AT),
        ("memorial-1", "attempt-1", "[]", "   "),
    ],
)
def test_v31_binding_checks_reject_invalid_rows(
    memorial_id: str,
    attempt_id: str,
    generation_ids_json: str,
    created_at: str,
) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:31])
    _insert_snapshot(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO run_system_bindings VALUES (?, ?, ?, ?, ?)",
            (memorial_id, attempt_id, _DIGEST_A, generation_ids_json, created_at),
        )
    connection.close()


def test_v31_guards_reject_mutation_and_replace_but_allow_binding_delete() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:31])
    _insert_snapshot(connection)
    _insert_snapshot(connection, _DIGEST_B)
    connection.execute(
        "INSERT INTO run_system_bindings VALUES (?, ?, ?, '[]', ?)",
        ("memorial-1", "attempt-1", _DIGEST_A, _CREATED_AT),
    )

    with pytest.raises(sqlite3.IntegrityError, match="system snapshot is immutable"):
        connection.execute(
            "UPDATE system_snapshots SET components_json='{" + '"kernel":"' + "a" * 64 + '"}' + "' "
            "WHERE snapshot_digest=?",
            (_DIGEST_A,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="system snapshot is immutable"):
        connection.execute(
            "INSERT OR REPLACE INTO system_snapshots VALUES (?, 1, '{}', ?)",
            (_DIGEST_A, _CREATED_AT),
        )
    with pytest.raises(sqlite3.IntegrityError, match="run system binding is immutable"):
        connection.execute(
            "UPDATE run_system_bindings SET snapshot_digest=? "
            "WHERE memorial_id='memorial-1' AND attempt_id='attempt-1'",
            (_DIGEST_B,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="run system binding is immutable"):
        connection.execute(
            "INSERT OR REPLACE INTO run_system_bindings VALUES (?, ?, ?, '[]', ?)",
            ("memorial-1", "attempt-1", _DIGEST_B, _CREATED_AT),
        )

    connection.execute(
        "DELETE FROM run_system_bindings WHERE memorial_id=? AND attempt_id=?",
        ("memorial-1", "attempt-1"),
    )
    assert connection.execute("SELECT COUNT(*) FROM run_system_bindings").fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match="system snapshot is immutable"):
        connection.execute(
            "DELETE FROM system_snapshots WHERE snapshot_digest=?",
            (_DIGEST_A,),
        )
    connection.close()


def test_v31_exact_schema_replay_preserves_existing_row_and_rowid() -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:30])
    for statement in _SYSTEM_SNAPSHOTS_STATEMENTS:
        connection.execute(statement)
    _insert_snapshot(connection)
    connection.commit()
    before = connection.execute(
        "SELECT rowid, * FROM system_snapshots WHERE snapshot_digest=?",
        (_DIGEST_A,),
    ).fetchone()

    assert apply_migrations(connection, MIGRATIONS[:31]) == (31,)
    after = connection.execute(
        "SELECT rowid, * FROM system_snapshots WHERE snapshot_digest=?",
        (_DIGEST_A,),
    ).fetchone()
    assert tuple(after) == tuple(before)
    connection.close()


@pytest.mark.parametrize("mode", ["partial", "drifted"])
def test_v31_rejects_partial_or_drifted_owned_schema_atomically(mode: str) -> None:
    connection = _connection()
    apply_migrations(connection, MIGRATIONS[:30])
    statement = _SYSTEM_SNAPSHOTS_STATEMENTS[0]
    if mode == "drifted":
        statement = statement.replace(
            "first_seen_at TEXT NOT NULL CHECK (length(trim(first_seen_at)) > 0)",
            "first_seen_at TEXT NOT NULL",
        )
    connection.execute(statement)
    connection.commit()

    with pytest.raises(MigrationExecutionError, match="0031_system_snapshots"):
        apply_migrations(connection, MIGRATIONS[:31])

    assert (
        connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE version=31").fetchone()[0]
        == 0
    )
    remaining = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN ({})".format(
                ",".join("?" for _ in _SYSTEM_SNAPSHOTS_OBJECT_NAMES)
            ),
            _SYSTEM_SNAPSHOTS_OBJECT_NAMES,
        ).fetchall()
    }
    assert remaining == {"system_snapshots"}
    connection.close()
