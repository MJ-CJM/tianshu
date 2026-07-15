"""V13 binds governed apply projections to durable generic decisions."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime

import pytest

from tianshu.storage.migration_ledger import (
    Migration,
    MigrationConnection,
    MigrationExecutionError,
    apply_migrations,
)
from tianshu.storage.migrations import MIGRATIONS

_NOW = datetime(2026, 7, 16, tzinfo=UTC).isoformat()
_EXPIRES = datetime(2026, 7, 17, tzinfo=UTC).isoformat()
_SHA = "a" * 40


def _staging_digest(suffix: str) -> str:
    return hashlib.sha256(f"staging:{suffix}".encode()).hexdigest()


def _digest(label: str, suffix: str) -> str:
    return hashlib.sha256(f"{label}:{suffix}".encode()).hexdigest()


def _connection(*, through: int = 13) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    apply_migrations(connection, MIGRATIONS[:through])
    return connection


def _insert_workspace_envelope(connection: sqlite3.Connection, suffix: str) -> None:
    lease_id = f"lease-{suffix}"
    restore_id = f"restore-{suffix}"
    changes_id = f"changes-{suffix}"
    connection.execute(
        """
        INSERT INTO workspace_leases (
            id, schema_version, run_id, lineage_root_run_id, parent_run_id,
            attempt, source_kind, apply_mode, source_root, source_repository_id,
            source_git_dir, source_git_dir_identity, base_revision, staging_root,
            created_at
        ) VALUES (?, '1', ?, ?, NULL, 0, 'git', 'governed', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            lease_id,
            f"run-{suffix}",
            f"run-{suffix}",
            f"/source/{suffix}",
            f"repo-{suffix}",
            f"/source/{suffix}/.git",
            "1" * 64,
            _SHA,
            f"/staging/{suffix}",
            _NOW,
        ),
    )
    connection.execute(
        """
        INSERT INTO workspace_lease_states (lease_id, version, state, detail, created_at)
        VALUES (?, 1, 'starting', NULL, ?)
        """,
        (lease_id, _NOW),
    )
    connection.execute(
        """
        INSERT INTO workspace_staging_identities (
            lease_id, schema_version, staging_root, git_dir, git_dir_identity,
            source_repository_id, base_revision, created_at
        ) VALUES (?, '1', ?, ?, ?, ?, ?, ?)
        """,
        (
            lease_id,
            f"/staging/{suffix}",
            f"/source/{suffix}/.git/worktrees/{lease_id}",
            _staging_digest(suffix),
            f"repo-{suffix}",
            _SHA,
            _NOW,
        ),
    )
    connection.execute(
        """
        INSERT INTO workspace_lease_states (lease_id, version, state, detail, created_at)
        VALUES (?, 2, 'active', NULL, ?)
        """,
        (lease_id, _NOW),
    )
    connection.execute(
        """
        INSERT INTO restore_points (
            id, schema_version, lease_id, source_repository_id, source_root,
            source_git_dir, source_git_dir_identity, base_revision,
            source_head_revision, source_head_ref, source_index_tree,
            source_status_hash, canonical_json, content_hash, created_at
        ) VALUES (?, '1', ?, ?, ?, ?, ?, ?, ?, 'refs/heads/main', ?, ?, '{}', ?, ?)
        """,
        (
            restore_id,
            lease_id,
            f"repo-{suffix}",
            f"/source/{suffix}",
            f"/source/{suffix}/.git",
            "1" * 64,
            _SHA,
            _SHA,
            "b" * 40,
            "c" * 64,
            "d" * 64,
            _NOW,
        ),
    )
    connection.execute(
        """
        INSERT INTO canonical_change_sets (
            id, schema_version, lease_id, restore_point_id,
            source_repository_id, base_revision, sequence, canonical_json,
            content_hash, created_at
        ) VALUES (?, '1', ?, ?, ?, ?, 1, '{}', ?, ?)
        """,
        (changes_id, lease_id, restore_id, f"repo-{suffix}", _SHA, "e" * 64, _NOW),
    )


def _insert_apply_decision(
    connection: sqlite3.Connection,
    suffix: str,
    *,
    decision_id: str | None = None,
    decision_request_id: str | None = None,
) -> str:
    identifier = decision_id or f"apply-{suffix}"
    columns = ""
    values = ""
    parameters: tuple[object, ...] = ()
    if decision_request_id is not None:
        columns = ", decision_request_id"
        values = ", ?"
        parameters = (decision_request_id,)
    connection.execute(
        f"""
        INSERT INTO apply_decisions (
            id, schema_version, run_id, lease_id, restore_point_id,
            restore_point_hash, change_set_id, change_set_hash,
            source_repository_id, source_root, source_git_dir_identity,
            base_revision, source_head_revision, source_head_ref,
            source_index_tree, source_status_hash, staging_root,
            staging_git_dir_identity, principal_digest, apply_scope, reason,
            decision_hash, token_hash, expires_at, created_at{columns}
        ) VALUES (
            ?, '1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'refs/heads/main',
            ?, ?, ?, ?, ?, 'workspace', 'reviewed', ?, ?, ?, ?{values}
        )
        """,
        (
            identifier,
            f"run-{suffix}",
            f"lease-{suffix}",
            f"restore-{suffix}",
            "d" * 64,
            f"changes-{suffix}",
            "e" * 64,
            f"repo-{suffix}",
            f"/source/{suffix}",
            "1" * 64,
            _SHA,
            _SHA,
            "b" * 40,
            "c" * 64,
            f"/staging/{suffix}",
            _staging_digest(suffix),
            "f" * 64,
            _digest("decision", suffix),
            _digest("token", suffix),
            _EXPIRES,
            _NOW,
            *parameters,
        ),
    )
    return identifier


def _insert_decision_state(connection: sqlite3.Connection, decision_id: str, state: str) -> None:
    connection.execute(
        """
        INSERT INTO apply_decision_states (decision_id, version, state, receipt_id, created_at)
        VALUES (?, 1, 'pending', NULL, ?)
        """,
        (decision_id, _NOW),
    )
    if state != "pending":
        receipt_id = "receipt-consumed" if state == "consumed" else None
        connection.execute(
            """
            INSERT INTO apply_decision_states
                (decision_id, version, state, receipt_id, created_at)
            VALUES (?, 2, ?, ?, ?)
            """,
            (decision_id, state, receipt_id, _NOW),
        )


def _insert_generic_decision(
    connection: sqlite3.Connection,
    decision_id: str,
    *,
    kind: str = "governed_apply",
    status: str = "resolved",
    action: str | None = "approve",
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO edicts (id, goal, created_at) VALUES ('edict-v13', 'apply', ?)",
        (_NOW,),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO memorials (id, edict_id, status, created_at)
        VALUES ('memorial-v13', 'edict-v13', 'submitted', ?)
        """,
        (_NOW,),
    )
    connection.execute(
        """
        INSERT INTO decision_requests (
            decision_request_id, schema_version, kind, edict_id, memorial_id,
            request_key, payload_json, payload_hash, requested_by, expires_at,
            status, version, created_at, updated_at
        ) VALUES (?, 1, ?, 'edict-v13', 'memorial-v13', ?, '{}', ?,
                  'user:operator', ?, ?, ?, ?, ?)
        """,
        (
            decision_id,
            kind,
            f"apply:{decision_id}",
            "0" * 64,
            _EXPIRES,
            status,
            2 if status == "resolved" else 1,
            _NOW,
            _NOW,
        ),
    )
    if action is not None:
        connection.execute(
            """
            INSERT INTO decision_resolutions (
                decision_request_id, action, reason, payload_json,
                actor_principal_id, actor_display_name, resolved_at
            ) VALUES (?, ?, 'reviewed', '{"schema_version":1}',
                      'user:reviewer', 'Reviewer', ?)
            """,
            (decision_id, action, _NOW),
        )


def test_live_migration_tail_is_v13_without_drifting_v1_to_v12() -> None:
    assert tuple(item.version for item in MIGRATIONS) == tuple(range(1, 14))
    assert (MIGRATIONS[-1].version, MIGRATIONS[-1].name) == (
        13,
        "0013_governed_apply_decision_binding",
    )
    assert MIGRATIONS[-1].checksum == (
        "e3d72d6b4558437d0a2fd7d3a6fba8c1e4261f56c4ef4168b1f9eb3049da412e"
    )


def test_v13_failure_rolls_back_binding_schema_and_ledger() -> None:
    connection = _connection(through=12)
    migration = MIGRATIONS[12]

    def fail_after_upgrade(active: MigrationConnection) -> None:
        migration.upgrade(active)
        raise RuntimeError("stop after v13")

    failing = Migration(
        version=migration.version,
        name=migration.name,
        checksum=migration.checksum,
        upgrade=fail_after_upgrade,
    )
    try:
        with pytest.raises(MigrationExecutionError, match=migration.name):
            apply_migrations(connection, (*MIGRATIONS[:12], failing))

        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(apply_decisions)").fetchall()
        }
        assert "decision_request_id" not in columns
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 12
    finally:
        connection.close()


def test_v13_preserves_legacy_states_and_receipt_without_fabricating_authority() -> None:
    connection = _connection(through=12)
    try:
        for suffix, state in (
            ("consumed", "consumed"),
            ("pending", "pending"),
            ("expired", "expired"),
            ("revoked", "revoked"),
        ):
            _insert_workspace_envelope(connection, suffix)
            decision_id = _insert_apply_decision(connection, suffix)
            _insert_decision_state(connection, decision_id, state)
        connection.execute(
            """
            INSERT INTO apply_receipts (
                id, schema_version, decision_id, decision_hash, lease_id,
                change_set_id, change_set_hash, outcome, detail,
                pre_source_head, pre_source_status_hash, post_source_head,
                post_source_status_hash, rollback_status, failure_code,
                evidence_json, created_at
            ) VALUES (
                'receipt-consumed', '1', 'apply-consumed', ?, 'lease-consumed',
                'changes-consumed', ?, 'succeeded', 'preserved', ?, ?, ?, ?,
                'not_required', NULL, '[]', ?
            )
            """,
            (
                _digest("decision", "consumed"),
                "e" * 64,
                _SHA,
                "c" * 64,
                _SHA,
                "c" * 64,
                _NOW,
            ),
        )
        before_states = connection.execute(
            "SELECT decision_id, version, state FROM apply_decision_states ORDER BY 1, 2"
        ).fetchall()
        before_receipt = connection.execute("SELECT * FROM apply_receipts").fetchone()
        connection.commit()

        assert apply_migrations(connection, MIGRATIONS) == (13,)

        assert (
            connection.execute(
                "SELECT decision_id, version, state FROM apply_decision_states ORDER BY 1, 2"
            ).fetchall()
            == before_states
        )
        assert connection.execute("SELECT * FROM apply_receipts").fetchone() == before_receipt
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM apply_decisions WHERE decision_request_id IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM decision_requests").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("kind", "status", "action", "message"),
    [
        ("tool", "resolved", "approve", "governed apply"),
        ("governed_apply", "pending", None, "resolved approve"),
        ("governed_apply", "resolved", "reject", "resolved approve"),
    ],
)
def test_v13_rejects_non_approved_generic_projection(
    kind: str, status: str, action: str | None, message: str
) -> None:
    connection = _connection()
    try:
        _insert_workspace_envelope(connection, "invalid")
        _insert_generic_decision(
            connection,
            "decision-invalid",
            kind=kind,
            status=status,
            action=action,
        )
        with pytest.raises(sqlite3.IntegrityError, match=message):
            _insert_apply_decision(
                connection,
                "invalid",
                decision_id="decision-invalid",
                decision_request_id="decision-invalid",
            )
    finally:
        connection.close()


def test_v13_requires_projection_id_identity_and_partial_unique_fk() -> None:
    connection = _connection()
    try:
        index = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_apply_decisions_decision_request",),
        ).fetchone()
        assert index is not None
        assert "UNIQUE" in index[0].upper()
        assert "WHERE decision_request_id IS NOT NULL" in index[0]
        assert any(
            row[2] == "decision_requests"
            and row[3] == "decision_request_id"
            and row[4] == "decision_request_id"
            for row in connection.execute("PRAGMA foreign_key_list(apply_decisions)").fetchall()
        )

        _insert_workspace_envelope(connection, "bound")
        _insert_generic_decision(connection, "decision-bound")
        with pytest.raises(sqlite3.IntegrityError, match="identity"):
            _insert_apply_decision(
                connection,
                "bound",
                decision_id="apply-other",
                decision_request_id="decision-bound",
            )
        _insert_apply_decision(
            connection,
            "bound",
            decision_id="decision-bound",
            decision_request_id="decision-bound",
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
