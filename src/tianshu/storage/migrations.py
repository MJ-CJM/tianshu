"""G0 schema baseline: canonical v0.4.2 adoption with explicit legacy adapters."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable

from tianshu.storage.migration_ledger import (
    Migration,
    MigrationConnection,
    apply_migrations,
)
from tianshu.storage.schema import SCHEMA_V1_CHECKSUM, SCHEMA_V1_STATEMENTS

_AUTH_TOKEN_STATEMENTS = (
    """
    CREATE TABLE auth_tokens (
        id TEXT PRIMARY KEY,
        prefix TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
        principal_id TEXT NOT NULL,
        principal_kind TEXT NOT NULL,
        display_name TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        scopes_json TEXT NOT NULL DEFAULT '[]',
        token_type TEXT NOT NULL CHECK (token_type IN ('pat', 'access', 'refresh')),
        family_id TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        revoked_at TEXT,
        replaced_by TEXT REFERENCES auth_tokens(id),
        last_used_at TEXT
    )
    """,
    "CREATE INDEX idx_auth_tokens_principal ON auth_tokens(principal_id)",
    "CREATE INDEX idx_auth_tokens_family ON auth_tokens(family_id)",
    "CREATE INDEX idx_auth_tokens_active ON auth_tokens(token_type, revoked_at, expires_at)",
)
_AUTH_TOKEN_CHECKSUM = hashlib.sha256(
    (
        "0002_auth_tokens\n" + "\n".join(" ".join(sql.split()) for sql in _AUTH_TOKEN_STATEMENTS)
    ).encode()
).hexdigest()

_GOVERNANCE_CONTRACT_STATEMENTS = (
    """
    CREATE TABLE requested_governance_contracts (
        edict_id TEXT PRIMARY KEY REFERENCES edicts(id) ON DELETE CASCADE,
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        contract_json TEXT NOT NULL,
        contract_hash TEXT NOT NULL CHECK (length(contract_hash) = 64),
        source TEXT NOT NULL CHECK (source IN ('explicit', 'legacy_derived')),
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_requested_governance_hash ON requested_governance_contracts(contract_hash)",
    """
    CREATE TABLE effective_governance_contracts (
        memorial_id TEXT PRIMARY KEY REFERENCES memorials(id) ON DELETE CASCADE,
        edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        requested_contract_hash TEXT NOT NULL CHECK (length(requested_contract_hash) = 64),
        contract_json TEXT NOT NULL,
        contract_hash TEXT NOT NULL CHECK (length(contract_hash) = 64),
        executor_manifest_id TEXT NOT NULL,
        executor_manifest_version TEXT NOT NULL,
        executor_manifest_hash TEXT NOT NULL CHECK (length(executor_manifest_hash) = 64),
        runtime_probe_id TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_effective_governance_edict ON effective_governance_contracts(edict_id)",
    "CREATE INDEX idx_effective_governance_hash ON effective_governance_contracts(contract_hash)",
)
_GOVERNANCE_CONTRACT_CHECKSUM = hashlib.sha256(
    (
        "0003_governance_contracts\n"
        + "\n".join(" ".join(sql.split()) for sql in _GOVERNANCE_CONTRACT_STATEMENTS)
    ).encode()
).hexdigest()

_WORKSPACE_FOUNDATION_STATEMENTS = (
    """
    CREATE TABLE workspace_leases (
        id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        run_id TEXT NOT NULL UNIQUE CHECK (length(run_id) BETWEEN 1 AND 256),
        lineage_root_run_id TEXT NOT NULL CHECK (length(lineage_root_run_id) BETWEEN 1 AND 256),
        parent_run_id TEXT,
        attempt INTEGER NOT NULL CHECK (attempt >= 0),
        source_kind TEXT NOT NULL CHECK (source_kind IN ('git', 'scratch')),
        apply_mode TEXT NOT NULL CHECK (apply_mode IN ('governed', 'none')),
        source_root TEXT,
        source_repository_id TEXT,
        source_git_dir TEXT,
        source_git_dir_identity TEXT CHECK (
            source_git_dir_identity IS NULL OR length(source_git_dir_identity) = 64
        ),
        base_revision TEXT,
        staging_root TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        UNIQUE (id, run_id),
        UNIQUE (lineage_root_run_id, attempt),
        FOREIGN KEY (parent_run_id) REFERENCES workspace_leases(run_id),
        CHECK (
            (attempt = 0 AND parent_run_id IS NULL AND lineage_root_run_id = run_id)
            OR (attempt > 0 AND parent_run_id IS NOT NULL)
        ),
        CHECK (
            (source_kind = 'git' AND apply_mode = 'governed'
             AND source_root IS NOT NULL AND source_repository_id IS NOT NULL
             AND source_git_dir IS NOT NULL AND source_git_dir_identity IS NOT NULL
             AND base_revision IS NOT NULL)
            OR
            (source_kind = 'scratch' AND apply_mode = 'none'
             AND source_root IS NULL AND source_repository_id IS NULL
             AND source_git_dir IS NULL AND source_git_dir_identity IS NULL
             AND base_revision IS NULL)
        )
    )
    """,
    "CREATE INDEX idx_workspace_leases_lineage ON workspace_leases(lineage_root_run_id, attempt)",
    """
    CREATE TRIGGER validate_workspace_lease_lineage
    BEFORE INSERT ON workspace_leases
    WHEN NEW.attempt > 0
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM workspace_leases AS parent
            WHERE parent.run_id = NEW.parent_run_id
              AND parent.lineage_root_run_id = NEW.lineage_root_run_id
              AND parent.attempt = NEW.attempt - 1
        ) THEN RAISE(ABORT, 'workspace lease lineage mismatch') END;
    END
    """,
    """
    CREATE TABLE workspace_lease_states (
        lease_id TEXT NOT NULL REFERENCES workspace_leases(id),
        version INTEGER NOT NULL CHECK (version >= 1),
        state TEXT NOT NULL CHECK (
            state IN ('starting', 'active', 'closing', 'cleanup_failed', 'closed')
        ),
        detail TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (lease_id, version)
    )
    """,
    """
    CREATE TABLE workspace_staging_identities (
        lease_id TEXT PRIMARY KEY REFERENCES workspace_leases(id),
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        staging_root TEXT NOT NULL UNIQUE,
        git_dir TEXT NOT NULL UNIQUE,
        git_dir_identity TEXT NOT NULL UNIQUE CHECK (length(git_dir_identity) = 64),
        source_repository_id TEXT NOT NULL,
        base_revision TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TRIGGER validate_workspace_staging_identity
    BEFORE INSERT ON workspace_staging_identities
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM workspace_leases AS lease
            WHERE lease.id = NEW.lease_id
              AND lease.source_kind = 'git'
              AND lease.staging_root = NEW.staging_root
              AND lease.source_repository_id = NEW.source_repository_id
              AND lease.base_revision = NEW.base_revision
        ) THEN RAISE(ABORT, 'workspace staging identity binding mismatch') END;
    END
    """,
    """
    CREATE TRIGGER validate_workspace_lease_state_insert
    BEFORE INSERT ON workspace_lease_states
    BEGIN
        SELECT CASE
            WHEN NEW.version = 1 AND NEW.state <> 'starting'
                THEN RAISE(ABORT, 'first workspace lease state must be starting')
            WHEN NEW.version = 1 AND EXISTS (
                SELECT 1 FROM workspace_lease_states WHERE lease_id = NEW.lease_id
            )
                THEN RAISE(ABORT, 'workspace lease state already initialized')
            WHEN NEW.version > 1 AND NOT EXISTS (
                SELECT 1 FROM workspace_lease_states
                WHERE lease_id = NEW.lease_id AND version = NEW.version - 1
            )
                THEN RAISE(ABORT, 'workspace lease state version is not contiguous')
            WHEN NEW.state IN ('active', 'closing') AND EXISTS (
                SELECT 1 FROM workspace_leases
                WHERE id = NEW.lease_id AND source_kind = 'git'
            ) AND NOT EXISTS (
                SELECT 1 FROM workspace_staging_identities WHERE lease_id = NEW.lease_id
            )
                THEN RAISE(ABORT, 'active or closing workspace lease requires staging identity')
            WHEN NEW.version > 1 AND NOT (
                ((SELECT state FROM workspace_lease_states
                  WHERE lease_id = NEW.lease_id AND version = NEW.version - 1) = 'starting'
                 AND NEW.state IN ('active', 'closing', 'cleanup_failed', 'closed'))
                OR
                ((SELECT state FROM workspace_lease_states
                  WHERE lease_id = NEW.lease_id AND version = NEW.version - 1) = 'active'
                 AND NEW.state = 'closing')
                OR
                ((SELECT state FROM workspace_lease_states
                  WHERE lease_id = NEW.lease_id AND version = NEW.version - 1) = 'closing'
                 AND NEW.state IN ('closed', 'cleanup_failed'))
                OR
                ((SELECT state FROM workspace_lease_states
                  WHERE lease_id = NEW.lease_id AND version = NEW.version - 1) = 'cleanup_failed'
                 AND NEW.state IN ('closing', 'closed'))
            )
                THEN RAISE(ABORT, 'invalid workspace lease state transition')
        END;
    END
    """,
    """
    CREATE TABLE restore_points (
        id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        lease_id TEXT NOT NULL UNIQUE REFERENCES workspace_leases(id),
        source_repository_id TEXT NOT NULL,
        source_root TEXT NOT NULL,
        source_git_dir TEXT NOT NULL,
        source_git_dir_identity TEXT NOT NULL CHECK (length(source_git_dir_identity) = 64),
        base_revision TEXT NOT NULL,
        source_head_revision TEXT NOT NULL,
        source_head_ref TEXT,
        source_index_tree TEXT NOT NULL,
        source_status_hash TEXT NOT NULL CHECK (length(source_status_hash) = 64),
        canonical_json TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
        created_at TEXT NOT NULL,
        UNIQUE (id, lease_id),
        UNIQUE (lease_id, content_hash)
    )
    """,
    "CREATE INDEX idx_restore_points_repository ON restore_points(source_repository_id, base_revision)",
    """
    CREATE TRIGGER validate_restore_point_binding
    BEFORE INSERT ON restore_points
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM workspace_leases AS lease
            WHERE lease.id = NEW.lease_id
              AND lease.source_kind = 'git'
              AND lease.source_repository_id = NEW.source_repository_id
              AND lease.source_root = NEW.source_root
              AND lease.source_git_dir = NEW.source_git_dir
              AND lease.source_git_dir_identity = NEW.source_git_dir_identity
              AND lease.base_revision = NEW.base_revision
        ) THEN RAISE(ABORT, 'restore point binding mismatch') END;
    END
    """,
    """
    CREATE TABLE canonical_change_sets (
        id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        lease_id TEXT NOT NULL REFERENCES workspace_leases(id),
        restore_point_id TEXT NOT NULL,
        source_repository_id TEXT NOT NULL,
        base_revision TEXT NOT NULL,
        sequence INTEGER NOT NULL CHECK (sequence >= 1),
        canonical_json TEXT NOT NULL,
        content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
        created_at TEXT NOT NULL,
        UNIQUE (id, lease_id),
        UNIQUE (lease_id, sequence),
        FOREIGN KEY (restore_point_id, lease_id)
            REFERENCES restore_points(id, lease_id)
    )
    """,
    "CREATE INDEX idx_change_sets_restore ON canonical_change_sets(restore_point_id, sequence)",
    """
    CREATE TRIGGER validate_canonical_change_set_binding
    BEFORE INSERT ON canonical_change_sets
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1
            FROM workspace_leases AS lease
            JOIN restore_points AS restore ON restore.lease_id = lease.id
            WHERE lease.id = NEW.lease_id
              AND restore.id = NEW.restore_point_id
              AND lease.source_repository_id = NEW.source_repository_id
              AND restore.source_repository_id = NEW.source_repository_id
              AND lease.base_revision = NEW.base_revision
              AND restore.base_revision = NEW.base_revision
        ) THEN RAISE(ABORT, 'canonical change set binding mismatch') END;
    END
    """,
    """
    CREATE TABLE apply_decisions (
        id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        lease_id TEXT NOT NULL,
        restore_point_id TEXT NOT NULL,
        change_set_id TEXT NOT NULL,
        change_set_hash TEXT NOT NULL CHECK (length(change_set_hash) = 64),
        source_repository_id TEXT NOT NULL,
        source_root TEXT NOT NULL,
        base_revision TEXT NOT NULL,
        source_head_ref TEXT,
        principal_digest TEXT NOT NULL CHECK (length(principal_digest) = 64),
        apply_scope TEXT NOT NULL CHECK (apply_scope = 'workspace'),
        reason TEXT NOT NULL CHECK (length(reason) > 0),
        decision_hash TEXT NOT NULL UNIQUE CHECK (length(decision_hash) = 64),
        token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (id, lease_id),
        FOREIGN KEY (restore_point_id, lease_id)
            REFERENCES restore_points(id, lease_id),
        FOREIGN KEY (change_set_id, lease_id)
            REFERENCES canonical_change_sets(id, lease_id)
    )
    """,
    "CREATE INDEX idx_apply_decisions_lease ON apply_decisions(lease_id, expires_at)",
    """
    CREATE TRIGGER validate_apply_decision_binding
    BEFORE INSERT ON apply_decisions
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1
            FROM canonical_change_sets AS c
            JOIN restore_points AS r
              ON r.id = c.restore_point_id AND r.lease_id = c.lease_id
            JOIN workspace_leases AS l ON l.id = c.lease_id
            WHERE c.id = NEW.change_set_id
              AND c.lease_id = NEW.lease_id
              AND c.content_hash = NEW.change_set_hash
              AND r.id = NEW.restore_point_id
              AND r.source_repository_id = NEW.source_repository_id
              AND r.source_root = NEW.source_root
              AND r.base_revision = NEW.base_revision
              AND r.source_head_ref IS NEW.source_head_ref
              AND l.source_repository_id = NEW.source_repository_id
              AND l.source_root = NEW.source_root
              AND l.base_revision = NEW.base_revision
              AND c.source_repository_id = NEW.source_repository_id
              AND c.base_revision = NEW.base_revision
        ) THEN RAISE(ABORT, 'apply decision binding mismatch') END;
    END
    """,
    """
    CREATE TABLE apply_decision_states (
        decision_id TEXT NOT NULL REFERENCES apply_decisions(id),
        version INTEGER NOT NULL CHECK (version >= 1),
        state TEXT NOT NULL CHECK (state IN ('pending', 'consumed', 'expired', 'revoked')),
        receipt_id TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (decision_id, version)
    )
    """,
    """
    CREATE TRIGGER validate_apply_decision_state_insert
    BEFORE INSERT ON apply_decision_states
    BEGIN
        SELECT CASE
            WHEN NEW.version = 1 AND NEW.state <> 'pending'
                THEN RAISE(ABORT, 'first apply decision state must be pending')
            WHEN NEW.version = 1 AND EXISTS (
                SELECT 1 FROM apply_decision_states WHERE decision_id = NEW.decision_id
            )
                THEN RAISE(ABORT, 'apply decision state already initialized')
            WHEN NEW.version > 1 AND NOT EXISTS (
                SELECT 1 FROM apply_decision_states
                WHERE decision_id = NEW.decision_id AND version = NEW.version - 1
                  AND state = 'pending'
            )
                THEN RAISE(ABORT, 'apply decision is already terminal')
            WHEN NEW.version > 1 AND NEW.state NOT IN ('consumed', 'expired', 'revoked')
                THEN RAISE(ABORT, 'invalid apply decision terminal state')
        END;
    END
    """,
    """
    CREATE TABLE apply_receipts (
        id TEXT PRIMARY KEY,
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        decision_id TEXT NOT NULL UNIQUE,
        decision_hash TEXT NOT NULL CHECK (length(decision_hash) = 64),
        lease_id TEXT NOT NULL,
        change_set_id TEXT NOT NULL,
        change_set_hash TEXT NOT NULL CHECK (length(change_set_hash) = 64),
        outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'denied')),
        detail TEXT NOT NULL,
        pre_source_head TEXT NOT NULL,
        pre_source_status_hash TEXT NOT NULL CHECK (length(pre_source_status_hash) = 64),
        post_source_head TEXT NOT NULL,
        post_source_status_hash TEXT NOT NULL CHECK (length(post_source_status_hash) = 64),
        rollback_status TEXT NOT NULL CHECK (
            rollback_status IN ('not_required', 'not_attempted', 'succeeded', 'failed')
        ),
        failure_code TEXT,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        FOREIGN KEY (decision_id, lease_id)
            REFERENCES apply_decisions(id, lease_id),
        FOREIGN KEY (change_set_id, lease_id)
            REFERENCES canonical_change_sets(id, lease_id)
    )
    """,
    "CREATE INDEX idx_apply_receipts_lease ON apply_receipts(lease_id, created_at)",
    """
    CREATE TRIGGER validate_apply_receipt_binding
    BEFORE INSERT ON apply_receipts
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1
            FROM apply_decisions AS d
            JOIN canonical_change_sets AS c
              ON c.id = d.change_set_id AND c.lease_id = d.lease_id
            WHERE d.id = NEW.decision_id
              AND d.lease_id = NEW.lease_id
              AND d.decision_hash = NEW.decision_hash
              AND c.id = NEW.change_set_id
              AND c.content_hash = NEW.change_set_hash
        ) THEN RAISE(ABORT, 'apply receipt binding mismatch') END;
    END
    """,
    *tuple(
        f"""
        CREATE TRIGGER immutable_{table}_update
        BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table} records are immutable'); END
        """
        for table in (
            "workspace_leases",
            "workspace_lease_states",
            "workspace_staging_identities",
            "restore_points",
            "canonical_change_sets",
            "apply_decisions",
            "apply_decision_states",
            "apply_receipts",
        )
    ),
    *tuple(
        f"""
        CREATE TRIGGER immutable_{table}_delete
        BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table} records are immutable'); END
        """
        for table in (
            "workspace_leases",
            "workspace_lease_states",
            "workspace_staging_identities",
            "restore_points",
            "canonical_change_sets",
            "apply_decisions",
            "apply_decision_states",
            "apply_receipts",
        )
    ),
)
_WORKSPACE_FOUNDATION_CHECKSUM = hashlib.sha256(
    (
        "0004_workspace_foundation\n"
        + "\n".join(" ".join(sql.split()) for sql in _WORKSPACE_FOUNDATION_STATEMENTS)
    ).encode()
).hexdigest()

type _Connection = sqlite3.Connection | MigrationConnection
type _ColumnSignature = tuple[str, str, int, str | None, int]
type _ForeignKeySignature = tuple[int, str, str, str, str, str, str]
type _IndexSignature = tuple[int, int, tuple[tuple[int, str | None, int, str, int], ...], str]
type _AutomaticIndexSignature = tuple[
    int, str, int, tuple[tuple[int, str | None, int, str, int], ...]
]
type _TableOptions = tuple[str, int, int]
type _SqlTokens = tuple[str, ...]
type _TableSignature = tuple[
    tuple[_ColumnSignature, ...],
    tuple[_ForeignKeySignature, ...],
    tuple[_AutomaticIndexSignature, ...],
    tuple[_SqlTokens, ...],
    _TableOptions,
]
type _TriggerSignature = tuple[str, _SqlTokens]

_SUPERVISION_TEMP_TABLES = {
    "_supervision_reports_new",
    "_supervision_reports_v1",
}
_SESSION_TEMP_TABLES = {
    "_feishu_session_anchor_v1",
    "_feishu_pending_cards_v1",
    "_feishu_seen_messages_v1",
    "_telegram_session_anchor_v1",
    "_telegram_pending_buttons_v1",
    "_telegram_seen_messages_v1",
    "feishu_session_anchor_new",
    "telegram_session_anchor_new",
}
_HISTORICAL_CORE_TEMP_TABLES = {
    "_events_v1",
    "_memorials_v1",
    "_pending_notifications_v1",
}
_RESERVED_TEMP_TABLES = (
    _SUPERVISION_TEMP_TABLES | _SESSION_TEMP_TABLES | _HISTORICAL_CORE_TEMP_TABLES
)

_SESSION_LEGACY_TABLES = {
    "feishu_session_anchor",
    "telegram_session_anchor",
    "feishu_pending_cards",
    "telegram_pending_buttons",
    "feishu_seen_messages",
    "telegram_seen_messages",
}
_SESSION_INDEXES = {"idx_feishu_seen_at", "idx_tg_seen_at"}
_SUPERVISION_INDEXES = {"idx_supervision_edict"}
_HISTORICAL_CORE_CANONICAL_INDEXES = {
    "events": {"idx_events_edict_id"},
    "memorials": {
        "idx_memorials_edict_id",
        "idx_memorials_failure_reason",
        "idx_memorials_universe_id",
    },
    "pending_notifications": set(),
}

_MEMORIAL_PAYLOAD_COLUMNS = (
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
)
_EVENT_PAYLOAD_COLUMNS = (
    "id",
    "edict_id",
    "memorial_id",
    "event_type",
    "payload_json",
    "created_at",
)

_SUPERVISION_PAYLOAD_COLUMNS = (
    "edict_id",
    "persona_id",
    "persona_name",
    "final_status",
    "iterations_count",
    "total_cost_cny",
    "report_json",
    "created_at",
)


class SchemaCompatibilityError(RuntimeError):
    """The pre-ledger database is outside the explicitly supported G0 boundary."""


def _normalize_type(value: object) -> str:
    return " ".join(str(value or "").upper().split())


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())


def _normalize_sql(value: object) -> str:
    statement = re.sub(r"--[^\n]*", " ", str(value or ""))
    return " ".join(statement.split()).replace("CREATE INDEX IF NOT EXISTS", "CREATE INDEX")


_SQL_TOKEN_PATTERN = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]"
    r"|!=|<>|<=|>=|==|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]"
)


def _sql_tokens(sql: str) -> _SqlTokens:
    return tuple(
        token.upper() if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token) else token
        for token in _SQL_TOKEN_PATTERN.findall(sql)
    )


def _masked_sql(sql: str) -> str:
    """Mask quoted text/comments while preserving positions and parentheses."""

    masked = list(sql)
    index = 0
    while index < len(sql):
        if sql.startswith("--", index):
            end = sql.find("\n", index + 2)
            end = len(sql) if end == -1 else end
        elif sql.startswith("/*", index):
            closing = sql.find("*/", index + 2)
            end = len(sql) if closing == -1 else closing + 2
        elif sql[index] in {"'", '"', "`"}:
            quote = sql[index]
            end = index + 1
            while end < len(sql):
                if sql[end] != quote:
                    end += 1
                    continue
                if end + 1 < len(sql) and sql[end + 1] == quote:
                    end += 2
                    continue
                end += 1
                break
        elif sql[index] == "[":
            closing = sql.find("]", index + 1)
            end = len(sql) if closing == -1 else closing + 1
        else:
            index += 1
            continue
        masked[index:end] = " " * (end - index)
        index = end
    return "".join(masked)


def _check_constraints(sql: str) -> tuple[_SqlTokens, ...]:
    masked = _masked_sql(sql)
    constraints: list[_SqlTokens] = []
    for match in re.finditer(r"\bCHECK\s*\(", masked, flags=re.IGNORECASE):
        opening = masked.find("(", match.start())
        depth = 1
        index = opening + 1
        while index < len(masked) and depth:
            if masked[index] == "(":
                depth += 1
            elif masked[index] == ")":
                depth -= 1
            index += 1
        if depth:
            raise SchemaCompatibilityError("unterminated CHECK constraint in sqlite schema")
        constraints.append(_sql_tokens(sql[opening + 1 : index - 1]))
    return tuple(sorted(constraints))


def _table_names(conn: _Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row[0]) for row in rows}


def _column_signature(conn: _Connection, table: str) -> tuple[_ColumnSignature, ...]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    columns = (
        (
            str(row[1]),
            _normalize_type(row[2]),
            int(row[3]),
            _normalize_default(row[4]),
            int(row[5]),
        )
        for row in rows
    )
    return tuple(sorted(columns, key=lambda column: column[0]))


def _ordered_column_signature(conn: _Connection, table: str) -> tuple[_ColumnSignature, ...]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return tuple(
        (
            str(row[1]),
            _normalize_type(row[2]),
            int(row[3]),
            _normalize_default(row[4]),
            int(row[5]),
        )
        for row in rows
    )


def _foreign_key_signature(conn: _Connection, table: str) -> tuple[_ForeignKeySignature, ...]:
    rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    foreign_keys = (
        (
            int(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
            str(row[6]),
            str(row[7]),
        )
        for row in rows
    )
    return tuple(sorted(foreign_keys))


def _index_columns(
    conn: _Connection, index: str
) -> tuple[tuple[int, str | None, int, str, int], ...]:
    return tuple(
        (
            int(item[0]),
            None if item[2] is None else str(item[2]),
            int(item[3]),
            str(item[4]),
            int(item[5]),
        )
        for item in conn.execute(f'PRAGMA index_xinfo("{index}")').fetchall()
    )


def _automatic_indexes(conn: _Connection, table: str) -> tuple[_AutomaticIndexSignature, ...]:
    signatures: list[_AutomaticIndexSignature] = []
    for row in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
        name = str(row[1])
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        if sql_row is None or sql_row[0] is not None:
            continue
        signatures.append(
            (
                int(row[2]),
                str(row[3]),
                int(row[4]),
                _index_columns(conn, name),
            )
        )
    return tuple(sorted(signatures, key=repr))


def _table_sql(conn: _Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if row is None or row[0] is None:
        raise SchemaCompatibilityError(f"table {table} has no canonical CREATE TABLE SQL")
    return str(row[0])


def _table_options(conn: _Connection, table: str) -> _TableOptions:
    row = next(
        (
            item
            for item in conn.execute("PRAGMA table_list").fetchall()
            if str(item[0]) == "main" and str(item[1]) == table
        ),
        None,
    )
    if row is None:
        raise SchemaCompatibilityError(f"table {table} is absent from PRAGMA table_list")
    return str(row[2]), int(row[4]), int(row[5])


def _table_signature(conn: _Connection, table: str) -> _TableSignature:
    table_sql = _table_sql(conn, table)
    return (
        _column_signature(conn, table),
        _foreign_key_signature(conn, table),
        _automatic_indexes(conn, table),
        _check_constraints(table_sql),
        _table_options(conn, table),
    )


def _ordered_table_signature(conn: _Connection, table: str) -> _TableSignature:
    table_sql = _table_sql(conn, table)
    return (
        _ordered_column_signature(conn, table),
        _foreign_key_signature(conn, table),
        _automatic_indexes(conn, table),
        _check_constraints(table_sql),
        _table_options(conn, table),
    )


def _named_indexes(conn: _Connection, tables: Iterable[str]) -> dict[str, _IndexSignature]:
    table_set = set(tables)
    indexes: dict[str, _IndexSignature] = {}
    rows = conn.execute(
        """
        SELECT name, tbl_name, sql
        FROM sqlite_master
        WHERE type='index' AND sql IS NOT NULL
        """
    ).fetchall()
    for row in rows:
        name = str(row[0])
        table = str(row[1])
        if table not in table_set:
            continue
        index_row = next(
            item
            for item in conn.execute(f'PRAGMA index_list("{table}")').fetchall()
            if str(item[1]) == name
        )
        columns = _index_columns(conn, name)
        indexes[name] = (
            int(index_row[2]),
            int(index_row[4]),
            columns,
            _normalize_sql(row[2]),
        )
    return indexes


def _canonical_signatures() -> tuple[dict[str, _TableSignature], dict[str, _IndexSignature]]:
    conn = sqlite3.connect(":memory:")
    try:
        for statement in SCHEMA_V1_STATEMENTS:
            conn.execute(statement)
        tables = _table_names(conn)
        return (
            {table: _table_signature(conn, table) for table in tables},
            _named_indexes(conn, tables),
        )
    finally:
        conn.close()


_EXPECTED_TABLES, _EXPECTED_INDEXES = _canonical_signatures()
_OWNED_TABLES = frozenset(_EXPECTED_TABLES)

_OPTIONAL_FTS_TRIGGER_DDLS = {
    "memory_fts_insert": """
        CREATE TRIGGER IF NOT EXISTS memory_fts_insert
        AFTER INSERT ON memory_entries BEGIN
            INSERT INTO memory_fts(rowid, id, persona_id, category, content)
            VALUES (new.rowid, new.id, new.persona_id, new.category, new.content);
        END
    """,
    "memory_fts_delete": """
        CREATE TRIGGER IF NOT EXISTS memory_fts_delete
        AFTER DELETE ON memory_entries BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, id, persona_id, category, content)
            VALUES ('delete', old.rowid, old.id, old.persona_id, old.category, old.content);
        END
    """,
    "memory_fts_update": """
        CREATE TRIGGER IF NOT EXISTS memory_fts_update
        AFTER UPDATE ON memory_entries BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, id, persona_id, category, content)
            VALUES ('delete', old.rowid, old.id, old.persona_id, old.category, old.content);
            INSERT INTO memory_fts(rowid, id, persona_id, category, content)
            VALUES (new.rowid, new.id, new.persona_id, new.category, new.content);
        END
    """,
}


def _expected_optional_triggers() -> dict[str, _TriggerSignature]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            """
            CREATE TABLE memory_entries (
                id TEXT,
                persona_id TEXT,
                category TEXT,
                content TEXT
            )
            """
        )
        for ddl in _OPTIONAL_FTS_TRIGGER_DDLS.values():
            conn.execute(ddl)
        rows = conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
        return {
            str(row[0]): (str(row[1]), _sql_tokens(str(row[2])))
            for row in rows
            if row[2] is not None
        }
    finally:
        conn.close()


_EXPECTED_OPTIONAL_TRIGGERS = _expected_optional_triggers()


def _signature_from_ddl(
    table: str,
    ddl: str,
    *,
    ordered_columns: bool = False,
) -> _TableSignature:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(ddl)
        signature = _ordered_table_signature if ordered_columns else _table_signature
        return signature(conn, table)
    finally:
        conn.close()


def _index_signature_from_ddl(
    table: str,
    index: str,
    table_ddl: str,
    index_ddl: str,
) -> _IndexSignature:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(table_ddl)
        conn.execute(index_ddl)
        return _named_indexes(conn, {table})[index]
    finally:
        conn.close()


_HISTORICAL_CORE_DDLS = {
    "pending_notifications": """
        CREATE TABLE pending_notifications (
            id TEXT PRIMARY KEY,
            edict_id TEXT,
            message_json TEXT NOT NULL,
            rendered TEXT,
            channels_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """,
    "memorials": """
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
        )
    """,
    "events": """
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            edict_id TEXT NOT NULL,
            memorial_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
    """,
}
_HISTORICAL_CORE_SIGNATURES = {
    table: _signature_from_ddl(table, ddl, ordered_columns=True)
    for table, ddl in _HISTORICAL_CORE_DDLS.items()
}
_HISTORICAL_CORE_INDEX_SIGNATURES = {
    "idx_outer_loop_edict": _index_signature_from_ddl(
        "outer_loop_iterations",
        "idx_outer_loop_edict",
        """
        CREATE TABLE outer_loop_iterations (
            edict_id TEXT NOT NULL,
            iteration INTEGER NOT NULL
        )
        """,
        """
        CREATE INDEX idx_outer_loop_edict
            ON outer_loop_iterations(edict_id, iteration)
        """,
    )
}


_SUPERVISION_LEGACY_SIGNATURES = {
    "edict": _signature_from_ddl(
        "supervision_reports",
        """
        CREATE TABLE supervision_reports (
            edict_id TEXT PRIMARY KEY,
            persona_id TEXT NOT NULL,
            persona_name TEXT NOT NULL,
            final_status TEXT NOT NULL,
            iterations_count INTEGER NOT NULL,
            total_cost_cny REAL NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
    ),
    "edict_persona": _signature_from_ddl(
        "supervision_reports",
        """
        CREATE TABLE supervision_reports (
            edict_id TEXT NOT NULL,
            persona_id TEXT NOT NULL,
            persona_name TEXT NOT NULL,
            final_status TEXT NOT NULL,
            iterations_count INTEGER NOT NULL,
            total_cost_cny REAL NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (edict_id, persona_id)
        )
        """,
    ),
}

_SESSION_LEGACY_DDLS = {
    "feishu_session_anchor": """
        CREATE TABLE feishu_session_anchor (
            chat_id TEXT PRIMARY KEY,
            current_edict_id TEXT,
            updated_at TIMESTAMP NOT NULL
        )
    """,
    "telegram_session_anchor": """
        CREATE TABLE telegram_session_anchor (
            chat_id TEXT PRIMARY KEY,
            current_edict_id TEXT,
            updated_at TIMESTAMP NOT NULL
        )
    """,
    "feishu_pending_cards": """
        CREATE TABLE feishu_pending_cards (
            approval_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """,
    "telegram_pending_buttons": """
        CREATE TABLE telegram_pending_buttons (
            approval_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL
        )
    """,
    "feishu_seen_messages": """
        CREATE TABLE feishu_seen_messages (
            message_id TEXT PRIMARY KEY,
            seen_at TIMESTAMP NOT NULL
        )
    """,
    "telegram_seen_messages": """
        CREATE TABLE telegram_seen_messages (
            update_id TEXT PRIMARY KEY,
            seen_at TIMESTAMP NOT NULL
        )
    """,
}
_SESSION_LEGACY_SIGNATURES = {
    table: _signature_from_ddl(table, ddl) for table, ddl in _SESSION_LEGACY_DDLS.items()
}


def _owned_triggers(conn: _Connection) -> dict[str, _TriggerSignature]:
    rows = conn.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    return {
        str(row[0]): (str(row[1]), _sql_tokens(str(row[2])))
        for row in rows
        if str(row[1]) in _OWNED_TABLES and row[2] is not None
    }


def _schema_differences(
    conn: _Connection,
    *,
    ignored_tables: frozenset[str] = frozenset(),
    ignored_indexes: frozenset[str] = frozenset(),
) -> list[str]:
    differences: list[str] = []
    actual_tables = _table_names(conn)
    for table, expected_table in _EXPECTED_TABLES.items():
        if table in ignored_tables:
            continue
        if table not in actual_tables:
            differences.append(f"missing table {table}")
        elif _table_signature(conn, table) != expected_table:
            differences.append(f"table {table} structure drift")

    actual_indexes = _named_indexes(conn, _OWNED_TABLES & actual_tables)
    expected_indexes = {
        name: signature
        for name, signature in _EXPECTED_INDEXES.items()
        if name not in ignored_indexes
    }
    comparable_actual = {
        name: signature for name, signature in actual_indexes.items() if name not in ignored_indexes
    }
    for name, expected_index in expected_indexes.items():
        if name not in comparable_actual:
            differences.append(f"missing index {name}")
        elif comparable_actual[name] != expected_index:
            differences.append(f"index {name} structure drift")
    for name in sorted(comparable_actual.keys() - expected_indexes.keys()):
        differences.append(f"unexpected index {name} on owned table")

    actual_triggers = _owned_triggers(conn)
    if actual_triggers and actual_triggers != _EXPECTED_OPTIONAL_TRIGGERS:
        differences.append("owned-table trigger set or definition drift")
    return differences


def _create_canonical_schema(conn: MigrationConnection) -> None:
    for statement in SCHEMA_V1_STATEMENTS:
        conn.execute(statement)


def _historical_core_tables(conn: _Connection) -> frozenset[str]:
    tables = _table_names(conn)
    return frozenset(
        table
        for table, signature in _HISTORICAL_CORE_SIGNATURES.items()
        if table in tables and _ordered_table_signature(conn, table) == signature
    )


def _historical_core_indexes(conn: _Connection) -> frozenset[str]:
    tables = _table_names(conn)
    indexes = _named_indexes(conn, _OWNED_TABLES & tables)
    return frozenset(
        index
        for index, signature in _HISTORICAL_CORE_INDEX_SIGNATURES.items()
        if indexes.get(index) == signature
    )


def _payload_rows(
    conn: _Connection,
    table: str,
    columns: tuple[str, ...],
) -> list[tuple[object, ...]]:
    return [
        tuple(row)
        for row in conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY id").fetchall()
    ]


def _copy_payload_rows(
    conn: MigrationConnection,
    table: str,
    columns: tuple[str, ...],
    rows: list[tuple[object, ...]],
) -> None:
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        rows,
    )


def _migrate_historical_memorials(conn: MigrationConnection) -> None:
    source_rows = _payload_rows(conn, "memorials", _MEMORIAL_PAYLOAD_COLUMNS)
    conn.execute(
        """
        CREATE TABLE _memorials_v1 (
            id TEXT PRIMARY KEY,
            edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
            instruction TEXT,
            status TEXT NOT NULL,
            summary TEXT,
            result TEXT,
            final_output TEXT,
            usage_json TEXT NOT NULL DEFAULT '{}',
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
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
            universe_id TEXT,
            feedback_score INTEGER NOT NULL DEFAULT 0,
            last_heartbeat_at TEXT,
            failure_reason TEXT
        )
        """
    )
    _copy_payload_rows(conn, "_memorials_v1", _MEMORIAL_PAYLOAD_COLUMNS, source_rows)
    if _payload_rows(conn, "_memorials_v1", _MEMORIAL_PAYLOAD_COLUMNS) != source_rows:
        raise SchemaCompatibilityError("historical memorial payload verification failed")

    conn.execute("DROP TABLE memorials")
    conn.execute("ALTER TABLE _memorials_v1 RENAME TO memorials")
    conn.execute("CREATE INDEX idx_memorials_edict_id ON memorials(edict_id)")
    conn.execute("CREATE INDEX idx_memorials_universe_id ON memorials(universe_id)")
    conn.execute(
        """
        CREATE INDEX idx_memorials_failure_reason
            ON memorials(failure_reason) WHERE failure_reason IS NOT NULL
        """
    )


def _migrate_historical_pending_notifications(conn: MigrationConnection) -> None:
    source_rows = _payload_rows(
        conn,
        "pending_notifications",
        ("id", "edict_id", "message_json", "rendered", "channels_json", "created_at"),
    )
    mapped_rows: list[tuple[object, ...]] = []
    for (
        notification_id,
        edict_id,
        message_json,
        _rendered,
        channels_json,
        created_at,
    ) in source_rows:
        memorial = conn.execute(
            """
            SELECT id
            FROM memorials
            WHERE edict_id = ? AND created_at <= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (edict_id, created_at),
        ).fetchone()
        if memorial is None:
            raise SchemaCompatibilityError(
                "historical pending notification has no memorial for deterministic mapping"
            )
        mapped_rows.append(
            (
                notification_id,
                edict_id,
                memorial[0],
                message_json,
                channels_json,
                created_at,
            )
        )

    columns = (
        "id",
        "edict_id",
        "memorial_id",
        "message_json",
        "channels_json",
        "created_at",
    )
    conn.execute(
        """
        CREATE TABLE _pending_notifications_v1 (
            id TEXT PRIMARY KEY,
            edict_id TEXT,
            memorial_id TEXT,
            message_json TEXT NOT NULL,
            channels_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    _copy_payload_rows(conn, "_pending_notifications_v1", columns, mapped_rows)
    if _payload_rows(conn, "_pending_notifications_v1", columns) != mapped_rows:
        raise SchemaCompatibilityError(
            "historical pending notification payload verification failed"
        )

    conn.execute("DROP TABLE pending_notifications")
    conn.execute("ALTER TABLE _pending_notifications_v1 RENAME TO pending_notifications")


def _migrate_historical_events(conn: MigrationConnection) -> None:
    source_rows = _payload_rows(conn, "events", _EVENT_PAYLOAD_COLUMNS)
    edict_ids = {row[0] for row in conn.execute("SELECT id FROM edicts ORDER BY id").fetchall()}
    valid_rows = [row for row in source_rows if row[1] in edict_ids]
    conn.execute(
        """
        CREATE TABLE _events_v1 (
            id TEXT PRIMARY KEY,
            edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
            memorial_id TEXT,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )
    _copy_payload_rows(conn, "_events_v1", _EVENT_PAYLOAD_COLUMNS, valid_rows)
    if _payload_rows(conn, "_events_v1", _EVENT_PAYLOAD_COLUMNS) != valid_rows:
        raise SchemaCompatibilityError("historical event payload verification failed")

    conn.execute("DROP TABLE events")
    conn.execute("ALTER TABLE _events_v1 RENAME TO events")
    conn.execute("CREATE INDEX idx_events_edict_id ON events(edict_id)")


def _migrate_historical_core_tables(conn: MigrationConnection) -> None:
    tables = _historical_core_tables(conn)
    indexes = _historical_core_indexes(conn)
    if "memorials" in tables:
        _migrate_historical_memorials(conn)
    if "pending_notifications" in tables:
        _migrate_historical_pending_notifications(conn)
    if "events" in tables:
        _migrate_historical_events(conn)
    for index in sorted(indexes):
        conn.execute(f"DROP INDEX {index}")


def _supervision_shape(conn: _Connection) -> str | None:
    actual = _table_signature(conn, "supervision_reports")
    for shape, expected in _SUPERVISION_LEGACY_SIGNATURES.items():
        if actual == expected:
            return shape
    return None


def _migrate_supervision(conn: MigrationConnection) -> None:
    source_rows = [
        tuple(row)
        for row in conn.execute(
            f"""
            SELECT {", ".join(f"r.{column}" for column in _SUPERVISION_PAYLOAD_COLUMNS)},
                   (
                       SELECT memorials.id
                       FROM memorials
                       WHERE memorials.edict_id = r.edict_id
                       ORDER BY memorials.created_at DESC, memorials.id DESC
                       LIMIT 1
                   ) AS memorial_id
            FROM supervision_reports AS r
            ORDER BY r.edict_id, r.persona_id
            """
        ).fetchall()
    ]
    if any(row[-1] is None for row in source_rows):
        raise SchemaCompatibilityError(
            "legacy supervision report has no memorial for deterministic mapping"
        )

    conn.execute(
        """
        CREATE TABLE _supervision_reports_v1 (
            edict_id TEXT NOT NULL,
            memorial_id TEXT NOT NULL,
            persona_id TEXT NOT NULL,
            persona_name TEXT NOT NULL,
            final_status TEXT NOT NULL,
            iterations_count INTEGER NOT NULL,
            total_cost_cny REAL NOT NULL,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (memorial_id, persona_id)
        )
        """
    )
    for row in source_rows:
        payload = row[:-1]
        memorial_id = row[-1]
        conn.execute(
            """
            INSERT INTO _supervision_reports_v1
                (edict_id, memorial_id, persona_id, persona_name, final_status,
                 iterations_count, total_cost_cny, report_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (payload[0], memorial_id, *payload[1:]),
        )

    copied_rows = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT edict_id, persona_id, persona_name, final_status, iterations_count,
                   total_cost_cny, report_json, created_at, memorial_id
            FROM _supervision_reports_v1
            ORDER BY edict_id, persona_id
            """
        ).fetchall()
    ]
    if copied_rows != source_rows:
        raise SchemaCompatibilityError("legacy supervision payload verification failed")

    conn.execute("DROP TABLE supervision_reports")
    conn.execute("ALTER TABLE _supervision_reports_v1 RENAME TO supervision_reports")
    conn.execute("CREATE INDEX idx_supervision_edict ON supervision_reports(edict_id)")


def _has_legacy_session_shape(conn: _Connection) -> bool:
    tables = _table_names(conn)
    if not tables >= _SESSION_LEGACY_TABLES:
        return False
    return all(
        _table_signature(conn, table) == signature
        for table, signature in _SESSION_LEGACY_SIGNATURES.items()
    )


def _migrate_session_tables(conn: MigrationConnection) -> None:
    for channel in ("feishu", "telegram"):
        table = f"{channel}_session_anchor"
        temp_table = f"_{table}_v1"
        default_instance = f"{channel}-default"
        source_rows = [
            tuple(row)
            for row in conn.execute(
                f"SELECT chat_id, current_edict_id, updated_at FROM {table} ORDER BY chat_id"
            ).fetchall()
        ]
        conn.execute(
            f"""
            CREATE TABLE {temp_table} (
                instance_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                current_edict_id TEXT,
                updated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (instance_id, chat_id)
            )
            """
        )
        for row in source_rows:
            conn.execute(
                f"""
                INSERT INTO {temp_table}
                    (instance_id, chat_id, current_edict_id, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (default_instance, *row),
            )
        copied = [
            tuple(row)
            for row in conn.execute(
                f"""
                SELECT chat_id, current_edict_id, updated_at
                FROM {temp_table}
                ORDER BY chat_id
                """
            ).fetchall()
        ]
        if copied != source_rows:
            raise SchemaCompatibilityError(f"{table} payload verification failed")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table}")

    for table, temp_table, default_instance, source_columns, create_sql in (
        (
            "feishu_pending_cards",
            "_feishu_pending_cards_v1",
            "feishu-default",
            ("approval_id", "chat_id", "message_id", "kind", "created_at"),
            """
            CREATE TABLE _feishu_pending_cards_v1 (
                approval_id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL DEFAULT 'feishu-default',
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """,
        ),
        (
            "telegram_pending_buttons",
            "_telegram_pending_buttons_v1",
            "telegram-default",
            ("approval_id", "chat_id", "message_id", "kind", "created_at"),
            """
            CREATE TABLE _telegram_pending_buttons_v1 (
                approval_id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL DEFAULT 'telegram-default',
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL
            )
            """,
        ),
        (
            "feishu_seen_messages",
            "_feishu_seen_messages_v1",
            "feishu-default",
            ("message_id", "seen_at"),
            """
            CREATE TABLE _feishu_seen_messages_v1 (
                message_id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL DEFAULT 'feishu-default',
                seen_at TIMESTAMP NOT NULL
            )
            """,
        ),
        (
            "telegram_seen_messages",
            "_telegram_seen_messages_v1",
            "telegram-default",
            ("update_id", "seen_at"),
            """
            CREATE TABLE _telegram_seen_messages_v1 (
                update_id TEXT PRIMARY KEY,
                instance_id TEXT NOT NULL DEFAULT 'telegram-default',
                seen_at TIMESTAMP NOT NULL
            )
            """,
        ),
    ):
        source_rows = [
            tuple(row)
            for row in conn.execute(
                f"SELECT {', '.join(source_columns)} FROM {table} ORDER BY {source_columns[0]}"
            ).fetchall()
        ]
        conn.execute(create_sql)
        target_columns = (source_columns[0], "instance_id", *source_columns[1:])
        placeholders = ", ".join("?" for _ in target_columns)
        conn.executemany(
            f"INSERT INTO {temp_table} ({', '.join(target_columns)}) VALUES ({placeholders})",
            ((row[0], default_instance, *row[1:]) for row in source_rows),
        )
        copied_rows = [
            tuple(row)
            for row in conn.execute(
                f"SELECT {', '.join(source_columns)} FROM {temp_table} ORDER BY {source_columns[0]}"
            ).fetchall()
        ]
        if copied_rows != source_rows:
            raise SchemaCompatibilityError(f"{table} payload verification failed")
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {temp_table} RENAME TO {table}")

    conn.execute("CREATE INDEX idx_feishu_seen_at ON feishu_seen_messages(seen_at)")
    conn.execute("CREATE INDEX idx_tg_seen_at ON telegram_seen_messages(seen_at)")


def _baseline_upgrade(conn: MigrationConnection) -> None:
    tables = _table_names(conn)
    residual = sorted(_RESERVED_TEMP_TABLES & tables)
    if residual:
        raise SchemaCompatibilityError(f"residual migration tables present: {residual}")

    present_owned = tables & _OWNED_TABLES
    if not present_owned:
        _create_canonical_schema(conn)
    else:
        legacy_session = _has_legacy_session_shape(conn)
        legacy_supervision = (
            "supervision_reports" in tables and _supervision_shape(conn) is not None
        )
        historical_core_tables = _historical_core_tables(conn)
        historical_core_indexes = _historical_core_indexes(conn)
        adapter_canonical_indexes = (
            (_SESSION_INDEXES if legacy_session else set())
            | (_SUPERVISION_INDEXES if legacy_supervision else set())
            | set().union(
                *(_HISTORICAL_CORE_CANONICAL_INDEXES[table] for table in historical_core_tables)
            )
        )
        actual_indexes = _named_indexes(conn, _OWNED_TABLES & tables)
        missing_adapter_indexes = adapter_canonical_indexes - actual_indexes.keys()
        session_only_fixture = legacy_session and present_owned == _SESSION_LEGACY_TABLES
        ignored_tables = (
            (_SESSION_LEGACY_TABLES if legacy_session else set())
            | ({"supervision_reports"} if legacy_supervision else set())
            | historical_core_tables
        )
        ignored_indexes = historical_core_indexes | missing_adapter_indexes
        if session_only_fixture:
            ignored_tables |= _OWNED_TABLES - present_owned
            ignored_indexes |= set(_EXPECTED_INDEXES) - _SESSION_INDEXES - actual_indexes.keys()
        differences = _schema_differences(
            conn,
            ignored_tables=frozenset(ignored_tables),
            ignored_indexes=frozenset(ignored_indexes),
        )
        if differences:
            raise SchemaCompatibilityError(
                "unsupported pre-ledger schema: " + "; ".join(differences)
            )
        if historical_core_tables or historical_core_indexes:
            _migrate_historical_core_tables(conn)
        if legacy_session:
            _migrate_session_tables(conn)
        if legacy_supervision:
            _migrate_supervision(conn)
        if session_only_fixture:
            _create_canonical_schema(conn)

    final_differences = _schema_differences(conn)
    if final_differences:
        raise SchemaCompatibilityError(
            "baseline did not produce canonical schema: " + "; ".join(final_differences)
        )


def _auth_tokens_upgrade(conn: MigrationConnection) -> None:
    if "auth_tokens" in _table_names(conn):
        reference = sqlite3.connect(":memory:")
        try:
            for statement in _AUTH_TOKEN_STATEMENTS:
                reference.execute(statement)
            expected_table = _table_signature(reference, "auth_tokens")
            expected_indexes = _named_indexes(reference, {"auth_tokens"})
        finally:
            reference.close()
        if _table_signature(conn, "auth_tokens") != expected_table:
            raise SchemaCompatibilityError("existing auth_tokens table is incompatible")
        if _named_indexes(conn, {"auth_tokens"}) != expected_indexes:
            raise SchemaCompatibilityError("existing auth_tokens indexes are incompatible")
        return
    for statement in _AUTH_TOKEN_STATEMENTS:
        conn.execute(statement)


def _validate_governance_contract_schema(conn: MigrationConnection) -> bool:
    table_names = _table_names(conn)
    expected_tables = {
        "requested_governance_contracts",
        "effective_governance_contracts",
    }
    present = table_names & expected_tables
    if not present:
        return False
    if present != expected_tables:
        raise SchemaCompatibilityError("partial governance contract schema is incompatible")

    reference = sqlite3.connect(":memory:")
    try:
        for statement in _GOVERNANCE_CONTRACT_STATEMENTS:
            reference.execute(statement)
        for table in expected_tables:
            if _table_signature(conn, table) != _table_signature(reference, table):
                raise SchemaCompatibilityError(f"existing {table} table is incompatible")
        if _named_indexes(conn, expected_tables) != _named_indexes(reference, expected_tables):
            raise SchemaCompatibilityError("existing governance contract indexes are incompatible")
    finally:
        reference.close()
    return True


def _backfill_requested_governance_contracts(conn: MigrationConnection) -> None:
    import json
    from types import SimpleNamespace

    from tianshu.models.acceptance import AcceptanceCriteria
    from tianshu.models.edict import EdictRuntime
    from tianshu.models.governance_contract import LegacyEdictGovernanceMapper

    rows = conn.execute(
        """
        SELECT id, goal, context, constraints_json, output_format, review_policy,
               runtime_json, acceptance_json, created_at
        FROM edicts
        WHERE id NOT IN (SELECT edict_id FROM requested_governance_contracts)
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        runtime = EdictRuntime.model_validate_json(row[6] or "{}")
        runtime_updates: dict[str, object] = {}
        if runtime.timeout_seconds <= 0:
            runtime_updates["timeout_seconds"] = 1
        if runtime.max_iterations <= 0:
            runtime_updates["max_iterations"] = 1
        if runtime.max_concurrency <= 0:
            runtime_updates["max_concurrency"] = 1
        if runtime.retry_limit < 0:
            runtime_updates["retry_limit"] = 0
        if runtime.token_budget is not None and runtime.token_budget <= 0:
            runtime_updates["token_budget"] = None
        if runtime.cost_budget_cny is not None and runtime.cost_budget_cny <= 0:
            runtime_updates["cost_budget_cny"] = None
        if runtime_updates:
            runtime = runtime.model_copy(update=runtime_updates)
        acceptance = AcceptanceCriteria.model_validate_json(row[7]) if row[7] else None
        legacy = SimpleNamespace(
            goal=str(row[1]),
            context=row[2],
            constraints=json.loads(row[3] or "[]"),
            output_format=row[4],
            review_policy=row[5] or "never",
            runtime=runtime,
            acceptance=acceptance,
        )
        contract = LegacyEdictGovernanceMapper.from_edict(
            legacy,
            default_workspace_id="legacy-default",
        )
        conn.execute(
            """
            INSERT INTO requested_governance_contracts
                (edict_id, schema_version, contract_json, contract_hash, source, created_at)
            VALUES (?, '1', ?, ?, 'legacy_derived', ?)
            """,
            (row[0], contract.canonical_json(), contract.content_hash, row[8]),
        )


def _governance_contracts_upgrade(conn: MigrationConnection) -> None:
    if not _validate_governance_contract_schema(conn):
        for statement in _GOVERNANCE_CONTRACT_STATEMENTS:
            conn.execute(statement)
    _backfill_requested_governance_contracts(conn)


def _workspace_trigger_signatures(
    conn: _Connection, tables: set[str]
) -> dict[str, _TriggerSignature]:
    rows = conn.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    return {
        str(row[0]): (str(row[1]), _sql_tokens(str(row[2])))
        for row in rows
        if str(row[1]) in tables and row[2] is not None
    }


def _validate_workspace_foundation_schema(conn: MigrationConnection) -> bool:
    expected_tables = {
        "workspace_leases",
        "workspace_lease_states",
        "workspace_staging_identities",
        "restore_points",
        "canonical_change_sets",
        "apply_decisions",
        "apply_decision_states",
        "apply_receipts",
    }
    present = _table_names(conn) & expected_tables
    if not present:
        return False
    if present != expected_tables:
        raise SchemaCompatibilityError("partial workspace foundation schema is incompatible")

    reference = sqlite3.connect(":memory:")
    reference.execute("PRAGMA foreign_keys=ON")
    try:
        for statement in _WORKSPACE_FOUNDATION_STATEMENTS:
            reference.execute(statement)
        for table in expected_tables:
            if _table_signature(conn, table) != _table_signature(reference, table):
                raise SchemaCompatibilityError(f"existing {table} table is incompatible")
        if _named_indexes(conn, expected_tables) != _named_indexes(reference, expected_tables):
            raise SchemaCompatibilityError("existing workspace foundation indexes are incompatible")
        if _workspace_trigger_signatures(conn, expected_tables) != _workspace_trigger_signatures(
            reference, expected_tables
        ):
            raise SchemaCompatibilityError(
                "existing workspace foundation triggers are incompatible"
            )
    finally:
        reference.close()
    return True


def _workspace_foundation_upgrade(conn: MigrationConnection) -> None:
    if not _validate_workspace_foundation_schema(conn):
        for statement in _WORKSPACE_FOUNDATION_STATEMENTS:
            conn.execute(statement)


MIGRATIONS = (
    Migration(
        version=1,
        name="0001_adopt_v042_baseline",
        checksum=SCHEMA_V1_CHECKSUM,
        upgrade=_baseline_upgrade,
    ),
    Migration(
        version=2,
        name="0002_auth_tokens",
        checksum=_AUTH_TOKEN_CHECKSUM,
        upgrade=_auth_tokens_upgrade,
    ),
    Migration(
        version=3,
        name="0003_governance_contracts",
        checksum=_GOVERNANCE_CONTRACT_CHECKSUM,
        upgrade=_governance_contracts_upgrade,
    ),
    Migration(
        version=4,
        name="0004_workspace_foundation",
        checksum=_WORKSPACE_FOUNDATION_CHECKSUM,
        upgrade=_workspace_foundation_upgrade,
    ),
)


def run_migrations(conn: sqlite3.Connection) -> tuple[int, ...]:
    """Apply the versioned schema sequence and return newly applied versions."""

    return apply_migrations(conn, MIGRATIONS)


__all__ = [
    "MIGRATIONS",
    "SCHEMA_V1_STATEMENTS",
    "SchemaCompatibilityError",
    "run_migrations",
]
