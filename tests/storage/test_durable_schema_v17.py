"""Append-only v17 delivery and correlation schema ownership."""

from __future__ import annotations

import sqlite3

from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS


def test_v17_appends_delivery_without_drifting_v1_to_v16() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    frozen = [(item.version, item.name, item.checksum) for item in MIGRATIONS[:16]]
    assert apply_migrations(connection, MIGRATIONS[:16]) == tuple(range(1, 17))
    assert apply_migrations(connection, MIGRATIONS[:17]) == (17,)
    assert [(item.version, item.name, item.checksum) for item in MIGRATIONS[:16]] == frozen
    assert MIGRATIONS[16].name == "0017_internal_notification_delivery"

    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "internal_notification_deliveries" in tables
    for table in (
        "outbox_events",
        "decision_requests",
        "run_states",
        "execution_attempts",
        "side_effect_journal",
        "evidence_bundles",
    ):
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        assert "correlation_id" in columns
    connection.close()
