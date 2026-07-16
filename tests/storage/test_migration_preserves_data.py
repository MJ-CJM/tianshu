"""G0 v0.4.2 baseline migration and data-preservation contracts."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from tianshu.storage import Storage
from tianshu.storage.migration_ledger import MigrationExecutionError, apply_migrations
from tianshu.storage.migrations import MIGRATIONS

_BASELINE_NAME = "0001_adopt_v042_baseline"
_AUTH_MIGRATION_NAME = "0002_auth_tokens"
_GOVERNANCE_MIGRATION_NAME = "0003_governance_contracts"
_WORKSPACE_MIGRATION_NAME = "0004_workspace_foundation"
_GOVERNED_APPLY_MIGRATION_NAME = "0005_governed_apply_bindings"
_SEED_PERSONAS_MIGRATION_NAME = "0006_seed_default_personas"
_SYSTEM_AUDIT_MIGRATION_NAME = "0007_system_audit_events"
_MCP_SECRET_MAPPING_MIGRATION_NAME = "0008_encrypt_mcp_secret_mappings"
_DURABLE_EDICT_INGRESS_MIGRATION_NAME = "0009_durable_edict_ingress"
_TELEGRAM_SEEN_IDENTITY_MIGRATION_NAME = "0010_telegram_seen_instance_identity"
_DECISIONS_RUN_STATE_MIGRATION_NAME = "0011_decisions_run_state"
_DECISION_RUN_STATE_GUARDS_MIGRATION_NAME = "0012_decision_run_state_guards"
_GOVERNED_APPLY_DECISION_BINDING_MIGRATION_NAME = "0013_governed_apply_decision_binding"
_EXECUTION_ATTEMPT_LEDGER_MIGRATION_NAME = "0014_execution_attempt_ledger"
_COMPLETE_MIGRATION_LEDGER = [
    (1, _BASELINE_NAME),
    (2, _AUTH_MIGRATION_NAME),
    (3, _GOVERNANCE_MIGRATION_NAME),
    (4, _WORKSPACE_MIGRATION_NAME),
    (5, _GOVERNED_APPLY_MIGRATION_NAME),
    (6, _SEED_PERSONAS_MIGRATION_NAME),
    (7, _SYSTEM_AUDIT_MIGRATION_NAME),
    (8, _MCP_SECRET_MAPPING_MIGRATION_NAME),
    (9, _DURABLE_EDICT_INGRESS_MIGRATION_NAME),
    (10, _TELEGRAM_SEEN_IDENTITY_MIGRATION_NAME),
    (11, _DECISIONS_RUN_STATE_MIGRATION_NAME),
    (12, _DECISION_RUN_STATE_GUARDS_MIGRATION_NAME),
    (13, _GOVERNED_APPLY_DECISION_BINDING_MIGRATION_NAME),
    (14, _EXECUTION_ATTEMPT_LEDGER_MIGRATION_NAME),
]
_POST_BASELINE_TABLES = {
    "auth_tokens",
    "requested_governance_contracts",
    "effective_governance_contracts",
    "workspace_leases",
    "workspace_lease_states",
    "workspace_staging_identities",
    "restore_points",
    "canonical_change_sets",
    "apply_decisions",
    "apply_decision_states",
    "apply_receipts",
    "system_audit_events",
    "outbox_events",
    "submission_idempotency",
    "outbox_consumptions",
    "decision_requests",
    "decision_resolutions",
    "run_states",
    "execution_attempts",
}
_POST_BASELINE_INDEXES = {
    "idx_auth_tokens_principal",
    "idx_auth_tokens_family",
    "idx_auth_tokens_active",
    "idx_requested_governance_hash",
    "idx_effective_governance_edict",
    "idx_effective_governance_hash",
    "idx_workspace_leases_lineage",
    "idx_restore_points_repository",
    "idx_change_sets_restore",
    "idx_apply_decisions_lease",
    "idx_apply_decisions_decision_request",
    "idx_apply_receipts_lease",
    "idx_system_audit_correlation_sequence",
    "idx_system_audit_action_sequence",
    "idx_outbox_claim",
    "idx_outbox_edict",
    "idx_decisions_pending",
    "idx_decisions_memorial",
    "idx_run_states_edict",
    "idx_execution_attempts_active_memorial",
    "idx_execution_attempts_claim",
    "idx_execution_attempts_memorial",
}
_V042_OWNED_TABLE_MANIFEST = (
    48,
    "b163e6d87e60fdb09349e82bf07489e8e64f633bde790178d424d77d6e2731f0",
)
_V042_NAMED_INDEX_MANIFEST = (
    31,
    "cf23891f666bd8d7b23e3524d007dda7f0f3143075875cc7b20f17887d6db4db",
)
_DATA_TABLES = (
    "edicts",
    "memorials",
    "supervision_reports",
    "cost_ledger",
    "personas",
    "persona_metrics",
)
_HISTORICAL_CORE_PAYLOAD_COLUMNS = {
    "pending_notifications": (
        "id",
        "edict_id",
        "memorial_id",
        "message_json",
        "channels_json",
        "created_at",
    ),
    "memorials": (
        "id",
        "edict_id",
        "instruction",
        "status",
        "summary",
        "result",
        "final_output",
        "usage_json",
        "error",
        "created_at",
        "started_at",
        "completed_at",
        "attempt",
        "parent_memorial_id",
        "review_status",
        "audit_json",
        "artifacts_json",
        "timeline_json",
        "dag_node_id",
        "persona_id",
        "runtime_override_json",
        "acceptance_override_json",
        "reasoning_content",
        "universe_id",
        "feedback_score",
        "last_heartbeat_at",
        "failure_reason",
    ),
    "events": (
        "id",
        "edict_id",
        "memorial_id",
        "event_type",
        "payload_json",
        "created_at",
    ),
}
_REQUIRED_TABLES = {
    "schedule_run",
    "eval_sets",
    "eval_runs",
    "estop_state",
    "shadow_snapshots",
    "kg_triples",
    "historian_log",
    "pending_notifications",
    "pending_steers",
    "feature_flags",
    "evolution_petitions",
    "system_audit_events",
}
_REQUIRED_COLUMNS = {
    "edicts": {
        "status",
        "title",
        "idempotency_key",
        "source",
        "submitter",
        "priority",
        "review_policy",
        "output_format",
        "constraints_json",
        "schedule_json",
        "dispatch_json",
        "runtime_json",
        "metadata_json",
        "assigned_persona_id",
        "planner_persona_id",
        "plan_review",
        "acceptance_json",
        "execution_profile",
    },
    "memorials": {
        "instruction",
        "attempt",
        "parent_memorial_id",
        "review_status",
        "audit_json",
        "artifacts_json",
        "timeline_json",
        "dag_node_id",
        "persona_id",
        "reasoning_content",
        "final_output",
        "universe_id",
        "feedback_score",
        "last_heartbeat_at",
        "failure_reason",
    },
    "personas": {"skills_allowed", "llm_config_name", "memory_global_read"},
    "skill_metrics": {
        "state",
        "pinned",
        "archived_at",
        "absorbed_into",
        "human_curated",
        "last_human_action",
    },
    "engine_preferences": {"scrapling_dynamic_enabled", "scrapling_stealthy_enabled"},
    "cost_budgets": {"period_start"},
}
_REQUIRED_INDEXES = {
    "idx_memorials_universe_id",
    "idx_memorials_failure_reason",
    "idx_netcreds_provider",
    "idx_schedule_run_source",
    "idx_eval_runs_fingerprint",
    "idx_shadow_edict",
    "idx_kg_sp",
    "idx_kg_valid",
    "idx_steers_edict",
    "idx_petitions_status",
    "idx_system_audit_correlation_sequence",
    "idx_system_audit_action_sequence",
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _rows(conn: sqlite3.Connection, table: str) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))


def _payload_rows(
    conn: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id").fetchall()
    )


def _snapshot(conn: sqlite3.Connection) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {table: _rows(conn, table) for table in _DATA_TABLES}


def _build_canonical_preledger(
    path: Path, *, prior_mcp_schema: bool = False
) -> dict[str, tuple[tuple[object, ...], ...]]:
    storage = Storage(str(path))
    storage.init_db()
    conn = storage._conn
    conn.executescript(
        """
        INSERT INTO edicts (id, title, goal, context, created_at)
        VALUES ('edict-1', 'title', 'goal', 'context', '2026-07-11T00:00:00+00:00');
        INSERT INTO memorials
            (id, edict_id, status, summary, result, final_output, usage_json,
             error, created_at)
        VALUES
            ('memorial-1', 'edict-1', 'completed', 'summary', 'result', 'final',
             '{"total_tokens":7}', NULL, '2026-07-11T00:01:00+00:00');
        INSERT INTO supervision_reports
            (edict_id, memorial_id, persona_id, persona_name, final_status,
             iterations_count, total_cost_cny, report_json, created_at)
        VALUES
            ('edict-1', 'memorial-1', 'persona-1', '御史', 'completed',
             2, 1.25, '{"verdict":"keep"}', '2026-07-11T00:02:00+00:00');
        INSERT INTO cost_ledger
            (id, edict_id, memorial_id, provider_name, model, prompt_tokens,
             completion_tokens, total_tokens, cost_cny, created_at)
        VALUES
            ('cost-1', 'edict-1', 'memorial-1', 'provider', 'model', 3, 4, 7,
             1.25, '2026-07-11T00:03:00+00:00');
        INSERT INTO personas (id, name, department, title, created_at, updated_at)
        VALUES ('persona-1', '御史', 'ducha', '监察',
                '2026-07-11T00:00:00+00:00', '2026-07-11T00:00:00+00:00');
        INSERT INTO persona_metrics
            (persona_id, total_executions, completed, success_rate, total_tokens,
             total_cost_cny, updated_at)
        VALUES
            ('persona-1', 2, 2, 1.0, 7, 1.25, '2026-07-11T00:03:00+00:00');

        """
    )
    if prior_mcp_schema:
        conn.executescript(
            """
            DROP TABLE execution_attempts;
            DROP TABLE run_states;
            DROP TABLE decision_resolutions;
            DROP TABLE decision_requests;

            DROP TABLE outbox_consumptions;
            DROP TABLE submission_idempotency;
            DROP TABLE outbox_events;

            -- This fixture represents a schema from before the live v10 rebuild.
            DROP TABLE telegram_seen_messages;
            CREATE TABLE telegram_seen_messages (
                update_id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL DEFAULT 'telegram-default',
                seen_at TIMESTAMP NOT NULL
            );
            CREATE INDEX idx_tg_seen_at ON telegram_seen_messages(seen_at);

            -- Historical adapters consume the canonical pre-v8 table shape.
            DROP TABLE mcp_server_overrides;
            CREATE TABLE mcp_server_overrides (
                name                 TEXT PRIMARY KEY,
                enabled              INTEGER,
                env_json             TEXT,
                tools_include_json   TEXT,
                tools_exclude_json   TEXT,
                transport            TEXT,
                command              TEXT,
                args_json            TEXT,
                url                  TEXT,
                headers_json         TEXT,
                default_tier         INTEGER,
                timeout              INTEGER,
                connect_timeout      INTEGER,
                tool_overrides_json  TEXT,
                updated_at           TEXT NOT NULL
            );
            """
        )
    conn.execute("DROP TABLE IF EXISTS schema_migrations")
    before = _snapshot(conn)
    storage.close()
    return before


def _build_historical_core_preledger(
    path: Path,
    *,
    include_orphan_event: bool = False,
    pending_edict_id: str = "edict-1",
) -> dict[str, tuple[tuple[object, ...], ...]]:
    _build_canonical_preledger(path, prior_mcp_schema=True)
    conn = _connect(path)
    conn.executescript(
        """
        -- 历史库形状早于 workspace foundation：真实历史库不含这些表。
        DROP TABLE system_audit_events;
        DROP TABLE apply_receipts;
        DROP TABLE apply_decision_states;
        DROP TABLE apply_decisions;
        DROP TABLE canonical_change_sets;
        DROP TABLE restore_points;
        DROP TABLE workspace_staging_identities;
        DROP TABLE workspace_lease_states;
        DROP TABLE workspace_leases;

        DROP TABLE pending_notifications;
        DROP TABLE events;
        DROP TABLE memorials;

        CREATE TABLE pending_notifications (
            id TEXT PRIMARY KEY,
            edict_id TEXT,
            message_json TEXT NOT NULL,
            rendered TEXT,
            channels_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE memorials (
            id TEXT PRIMARY KEY,
            edict_id TEXT NOT NULL REFERENCES edicts(id),
            status TEXT NOT NULL,
            summary TEXT,
            result TEXT,
            usage_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            instruction TEXT,
            attempt INTEGER NOT NULL DEFAULT 1,
            parent_memorial_id TEXT,
            review_status TEXT NOT NULL DEFAULT 'not_required',
            audit_json TEXT,
            artifacts_json TEXT NOT NULL DEFAULT '[]',
            timeline_json TEXT NOT NULL DEFAULT '[]',
            dag_node_id TEXT,
            persona_id TEXT,
            runtime_override_json TEXT,
            acceptance_override_json TEXT,
            reasoning_content TEXT,
            final_output TEXT,
            universe_id TEXT,
            feedback_score INTEGER NOT NULL DEFAULT 0,
            last_heartbeat_at TEXT,
            failure_reason TEXT
        );
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            edict_id TEXT NOT NULL,
            memorial_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_outer_loop_edict
            ON outer_loop_iterations(edict_id, iteration);

        INSERT INTO memorials
            (id, edict_id, status, summary, result, usage_json, error, created_at,
             final_output)
        VALUES
            ('memorial-1', 'edict-1', 'completed', 'summary', 'result',
             '{"total_tokens":7}', NULL, '2026-07-11T00:01:00+00:00', 'final'),
            ('memorial-future', 'edict-1', 'pending', NULL, NULL, '{}', NULL,
             '2026-07-11T00:03:00+00:00', NULL);
        INSERT INTO events
            (id, edict_id, memorial_id, event_type, payload_json, created_at)
        VALUES
            ('event-valid', 'edict-1', 'memorial-1', 'completed',
             '{"status":"completed"}', '2026-07-11T00:02:00+00:00');
        """
    )
    if include_orphan_event:
        conn.execute(
            """
            INSERT INTO events
                (id, edict_id, memorial_id, event_type, payload_json, created_at)
            VALUES
                ('event-orphan', 'missing-edict', NULL, 'orphan', '{}',
                 '2026-07-11T00:02:00+00:00')
            """
        )
    conn.execute(
        """
        INSERT INTO pending_notifications
            (id, edict_id, message_json, rendered, channels_json, created_at)
        VALUES ('pending-1', ?, '{"message":"legacy"}', 'legacy', '["feishu"]',
                '2026-07-11T00:02:00+00:00')
        """,
        (pending_edict_id,),
    )
    conn.commit()
    expected = {
        "memorials": _payload_rows(
            conn,
            "memorials",
            _HISTORICAL_CORE_PAYLOAD_COLUMNS["memorials"],
        ),
        "events": tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT e.id, e.edict_id, e.memorial_id, e.event_type,
                       e.payload_json, e.created_at
                FROM events AS e
                WHERE EXISTS (SELECT 1 FROM edicts WHERE edicts.id = e.edict_id)
                ORDER BY e.id
                """
            ).fetchall()
        ),
        "pending_notifications": tuple(
            tuple(row)
            for row in conn.execute(
                """
                SELECT p.id, p.edict_id,
                       (
                           SELECT memorials.id
                           FROM memorials
                           WHERE memorials.edict_id = p.edict_id
                             AND memorials.created_at <= p.created_at
                           ORDER BY memorials.created_at DESC, memorials.id DESC
                           LIMIT 1
                       ),
                       p.message_json, p.channels_json, p.created_at
                FROM pending_notifications AS p
                ORDER BY p.id
                """
            ).fetchall()
        ),
    }
    conn.close()
    return expected


def _replace_session_with_legacy(path: Path) -> None:
    conn = _connect(path)
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feishu_session_anchor'"
        ).fetchone()
        is not None
    ):
        conn.executescript(
            """
            DROP TABLE feishu_session_anchor;
            DROP TABLE telegram_session_anchor;
            DROP TABLE feishu_pending_cards;
            DROP TABLE telegram_pending_buttons;
            DROP TABLE feishu_seen_messages;
            DROP TABLE telegram_seen_messages;
            """
        )
    conn.executescript(
        """
        CREATE TABLE feishu_session_anchor (
            chat_id TEXT PRIMARY KEY,
            current_edict_id TEXT,
            updated_at TIMESTAMP NOT NULL
        );
        CREATE TABLE telegram_session_anchor (
            chat_id TEXT PRIMARY KEY,
            current_edict_id TEXT,
            updated_at TIMESTAMP NOT NULL
        );
        CREATE TABLE feishu_pending_cards (
            approval_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        );
        CREATE TABLE telegram_pending_buttons (
            approval_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        );
        CREATE TABLE feishu_seen_messages (
            message_id TEXT PRIMARY KEY,
            seen_at TIMESTAMP NOT NULL
        );
        CREATE TABLE telegram_seen_messages (
            update_id TEXT PRIMARY KEY,
            seen_at TIMESTAMP NOT NULL
        );

        INSERT INTO feishu_session_anchor (chat_id, current_edict_id, updated_at)
        VALUES ('feishu-chat', 'edict-1', '2026-07-11T00:02:00+00:00');
        INSERT INTO telegram_session_anchor (chat_id, current_edict_id, updated_at)
        VALUES ('telegram-chat', 'edict-1', '2026-07-11T00:02:00+00:00');
        INSERT INTO feishu_pending_cards
            (approval_id, chat_id, message_id, kind, created_at)
        VALUES ('feishu-approval', 'feishu-chat', 'feishu-message', 'approval',
                '2026-07-11T00:02:00+00:00');
        INSERT INTO telegram_pending_buttons
            (approval_id, chat_id, message_id, kind, created_at)
        VALUES ('telegram-approval', 'telegram-chat', 'telegram-message', 'approval',
                '2026-07-11T00:02:00+00:00');
        INSERT INTO feishu_seen_messages (message_id, seen_at)
        VALUES ('feishu-seen', '2026-07-11T00:02:00+00:00');
        INSERT INTO telegram_seen_messages (update_id, seen_at)
        VALUES ('telegram-seen', '2026-07-11T00:02:00+00:00');
        """
    )
    conn.close()


def _ledger_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()


def _name_manifest(names: set[str]) -> tuple[int, str]:
    payload = "\n".join(sorted(names)).encode()
    return len(names), hashlib.sha256(payload).hexdigest()


def test_fresh_storage_creates_complete_schema_and_records_baseline_once(tmp_path: Path) -> None:
    path = tmp_path / "fresh.sqlite3"

    storage = Storage(str(path))
    storage.init_db()
    tables = {
        str(row[0])
        for row in storage._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    indexes = {
        str(row[0])
        for row in storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )
    }
    triggers = {
        str(row[0])
        for row in storage._conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    first_ledger = _ledger_rows(storage._conn)
    owned_tables = {
        table
        for table in tables
        if table != "schema_migrations" and not table.startswith("memory_fts")
    }

    assert tables >= _REQUIRED_TABLES
    assert _name_manifest(owned_tables - _POST_BASELINE_TABLES) == _V042_OWNED_TABLE_MANIFEST
    assert _name_manifest(indexes - _POST_BASELINE_INDEXES) == _V042_NAMED_INDEX_MANIFEST
    assert owned_tables >= _POST_BASELINE_TABLES
    for table, required_columns in _REQUIRED_COLUMNS.items():
        actual_columns = {
            str(row["name"]) for row in storage._conn.execute(f'PRAGMA table_info("{table}")')
        }
        assert actual_columns >= required_columns
    assert indexes >= _REQUIRED_INDEXES
    assert {
        "system_audit_events_no_update",
        "system_audit_events_no_delete",
    } <= triggers
    assert [(row["version"], row["name"]) for row in first_ledger] == _COMPLETE_MIGRATION_LEDGER
    assert all(len(row["checksum"]) == 64 for row in first_ledger)
    storage.close()

    reopened = Storage(str(path))
    reopened.init_db()
    assert [tuple(row) for row in _ledger_rows(reopened._conn)] == [
        tuple(row) for row in first_ledger
    ]
    reopened.close()


def test_canonical_preledger_v042_upgrade_only_adds_ledger(tmp_path: Path) -> None:
    path = tmp_path / "canonical.sqlite3"
    before = _build_canonical_preledger(path)

    storage = Storage(str(path))
    storage.init_db()

    assert _snapshot(storage._conn) == before
    assert [
        (row["version"], row["name"]) for row in _ledger_rows(storage._conn)
    ] == _COMPLETE_MIGRATION_LEDGER
    ledger = [tuple(row) for row in _ledger_rows(storage._conn)]
    storage.close()

    reopened = Storage(str(path))
    reopened.init_db()
    assert _snapshot(reopened._conn) == before
    assert [tuple(row) for row in _ledger_rows(reopened._conn)] == ledger
    reopened.close()


def test_v4_shape_preledger_replays_v5_instead_of_adopt(tmp_path: Path) -> None:
    """恰好 V4 完成形状的丢 ledger 库不得被整体采纳，必须逐版重放补齐 V5。"""

    path = tmp_path / "v4-shape.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn, MIGRATIONS[:4])
    conn.execute("DROP TABLE schema_migrations")
    conn.commit()
    conn.close()

    storage = Storage(str(path))
    storage.init_db()
    assert [
        (row["version"], row["name"]) for row in _ledger_rows(storage._conn)
    ] == _COMPLETE_MIGRATION_LEDGER
    columns = {
        str(row[1])
        for row in storage._conn.execute("PRAGMA table_info(apply_decisions)").fetchall()
    }
    assert {
        "run_id",
        "restore_point_hash",
        "source_git_dir_identity",
        "source_head_revision",
        "source_index_tree",
        "source_status_hash",
        "staging_root",
        "staging_git_dir_identity",
    } <= columns
    storage.close()


def test_canonical_preledger_accepts_semantically_equivalent_column_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "canonical-reordered.sqlite3"
    _build_canonical_preledger(path)
    conn = _connect(path)
    conn.executescript(
        """
        DROP TABLE engine_preferences;
        CREATE TABLE engine_preferences (
            updated_at TEXT NOT NULL,
            id TEXT PRIMARY KEY DEFAULT 'default',
            scrapling_stealthy_enabled INTEGER NOT NULL DEFAULT 0,
            scrapling_dynamic_enabled INTEGER NOT NULL DEFAULT 0,
            fallback_mode TEXT,
            search_provider TEXT,
            fetch_chain TEXT NOT NULL DEFAULT '[]'
        );
        """
    )
    conn.close()

    storage = Storage(str(path))
    storage.init_db()
    assert [
        (row["version"], row["name"]) for row in _ledger_rows(storage._conn)
    ] == _COMPLETE_MIGRATION_LEDGER
    storage.close()


def test_canonical_preledger_accepts_exact_memory_fts_trigger_set(tmp_path: Path) -> None:
    path = tmp_path / "canonical-with-fts.sqlite3"
    before = _build_canonical_preledger(path)
    conn = _connect(path)
    assert {
        str(row["name"])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='memory_entries'"
        )
    } == {"memory_fts_insert", "memory_fts_delete", "memory_fts_update"}
    conn.close()

    storage = Storage(str(path))
    storage.init_db()
    assert _snapshot(storage._conn) == before
    storage.close()


def test_historical_preledger_core_shape_upgrades_to_canonical_without_valid_row_loss(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical.sqlite3"
    expected_payload = _build_historical_core_preledger(path)
    storage = Storage(str(path))
    storage.init_db()
    assert (
        storage._conn.execute(
            "SELECT memorial_id FROM pending_notifications WHERE id='pending-1'"
        ).fetchone()[0]
        == "memorial-1"
    )
    assert storage._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        storage._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='idx_outer_loop_edict'"
        ).fetchone()
        is None
    )
    assert [
        (row["version"], row["name"]) for row in _ledger_rows(storage._conn)
    ] == _COMPLETE_MIGRATION_LEDGER
    assert {
        table: _payload_rows(storage._conn, table, columns)
        for table, columns in _HISTORICAL_CORE_PAYLOAD_COLUMNS.items()
    } == expected_payload
    storage.close()


def test_historical_preledger_core_adapter_discards_only_orphan_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-orphan.sqlite3"
    _build_historical_core_preledger(path, include_orphan_event=True)
    storage = Storage(str(path))
    storage.init_db()
    assert [
        tuple(row) for row in storage._conn.execute("SELECT id FROM events ORDER BY id").fetchall()
    ] == [("event-valid",)]
    storage.close()


def test_unmappable_historical_pending_notification_rolls_back_entire_baseline(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-unmappable.sqlite3"
    _build_historical_core_preledger(path, pending_edict_id="missing-edict")
    _assert_preledger_rejected_without_ledger(path)
    conn = _connect(path)
    assert conn.execute("SELECT rendered FROM pending_notifications").fetchone()[0] == "legacy"
    assert {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }.isdisjoint({"_events_v1", "_memorials_v1", "_pending_notifications_v1"})
    indexes = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall()
    }
    assert "idx_outer_loop_edict" in indexes
    assert indexes.isdisjoint(
        {
            "idx_events_edict_id",
            "idx_memorials_edict_id",
            "idx_memorials_failure_reason",
            "idx_memorials_universe_id",
        }
    )
    conn.close()


def test_near_miss_historical_core_shape_still_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "historical-near-miss.sqlite3"
    _build_historical_core_preledger(path)
    conn = _connect(path)
    conn.execute("ALTER TABLE pending_notifications ADD COLUMN unknown_payload TEXT")
    conn.commit()
    conn.close()
    _assert_preledger_rejected_without_ledger(path)


def test_reordered_historical_core_columns_still_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "historical-reordered.sqlite3"
    _build_historical_core_preledger(path)
    conn = _connect(path)
    conn.executescript(
        """
        ALTER TABLE pending_notifications RENAME TO pending_notifications_original;
        CREATE TABLE pending_notifications (
            id TEXT PRIMARY KEY,
            message_json TEXT NOT NULL,
            edict_id TEXT,
            rendered TEXT,
            channels_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO pending_notifications
            (id, edict_id, message_json, rendered, channels_json, created_at)
        SELECT id, edict_id, message_json, rendered, channels_json, created_at
        FROM pending_notifications_original;
        DROP TABLE pending_notifications_original;
        """
    )
    conn.close()

    _assert_preledger_rejected_without_ledger(path)


def test_wrong_historical_canonical_index_definition_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "historical-wrong-index.sqlite3"
    _build_historical_core_preledger(path)
    conn = _connect(path)
    conn.execute("CREATE INDEX idx_events_edict_id ON events(created_at)")
    conn.commit()
    conn.close()

    error = _assert_preledger_rejected_without_ledger(path)
    assert "unsupported pre-ledger schema" in str(error.__cause__)
    assert "index idx_events_edict_id structure drift" in str(error.__cause__)


def test_combined_historical_core_session_and_supervision_adapters_reach_canonical(
    tmp_path: Path,
) -> None:
    path = tmp_path / "historical-combined.sqlite3"
    _build_historical_core_preledger(path)
    _replace_supervision_with_legacy(path, composite_pk=False)
    _replace_session_with_legacy(path)

    storage = Storage(str(path))
    storage.init_db()
    indexes = {
        str(row[0])
        for row in storage._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        ).fetchall()
    }
    assert {"idx_feishu_seen_at", "idx_tg_seen_at"} <= indexes
    assert (
        storage._conn.execute(
            "SELECT memorial_id FROM pending_notifications WHERE id='pending-1'"
        ).fetchone()[0]
        == "memorial-1"
    )
    assert (
        storage._conn.execute(
            "SELECT memorial_id FROM supervision_reports WHERE edict_id='edict-legacy'"
        ).fetchone()[0]
        == "memorial-z"
    )
    assert [
        (row["version"], row["name"]) for row in _ledger_rows(storage._conn)
    ] == _COMPLETE_MIGRATION_LEDGER
    storage.close()


def test_wrong_legacy_session_canonical_index_is_rejected_during_initial_check(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-session-wrong-index.sqlite3"
    _build_canonical_preledger(path, prior_mcp_schema=True)
    _replace_session_with_legacy(path)
    conn = _connect(path)
    conn.execute("CREATE INDEX idx_feishu_seen_at ON feishu_seen_messages(message_id)")
    conn.commit()
    conn.close()

    error = _assert_preledger_rejected_without_ledger(path)
    assert "unsupported pre-ledger schema" in str(error.__cause__)
    assert "index idx_feishu_seen_at structure drift" in str(error.__cause__)


def test_wrong_session_only_canonical_index_is_rejected_during_initial_check(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-session-only-wrong-index.sqlite3"
    _replace_session_with_legacy(path)
    conn = _connect(path)
    conn.execute("CREATE INDEX idx_feishu_seen_at ON feishu_seen_messages(message_id)")
    conn.commit()
    conn.close()

    error = _assert_preledger_rejected_without_ledger(path)
    assert "unsupported pre-ledger schema" in str(error.__cause__)
    assert "index idx_feishu_seen_at structure drift" in str(error.__cause__)


def test_session_only_wrong_non_session_index_is_rejected_during_initial_check(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-session-only-wrong-non-session-index.sqlite3"
    _replace_session_with_legacy(path)
    conn = _connect(path)
    conn.execute("CREATE INDEX idx_memorials_edict_id ON feishu_seen_messages(message_id)")
    conn.commit()
    conn.close()

    error = _assert_preledger_rejected_without_ledger(path)
    assert "unsupported pre-ledger schema" in str(error.__cause__)
    assert "index idx_memorials_edict_id structure drift" in str(error.__cause__)


def _assert_preledger_rejected_without_ledger(path: Path) -> MigrationExecutionError:
    storage = Storage(str(path))
    with pytest.raises(MigrationExecutionError) as error:
        storage.init_db()

    conn = _connect(path)
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        is None
    )
    conn.close()
    return error.value


def test_preledger_missing_estop_check_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "missing-check.sqlite3"
    _build_canonical_preledger(path)
    conn = _connect(path)
    conn.executescript(
        """
        ALTER TABLE estop_state RENAME TO estop_state_old;
        CREATE TABLE estop_state (
            id INTEGER PRIMARY KEY,
            kill_all INTEGER NOT NULL DEFAULT 0,
            network_kill INTEGER NOT NULL DEFAULT 0,
            frozen_tools_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT,
            reason TEXT
        );
        DROP TABLE estop_state_old;
        """
    )
    conn.close()

    _assert_preledger_rejected_without_ledger(path)


def test_preledger_missing_outer_loop_unique_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "missing-unique.sqlite3"
    _build_canonical_preledger(path)
    conn = _connect(path)
    conn.executescript(
        """
        ALTER TABLE outer_loop_iterations RENAME TO outer_loop_iterations_old;
        DROP INDEX idx_outer_loop_archive;
        CREATE TABLE outer_loop_iterations (
            id TEXT PRIMARY KEY,
            edict_id TEXT NOT NULL,
            iteration INTEGER NOT NULL,
            level TEXT NOT NULL,
            actor_output TEXT,
            checks_result TEXT,
            critic_result TEXT,
            cost_cny REAL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            archived_at TEXT
        );
        DROP TABLE outer_loop_iterations_old;
        CREATE INDEX idx_outer_loop_archive
            ON outer_loop_iterations(finished_at) WHERE archived_at IS NULL;
        """
    )
    conn.close()

    _assert_preledger_rejected_without_ledger(path)


def test_preledger_trigger_on_owned_table_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "malicious-trigger.sqlite3"
    _build_canonical_preledger(path)
    conn = _connect(path)
    conn.execute(
        """
        CREATE TRIGGER malicious_edict_delete AFTER DELETE ON edicts BEGIN
            DELETE FROM memorials;
        END
        """
    )
    conn.commit()
    conn.close()

    _assert_preledger_rejected_without_ledger(path)


def test_preledger_tampered_optional_fts_trigger_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "tampered-fts-trigger.sqlite3"
    _build_canonical_preledger(path)
    conn = _connect(path)
    conn.executescript(
        """
        DROP TRIGGER memory_fts_insert;
        CREATE TRIGGER memory_fts_insert AFTER INSERT ON memory_entries BEGIN
            DELETE FROM memorials;
        END;
        """
    )
    conn.close()

    _assert_preledger_rejected_without_ledger(path)


@pytest.mark.parametrize("table_option", ["STRICT", "WITHOUT ROWID"])
def test_preledger_owned_table_option_drift_fails_closed(tmp_path: Path, table_option: str) -> None:
    path = tmp_path / f"table-option-{table_option.lower().replace(' ', '-')}.sqlite3"
    _build_canonical_preledger(path)
    conn = _connect(path)
    conn.executescript(
        f"""
        ALTER TABLE feature_flags RENAME TO feature_flags_old;
        CREATE TABLE feature_flags (
            key TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            rollout_pct INTEGER NOT NULL DEFAULT 100,
            description TEXT,
            updated_at TEXT NOT NULL
        ) {table_option};
        DROP TABLE feature_flags_old;
        """
    )
    conn.close()

    _assert_preledger_rejected_without_ledger(path)


def _replace_supervision_with_legacy(path: Path, *, composite_pk: bool) -> list[tuple[object, ...]]:
    conn = _connect(path)
    conn.executescript(
        """
        -- legacy supervision 形状的真实历史库早于 workspace foundation；
        -- IF EXISTS：起点可能是 canonical，也可能是已降级的 historical 库。
        DROP TABLE IF EXISTS system_audit_events;
        DROP TABLE IF EXISTS apply_receipts;
        DROP TABLE IF EXISTS apply_decision_states;
        DROP TABLE IF EXISTS apply_decisions;
        DROP TABLE IF EXISTS canonical_change_sets;
        DROP TABLE IF EXISTS restore_points;
        DROP TABLE IF EXISTS workspace_staging_identities;
        DROP TABLE IF EXISTS workspace_lease_states;
        DROP TABLE IF EXISTS workspace_leases;

        INSERT INTO edicts (id, title, goal, created_at)
        VALUES ('edict-legacy', 'legacy', 'legacy', '2026-07-11T01:00:00+00:00');
        INSERT INTO memorials (id, edict_id, status, usage_json, created_at)
        VALUES
            ('memorial-a', 'edict-legacy', 'completed', '{}',
             '2026-07-11T01:01:00+00:00'),
            ('memorial-z', 'edict-legacy', 'completed', '{}',
             '2026-07-11T01:01:00+00:00');
        DROP TABLE supervision_reports;
        """
    )
    primary_key = "PRIMARY KEY (edict_id, persona_id)" if composite_pk else ""
    edict_column = "edict_id TEXT NOT NULL" if composite_pk else "edict_id TEXT PRIMARY KEY"
    conn.execute(
        f"""
        CREATE TABLE supervision_reports (
            {edict_column},
            persona_id TEXT NOT NULL,
            persona_name TEXT NOT NULL,
            final_status TEXT NOT NULL,
            iterations_count INTEGER NOT NULL,
            total_cost_cny REAL NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
            {"," if primary_key else ""} {primary_key}
        )
        """
    )
    reports = [
        (
            "edict-legacy",
            "persona-legacy-1",
            "御史甲",
            "completed",
            3,
            2.5,
            '{"verdict":"first"}',
            "2026-07-11T01:02:00+00:00",
        )
    ]
    if composite_pk:
        reports.append(
            (
                "edict-legacy",
                "persona-legacy-2",
                "御史乙",
                "failed",
                4,
                3.5,
                '{"verdict":"second"}',
                "2026-07-11T01:03:00+00:00",
            )
        )
    conn.executemany(
        """
        INSERT INTO supervision_reports
            (edict_id, persona_id, persona_name, final_status, iterations_count,
             total_cost_cny, report_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        reports,
    )
    conn.commit()
    conn.close()
    return reports


@pytest.mark.parametrize("composite_pk", [False, True])
def test_legacy_supervision_rows_map_to_latest_memorial_without_loss(
    tmp_path: Path, composite_pk: bool
) -> None:
    path = tmp_path / "legacy-supervision.sqlite3"
    _build_canonical_preledger(path, prior_mcp_schema=True)
    legacy_rows = _replace_supervision_with_legacy(path, composite_pk=composite_pk)

    storage = Storage(str(path))
    storage.init_db()
    rows = storage._conn.execute(
        """
        SELECT edict_id, memorial_id, persona_id, persona_name, final_status,
               iterations_count, total_cost_cny, report_json, created_at
        FROM supervision_reports
        WHERE edict_id = 'edict-legacy'
        ORDER BY persona_id
        """
    ).fetchall()

    expected_rows = [(legacy[0], "memorial-z", *legacy[1:]) for legacy in legacy_rows]
    assert [tuple(row) for row in rows] == expected_rows
    ledger = [tuple(row) for row in _ledger_rows(storage._conn)]
    storage.close()

    reopened = Storage(str(path))
    reopened.init_db()
    reopened_rows = reopened._conn.execute(
        """
        SELECT edict_id, memorial_id, persona_id, persona_name, final_status,
               iterations_count, total_cost_cny, report_json, created_at
        FROM supervision_reports
        WHERE edict_id = 'edict-legacy'
        ORDER BY persona_id
        """
    ).fetchall()
    assert [tuple(row) for row in reopened_rows] == expected_rows
    assert [tuple(row) for row in _ledger_rows(reopened._conn)] == ledger
    reopened.close()


def test_wrong_legacy_supervision_canonical_index_definition_fails_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-supervision-wrong-index.sqlite3"
    _build_canonical_preledger(path, prior_mcp_schema=True)
    _replace_supervision_with_legacy(path, composite_pk=False)
    conn = _connect(path)
    conn.execute("CREATE INDEX idx_supervision_edict ON supervision_reports(persona_id)")
    conn.commit()
    conn.close()

    error = _assert_preledger_rejected_without_ledger(path)
    assert "unsupported pre-ledger schema" in str(error.__cause__)
    assert "index idx_supervision_edict structure drift" in str(error.__cause__)


def _assert_failed_baseline_preserved(path: Path) -> None:
    conn = _connect(path)
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        is None
    )
    assert conn.execute("SELECT COUNT(*) FROM supervision_reports").fetchone()[0] == 1
    for temp_table in ("_supervision_reports_new", "_supervision_reports_v1"):
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (temp_table,),
            ).fetchone()
            is None
        )
    conn.close()


def test_unmapped_legacy_supervision_row_rolls_back_entire_baseline(tmp_path: Path) -> None:
    path = tmp_path / "unmapped.sqlite3"
    _build_canonical_preledger(path, prior_mcp_schema=True)
    _replace_supervision_with_legacy(path, composite_pk=False)
    conn = _connect(path)
    conn.execute("UPDATE supervision_reports SET edict_id='missing-edict'")
    conn.commit()
    conn.close()

    storage = Storage(str(path))
    with pytest.raises(MigrationExecutionError):
        storage.init_db()

    _assert_failed_baseline_preserved(path)


def test_residual_supervision_temp_table_refuses_migration_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "residual.sqlite3"
    _build_canonical_preledger(path)
    conn = _connect(path)
    conn.execute("CREATE TABLE _supervision_reports_new (sentinel TEXT)")
    conn.commit()
    conn.close()

    storage = Storage(str(path))
    with pytest.raises(MigrationExecutionError):
        storage.init_db()

    conn = _connect(path)
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_supervision_reports_new'"
        ).fetchone()
        is not None
    )
    conn.close()


def test_unknown_preledger_schema_drift_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "drift.sqlite3"
    _build_canonical_preledger(path)
    conn = _connect(path)
    conn.executescript(
        """
        DROP TABLE cost_ledger;
        CREATE TABLE cost_ledger (id TEXT PRIMARY KEY);
        """
    )
    conn.close()

    storage = Storage(str(path))
    with pytest.raises(MigrationExecutionError):
        storage.init_db()

    conn = _connect(path)
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        is None
    )
    assert [row["name"] for row in conn.execute("PRAGMA table_info(cost_ledger)")] == ["id"]
    conn.close()


@pytest.fixture
def migration_connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = _connect(tmp_path / "atomic.sqlite3")
    yield conn
    conn.close()


def test_fresh_baseline_failure_rolls_back_all_schema_objects(
    migration_connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tianshu.storage import migrations

    statements = getattr(migrations, "SCHEMA_V1_STATEMENTS", ())
    monkeypatch.setattr(
        migrations,
        "SCHEMA_V1_STATEMENTS",
        (*statements, "CREATE TABLE doomed (id INTEGER PRIMARY KEY)", "INVALID SQL"),
        raising=False,
    )

    with pytest.raises(MigrationExecutionError):
        migrations.run_migrations(migration_connection)

    assert (
        migration_connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        == []
    )
