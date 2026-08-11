"""V24 per-channel notification progress migration contracts."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS


def test_v24_adds_empty_progress_to_existing_delivery_without_drifting_v1_to_v23() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    frozen = [(item.version, item.name, item.checksum) for item in MIGRATIONS[:23]]
    assert apply_migrations(connection, MIGRATIONS[:23]) == tuple(range(1, 24))
    connection.execute(
        """
        INSERT INTO internal_notification_deliveries (
            delivery_id, event_id, event_type, correlation_id, status,
            available_at, deadline_at, max_attempts, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'pending', ?, ?, 3, ?, ?)
        """,
        (
            "d" * 64,
            "event-before-channel-progress",
            "execution.failed",
            "correlation-before-channel-progress",
            "2026-07-31T00:00:00+00:00",
            "2026-07-31T01:00:00+00:00",
            "2026-07-31T00:00:00+00:00",
            "2026-07-31T00:00:00+00:00",
        ),
    )
    connection.commit()

    # 锁定切片而非整个 MIGRATIONS：本用例只验证 v24 自身的效果，
    # 不应随后续迁移追加而改动。
    assert apply_migrations(connection, MIGRATIONS[:24]) == (24,)
    assert [(item.version, item.name, item.checksum) for item in MIGRATIONS[:23]] == frozen
    assert MIGRATIONS[23].name == "0024_notification_channel_progress"
    raw = connection.execute(
        """
        SELECT accepted_channels_json
        FROM internal_notification_deliveries
        WHERE delivery_id=?
        """,
        ("d" * 64,),
    ).fetchone()
    assert raw is not None
    assert json.loads(raw[0]) == []
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            UPDATE internal_notification_deliveries
            SET accepted_channels_json='{}'
            WHERE delivery_id=?
            """,
            ("d" * 64,),
        )
    connection.close()
