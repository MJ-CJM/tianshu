from __future__ import annotations

import sqlite3

import pytest

from tianshu.storage.migration_ledger import MigrationExecutionError, apply_migrations
from tianshu.storage.migrations import MIGRATIONS


def test_v16_appends_artifact_and_evidence_objects_without_drifting_prefix() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    frozen = [(item.version, item.name, item.checksum) for item in MIGRATIONS[:15]]

    assert apply_migrations(connection, MIGRATIONS[:15]) == tuple(range(1, 16))
    assert apply_migrations(connection, MIGRATIONS[:16]) == (16,)
    assert [(item.version, item.name, item.checksum) for item in MIGRATIONS[:15]] == frozen
    assert MIGRATIONS[15].name == "0016_artifacts_evidence"

    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"artifact_records", "evidence_bundles"} <= tables


def test_v16_database_guards_closed_snapshot_and_artifact_metadata() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    apply_migrations(connection, MIGRATIONS[:16])
    connection.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES ('e', 'g', '2026-07-17T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES ('m', 'e', 'completed', '2026-07-17T00:00:00+00:00')"
    )
    connection.execute(
        """
        INSERT INTO evidence_bundles
            (bundle_id, schema_version, edict_id, memorial_id, status, body_json,
             content_hash, version, created_at, closed_at)
        VALUES ('b', '1.0', 'e', 'm', 'closed', '{}', ?, 2, ?, ?)
        """,
        ("a" * 64, "2026-07-17T00:00:00+00:00", "2026-07-17T00:00:01+00:00"),
    )
    for statement in (
        "UPDATE evidence_bundles SET body_json='[]' WHERE bundle_id='b'",
        "DELETE FROM evidence_bundles WHERE bundle_id='b'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(statement)


def test_v16_adopts_only_a_complete_exact_preledger_shape() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    apply_migrations(connection, MIGRATIONS[:16])
    connection.execute("DELETE FROM schema_migrations WHERE version=16")
    connection.commit()

    assert apply_migrations(connection, MIGRATIONS[:16]) == (16,)

    incompatible = sqlite3.connect(":memory:")
    incompatible.execute("PRAGMA foreign_keys=ON")
    apply_migrations(incompatible, MIGRATIONS[:15])
    incompatible.execute("CREATE TABLE artifact_records (digest TEXT PRIMARY KEY)")
    incompatible.commit()

    with pytest.raises(MigrationExecutionError, match="migration 16"):
        apply_migrations(incompatible, MIGRATIONS[:16])
