"""G0 schema baseline: canonical v0.4.2 adoption with explicit legacy adapters."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable
from typing import TYPE_CHECKING

from tianshu.storage.migration_ledger import (
    Migration,
    MigrationConnection,
    adopt_migrations,
    apply_migrations,
    ledger_exists,
)
from tianshu.storage.schema import SCHEMA_V1_CHECKSUM, SCHEMA_V1_STATEMENTS

if TYPE_CHECKING:
    from tianshu.secrets.vault import SecretVault

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

_GOVERNED_APPLY_BINDING_COLUMNS = (
    "run_id",
    "restore_point_hash",
    "source_git_dir_identity",
    "source_head_revision",
    "source_index_tree",
    "source_status_hash",
    "staging_root",
    "staging_git_dir_identity",
)
_GOVERNED_APPLY_BINDING_ALTERS = (
    "ALTER TABLE apply_decisions ADD COLUMN run_id TEXT",
    """
    ALTER TABLE apply_decisions ADD COLUMN restore_point_hash TEXT
        CHECK (restore_point_hash IS NULL OR length(restore_point_hash) = 64)
    """,
    """
    ALTER TABLE apply_decisions ADD COLUMN source_git_dir_identity TEXT
        CHECK (source_git_dir_identity IS NULL OR length(source_git_dir_identity) = 64)
    """,
    "ALTER TABLE apply_decisions ADD COLUMN source_head_revision TEXT",
    "ALTER TABLE apply_decisions ADD COLUMN source_index_tree TEXT",
    """
    ALTER TABLE apply_decisions ADD COLUMN source_status_hash TEXT
        CHECK (source_status_hash IS NULL OR length(source_status_hash) = 64)
    """,
    "ALTER TABLE apply_decisions ADD COLUMN staging_root TEXT",
    """
    ALTER TABLE apply_decisions ADD COLUMN staging_git_dir_identity TEXT
        CHECK (staging_git_dir_identity IS NULL OR length(staging_git_dir_identity) = 64)
    """,
)
_BACKFILL_GOVERNED_APPLY_BINDINGS = """
UPDATE apply_decisions
SET run_id = (
        SELECT l.run_id FROM workspace_leases AS l
        WHERE l.id = apply_decisions.lease_id
    ),
    restore_point_hash = (
        SELECT r.content_hash FROM restore_points AS r
        WHERE r.id = apply_decisions.restore_point_id
          AND r.lease_id = apply_decisions.lease_id
    ),
    source_git_dir_identity = (
        SELECT r.source_git_dir_identity FROM restore_points AS r
        WHERE r.id = apply_decisions.restore_point_id
          AND r.lease_id = apply_decisions.lease_id
    ),
    source_head_revision = (
        SELECT r.source_head_revision FROM restore_points AS r
        WHERE r.id = apply_decisions.restore_point_id
          AND r.lease_id = apply_decisions.lease_id
    ),
    source_index_tree = (
        SELECT r.source_index_tree FROM restore_points AS r
        WHERE r.id = apply_decisions.restore_point_id
          AND r.lease_id = apply_decisions.lease_id
    ),
    source_status_hash = (
        SELECT r.source_status_hash FROM restore_points AS r
        WHERE r.id = apply_decisions.restore_point_id
          AND r.lease_id = apply_decisions.lease_id
    ),
    staging_root = (
        SELECT l.staging_root FROM workspace_leases AS l
        WHERE l.id = apply_decisions.lease_id
    ),
    staging_git_dir_identity = (
        SELECT a.git_dir_identity FROM workspace_staging_identities AS a
        WHERE a.lease_id = apply_decisions.lease_id
    )
"""
_IMMUTABLE_APPLY_DECISIONS_UPDATE = """
CREATE TRIGGER immutable_apply_decisions_update
BEFORE UPDATE ON apply_decisions
BEGIN SELECT RAISE(ABORT, 'apply_decisions records are immutable'); END
"""
_VALIDATE_APPLY_DECISION_BINDING_V5 = """
CREATE TRIGGER validate_apply_decision_binding
BEFORE INSERT ON apply_decisions
BEGIN
    SELECT CASE WHEN EXISTS (
        SELECT 1 FROM apply_decisions AS existing
        WHERE existing.lease_id = NEW.lease_id
    ) THEN RAISE(ABORT, 'apply authority already issued for workspace lease') END;
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM canonical_change_sets AS c
        JOIN restore_points AS r
          ON r.id = c.restore_point_id AND r.lease_id = c.lease_id
        JOIN workspace_leases AS l ON l.id = c.lease_id
        JOIN workspace_staging_identities AS a ON a.lease_id = l.id
        WHERE c.id = NEW.change_set_id
          AND c.lease_id = NEW.lease_id
          AND c.content_hash = NEW.change_set_hash
          AND l.run_id = NEW.run_id
          AND r.id = NEW.restore_point_id
          AND r.content_hash = NEW.restore_point_hash
          AND r.source_repository_id = NEW.source_repository_id
          AND r.source_root = NEW.source_root
          AND r.source_git_dir_identity = NEW.source_git_dir_identity
          AND r.base_revision = NEW.base_revision
          AND r.source_head_revision = NEW.source_head_revision
          AND r.source_head_ref IS NEW.source_head_ref
          AND r.source_index_tree = NEW.source_index_tree
          AND r.source_status_hash = NEW.source_status_hash
          AND l.source_repository_id = NEW.source_repository_id
          AND l.source_root = NEW.source_root
          AND l.source_git_dir_identity = NEW.source_git_dir_identity
          AND l.base_revision = NEW.base_revision
          AND l.staging_root = NEW.staging_root
          AND a.staging_root = NEW.staging_root
          AND a.git_dir_identity = NEW.staging_git_dir_identity
          AND a.source_repository_id = NEW.source_repository_id
          AND a.base_revision = NEW.base_revision
          AND c.source_repository_id = NEW.source_repository_id
          AND c.base_revision = NEW.base_revision
    ) THEN RAISE(ABORT, 'apply decision binding mismatch') END;
END
"""
_VALIDATE_APPLY_DECISION_STATE_V5 = """
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
        WHEN NEW.version > 1 AND NEW.state = 'consumed' AND NEW.receipt_id IS NULL
            THEN RAISE(ABORT, 'consumed apply decision requires receipt identity')
        WHEN NEW.version > 1 AND NEW.state IN ('expired', 'revoked')
             AND NEW.receipt_id IS NOT NULL
            THEN RAISE(ABORT, 'non-consumed apply decision forbids receipt identity')
        WHEN NEW.version > 1 AND NEW.state = 'consumed' AND NOT EXISTS (
            SELECT 1
            FROM apply_decisions AS d
            JOIN workspace_lease_states AS lease_state
              ON lease_state.lease_id = d.lease_id
            WHERE d.id = NEW.decision_id
              AND NEW.created_at < d.expires_at
              AND lease_state.version = (
                  SELECT MAX(latest.version)
                  FROM workspace_lease_states AS latest
                  WHERE latest.lease_id = d.lease_id
              )
              AND lease_state.state = 'active'
        )
            THEN RAISE(ABORT, 'apply decision claim authority is not active')
    END;
END
"""
_VALIDATE_APPLY_RECEIPT_BINDING_V5 = """
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
          AND EXISTS (
              SELECT 1 FROM apply_decision_states AS s
              WHERE s.decision_id = d.id
                AND s.receipt_id = NEW.id
                AND s.state = 'consumed'
                AND s.version = (
                    SELECT MAX(latest.version)
                    FROM apply_decision_states AS latest
                    WHERE latest.decision_id = d.id
                )
          )
    ) THEN RAISE(ABORT, 'apply receipt binding mismatch') END;
END
"""
_GOVERNED_APPLY_BINDING_TRIGGER_STATEMENTS = (
    "DROP TRIGGER validate_apply_decision_binding",
    "DROP TRIGGER validate_apply_decision_state_insert",
    "DROP TRIGGER validate_apply_receipt_binding",
    _VALIDATE_APPLY_DECISION_BINDING_V5,
    _VALIDATE_APPLY_DECISION_STATE_V5,
    _VALIDATE_APPLY_RECEIPT_BINDING_V5,
)
_GOVERNED_APPLY_BINDING_CHECKSUM = hashlib.sha256(
    (
        "0005_governed_apply_bindings\n"
        + "\n".join(
            " ".join(sql.split())
            for sql in (
                "DROP TRIGGER immutable_apply_decisions_update",
                *_GOVERNED_APPLY_BINDING_ALTERS,
                _BACKFILL_GOVERNED_APPLY_BINDINGS,
                _IMMUTABLE_APPLY_DECISIONS_UPDATE,
                *_GOVERNED_APPLY_BINDING_TRIGGER_STATEMENTS,
            )
        )
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


def _governed_apply_bindings_upgrade(conn: MigrationConnection) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(apply_decisions)").fetchall()}
    present = columns & set(_GOVERNED_APPLY_BINDING_COLUMNS)
    if present and present != set(_GOVERNED_APPLY_BINDING_COLUMNS):
        raise SchemaCompatibilityError("partial governed apply binding schema is incompatible")
    if not present:
        conn.execute("DROP TRIGGER immutable_apply_decisions_update")
        for statement in _GOVERNED_APPLY_BINDING_ALTERS:
            conn.execute(statement)
        conn.execute(_BACKFILL_GOVERNED_APPLY_BINDINGS)
        missing = conn.execute(
            """
            SELECT COUNT(*) FROM apply_decisions
            WHERE run_id IS NULL
               OR restore_point_hash IS NULL
               OR source_git_dir_identity IS NULL
               OR source_head_revision IS NULL
               OR source_index_tree IS NULL
               OR source_status_hash IS NULL
               OR staging_root IS NULL
               OR staging_git_dir_identity IS NULL
            """
        ).fetchone()
        if missing is None or int(missing[0]) != 0:
            raise SchemaCompatibilityError(
                "legacy apply decisions cannot be bound to persisted workspace authority"
            )
        conn.execute(_IMMUTABLE_APPLY_DECISIONS_UPDATE)
    else:
        missing = conn.execute(
            """
            SELECT COUNT(*) FROM apply_decisions
            WHERE run_id IS NULL
               OR restore_point_hash IS NULL
               OR source_git_dir_identity IS NULL
               OR source_head_revision IS NULL
               OR source_index_tree IS NULL
               OR source_status_hash IS NULL
               OR staging_root IS NULL
               OR staging_git_dir_identity IS NULL
            """
        ).fetchone()
        if missing is None or int(missing[0]) != 0:
            raise SchemaCompatibilityError("governed apply bindings contain null authority")
    for statement in _GOVERNED_APPLY_BINDING_TRIGGER_STATEMENTS:
        conn.execute(statement)


# --- V6: seed six default persona departments (data-only, additive) ---

_DEFAULT_PERSONA_SEED_ROWS: tuple[tuple[str, str, str, str, int, int, str], ...] = (
    ("bingbu", "兵部", "bingbu", "Ministry of War", 2, 0, "[]"),
    ("ducha", "都察院", "ducha", "Censorate", 1, 0, "[]"),
    ("hubu", "户部", "hubu", "Ministry of Revenue", 1, 0, "[]"),
    ("neige", "内阁", "neige", "Imperial Cabinet", 1, 1, '["bingbu", "ducha", "wenyuan"]'),
    ("tongzheng", "通政司", "tongzheng", "Bureau of Coordination", 1, 0, "[]"),
    ("wenyuan", "文渊阁", "wenyuan", "Grand Secretariat", 1, 0, "[]"),
)

_SEED_DEFAULT_PERSONAS_SQL = """
INSERT INTO personas
    (id, name, department, title, tools_allowed, tools_denied, skills_allowed,
     tool_tier_max, can_delegate, memory_global_read, delegates_to,
     soul_path, role_path, llm_config_name, created_at, updated_at)
VALUES
    (?, ?, ?, ?, '[]', '[]', '[]', ?, ?, 0, ?, NULL, NULL, NULL,
     datetime('now'), datetime('now'))
"""

_SEED_DEFAULT_PERSONAS_CHECKSUM = hashlib.sha256(
    (
        "0006_seed_default_personas\n"
        + _SEED_DEFAULT_PERSONAS_SQL
        + repr(_DEFAULT_PERSONA_SEED_ROWS)
    ).encode("utf-8")
).hexdigest()


def _seed_default_personas_upgrade(conn: MigrationConnection) -> None:
    row = conn.execute("SELECT COUNT(*) FROM personas").fetchone()
    if row is not None and int(row[0]) > 0:
        # Existing user data: never overwrite, duplicate, or resurrect rows.
        return
    for pid, name, department, title, tier, delegate, delegates_to in _DEFAULT_PERSONA_SEED_ROWS:
        conn.execute(
            _SEED_DEFAULT_PERSONAS_SQL,
            (pid, name, department, title, tier, delegate, delegates_to),
        )


# --- V7: immutable system audit chain ---

_SYSTEM_AUDIT_STATEMENTS = (
    """
    CREATE TABLE system_audit_events (
        sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
        id TEXT NOT NULL UNIQUE,
        schema_version TEXT NOT NULL CHECK (schema_version = '1'),
        correlation_id TEXT NOT NULL,
        actor_digest TEXT NOT NULL CHECK (length(actor_digest) = 64),
        action TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN ('allowed', 'denied', 'succeeded', 'failed')),
        reason_code TEXT NOT NULL,
        subject_kind TEXT NOT NULL,
        subject_digest TEXT NOT NULL CHECK (length(subject_digest) = 64),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        previous_hash TEXT NOT NULL CHECK (length(previous_hash) = 64),
        event_hash TEXT NOT NULL UNIQUE CHECK (length(event_hash) = 64),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX idx_system_audit_correlation_sequence
    ON system_audit_events(correlation_id, sequence)
    """,
    """
    CREATE INDEX idx_system_audit_action_sequence
    ON system_audit_events(action, sequence)
    """,
    """
    CREATE TRIGGER system_audit_events_no_replace
    BEFORE INSERT ON system_audit_events
    WHEN EXISTS (
        SELECT 1 FROM system_audit_events
        WHERE sequence = NEW.sequence OR id = NEW.id OR event_hash = NEW.event_hash
    )
    BEGIN
        SELECT RAISE(ABORT, 'system audit events are append-only');
    END
    """,
    """
    CREATE TRIGGER system_audit_events_no_update
    BEFORE UPDATE ON system_audit_events
    BEGIN
        SELECT RAISE(ABORT, 'system audit events are append-only');
    END
    """,
    """
    CREATE TRIGGER system_audit_events_no_delete
    BEFORE DELETE ON system_audit_events
    BEGIN
        SELECT RAISE(ABORT, 'system audit events are append-only');
    END
    """,
)
_SYSTEM_AUDIT_CHECKSUM = hashlib.sha256(
    (
        "0007_system_audit_events\n"
        + "\n".join(" ".join(statement.split()) for statement in _SYSTEM_AUDIT_STATEMENTS)
    ).encode("utf-8")
).hexdigest()


def _system_audit_upgrade(conn: MigrationConnection) -> None:
    """Create system audit storage with insert, update, and delete immutability guards."""

    for statement in _SYSTEM_AUDIT_STATEMENTS:
        conn.execute(statement)


# --- V8: encrypt persisted MCP env/header mappings ---

_MCP_SECRET_MAPPING_TABLE_SQL = """
CREATE TABLE _mcp_server_overrides_v8 (
    name                 TEXT PRIMARY KEY,
    enabled              INTEGER,
    env_ciphertext       BLOB,
    env_keys_json        TEXT,
    tools_include_json   TEXT,
    tools_exclude_json   TEXT,
    transport            TEXT,
    command              TEXT,
    args_json            TEXT,
    url                  TEXT,
    headers_ciphertext   BLOB,
    header_keys_json     TEXT,
    default_tier         INTEGER,
    timeout              INTEGER,
    connect_timeout      INTEGER,
    tool_overrides_json  TEXT,
    updated_at           TEXT NOT NULL
)
"""
_MCP_SECRET_MAPPING_CHECKSUM = hashlib.sha256(
    ("0008_encrypt_mcp_secret_mappings\n" + " ".join(_MCP_SECRET_MAPPING_TABLE_SQL.split())).encode(
        "utf-8"
    )
).hexdigest()


def _parse_legacy_secret_mapping(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("MCP legacy secret mapping is invalid")
    try:
        mapping = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("MCP legacy secret mapping is invalid") from None
    if not isinstance(mapping, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in mapping.items()
    ):
        raise ValueError("MCP legacy secret mapping is invalid")
    return mapping


def _encrypt_verified_mapping(vault: SecretVault, mapping: dict[str, str]) -> tuple[bytes, str]:
    from tianshu.secrets.vault import decrypt_canonical_mapping, encrypt_canonical_mapping

    ciphertext = encrypt_canonical_mapping(vault, mapping)
    if decrypt_canonical_mapping(vault, ciphertext) != mapping:
        raise ValueError("MCP secret mapping verification failed")
    keys_json = json.dumps(sorted(mapping), separators=(",", ":"), ensure_ascii=False)
    return ciphertext, keys_json


def _mcp_secret_mapping_upgrade(conn: MigrationConnection) -> None:
    from tianshu.secrets.vault import require_mcp_vault

    legacy_rows = conn.execute(
        """
        SELECT name, enabled, env_json, tools_include_json, tools_exclude_json,
               transport, command, args_json, url, headers_json,
               default_tier, timeout, connect_timeout, tool_overrides_json,
               updated_at
        FROM mcp_server_overrides ORDER BY name
        """
    ).fetchall()
    parsed_rows: list[tuple[tuple[object, ...], dict[str, str] | None, dict[str, str] | None]] = []
    needs_vault = False
    for row in legacy_rows:
        values = tuple(row)
        env = _parse_legacy_secret_mapping(values[2])
        headers = _parse_legacy_secret_mapping(values[9])
        needs_vault = needs_vault or env is not None or headers is not None
        parsed_rows.append((values, env, headers))

    vault = require_mcp_vault() if needs_vault else None
    encrypted_rows: list[tuple[object, ...]] = []
    for values, env, headers in parsed_rows:
        env_ciphertext, env_keys_json = (
            _encrypt_verified_mapping(vault, env)
            if vault is not None and env is not None
            else (None, None)
        )
        headers_ciphertext, header_keys_json = (
            _encrypt_verified_mapping(vault, headers)
            if vault is not None and headers is not None
            else (None, None)
        )
        encrypted_rows.append(
            (
                values[0],
                values[1],
                env_ciphertext,
                env_keys_json,
                values[3],
                values[4],
                values[5],
                values[6],
                values[7],
                values[8],
                headers_ciphertext,
                header_keys_json,
                values[10],
                values[11],
                values[12],
                values[13],
                values[14],
            )
        )

    conn.execute("PRAGMA secure_delete=ON")
    conn.execute(_MCP_SECRET_MAPPING_TABLE_SQL)
    conn.executemany(
        """
        INSERT INTO _mcp_server_overrides_v8 (
            name, enabled, env_ciphertext, env_keys_json,
            tools_include_json, tools_exclude_json, transport, command,
            args_json, url, headers_ciphertext, header_keys_json,
            default_tier, timeout, connect_timeout, tool_overrides_json,
            updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        encrypted_rows,
    )
    conn.execute("DROP TABLE mcp_server_overrides")
    conn.execute("ALTER TABLE _mcp_server_overrides_v8 RENAME TO mcp_server_overrides")


# --- V9: atomic Edict submission and durable outbox foundation ---

_DURABLE_EDICT_INGRESS_STATEMENTS = (
    """
    CREATE TABLE outbox_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        edict_id TEXT REFERENCES edicts(id) ON DELETE CASCADE,
        memorial_id TEXT REFERENCES memorials(id) ON DELETE CASCADE,
        producer TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        available_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('pending','claimed','published','retry_wait','dead_letter')
        ),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        max_attempts INTEGER NOT NULL DEFAULT 20 CHECK (max_attempts > 0),
        lease_owner TEXT,
        lease_expires_at TEXT,
        last_error_json TEXT,
        published_at TEXT,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
    )
    """,
    """
    CREATE TABLE submission_idempotency (
        principal_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
        edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE RESTRICT,
        memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE RESTRICT,
        event_id TEXT NOT NULL REFERENCES outbox_events(event_id) ON DELETE RESTRICT,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (principal_id, idempotency_key),
        UNIQUE (event_id)
    )
    """,
    """
    CREATE TABLE outbox_consumptions (
        event_id TEXT NOT NULL REFERENCES outbox_events(event_id) ON DELETE CASCADE,
        consumer_name TEXT NOT NULL,
        result_hash TEXT,
        consumed_at TEXT NOT NULL,
        PRIMARY KEY (event_id, consumer_name)
    )
    """,
    """
    CREATE INDEX idx_outbox_claim
    ON outbox_events(status, available_at, lease_expires_at)
    """,
    """
    CREATE INDEX idx_outbox_edict
    ON outbox_events(edict_id, occurred_at)
    """,
)
_DURABLE_EDICT_INGRESS_CHECKSUM = hashlib.sha256(
    (
        "0009_durable_edict_ingress\n"
        + "\n".join(" ".join(statement.split()) for statement in _DURABLE_EDICT_INGRESS_STATEMENTS)
    ).encode("utf-8")
).hexdigest()


def _durable_edict_ingress_upgrade(conn: MigrationConnection) -> None:
    for statement in _DURABLE_EDICT_INGRESS_STATEMENTS:
        conn.execute(statement)


# --- V10: Telegram update identity is scoped by gateway instance ---

_TELEGRAM_SEEN_INSTANCE_IDENTITY_STATEMENTS = (
    """
    CREATE TABLE _telegram_seen_messages_v10 (
        instance_id TEXT NOT NULL DEFAULT 'telegram-default',
        update_id TEXT NOT NULL,
        seen_at TIMESTAMP NOT NULL,
        PRIMARY KEY (instance_id, update_id)
    )
    """,
    """
    INSERT INTO _telegram_seen_messages_v10 (instance_id, update_id, seen_at)
    SELECT instance_id, update_id, seen_at
    FROM telegram_seen_messages
    """,
    "DROP TABLE telegram_seen_messages",
    "ALTER TABLE _telegram_seen_messages_v10 RENAME TO telegram_seen_messages",
    "CREATE INDEX idx_tg_seen_at ON telegram_seen_messages(seen_at)",
)
_TELEGRAM_SEEN_INSTANCE_IDENTITY_CHECKSUM = hashlib.sha256(
    (
        "0010_telegram_seen_instance_identity\n"
        + "\n".join(
            " ".join(statement.split()) for statement in _TELEGRAM_SEEN_INSTANCE_IDENTITY_STATEMENTS
        )
    ).encode("utf-8")
).hexdigest()


def _telegram_seen_instance_identity_upgrade(conn: MigrationConnection) -> None:
    source_rows = conn.execute(
        """
        SELECT instance_id, update_id, seen_at
        FROM telegram_seen_messages
        ORDER BY instance_id, update_id
        """
    ).fetchall()
    conn.execute(_TELEGRAM_SEEN_INSTANCE_IDENTITY_STATEMENTS[0])
    conn.execute(_TELEGRAM_SEEN_INSTANCE_IDENTITY_STATEMENTS[1])
    copied_rows = conn.execute(
        """
        SELECT instance_id, update_id, seen_at
        FROM _telegram_seen_messages_v10
        ORDER BY instance_id, update_id
        """
    ).fetchall()
    if copied_rows != source_rows:
        raise SchemaCompatibilityError("telegram_seen_messages payload verification failed")
    for statement in _TELEGRAM_SEEN_INSTANCE_IDENTITY_STATEMENTS[2:]:
        conn.execute(statement)


# --- V11: durable governance decisions and resumable run state ---

_DECISIONS_RUN_STATE_STATEMENTS = (
    """
    CREATE TABLE decision_requests (
        decision_request_id TEXT PRIMARY KEY,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        kind TEXT NOT NULL CHECK (kind IN ('tool','outer_loop','plan_review','governed_apply')),
        edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
        memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE CASCADE,
        request_key TEXT NOT NULL CHECK (length(trim(request_key)) > 0),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        payload_hash TEXT NOT NULL CHECK (
            length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'
        ),
        requested_by TEXT NOT NULL CHECK (length(trim(requested_by)) > 0),
        expires_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending','resolved','expired','cancelled')),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (memorial_id, kind, request_key)
    )
    """,
    """
    CREATE TABLE decision_resolutions (
        decision_request_id TEXT PRIMARY KEY
            REFERENCES decision_requests(decision_request_id) ON DELETE RESTRICT,
        action TEXT NOT NULL CHECK (length(trim(action)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        actor_principal_id TEXT NOT NULL CHECK (length(trim(actor_principal_id)) > 0),
        actor_display_name TEXT NOT NULL CHECK (length(trim(actor_display_name)) > 0),
        resolved_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE run_states (
        memorial_id TEXT PRIMARY KEY REFERENCES memorials(id) ON DELETE CASCADE,
        edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        phase TEXT NOT NULL CHECK (phase IN (
            'submitted','planning','executing','waiting_decision',
            'paused','auditing','completed','failed'
        )),
        continuation_kind TEXT NOT NULL CHECK (continuation_kind IN ('agent','outer_loop')),
        continuation_json TEXT NOT NULL CHECK (
            json_valid(continuation_json)
            AND json_extract(continuation_json, '$.kind') = continuation_kind
        ),
        checkpoint_ref TEXT,
        side_effect_cursor INTEGER NOT NULL DEFAULT 0 CHECK (side_effect_cursor >= 0),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX idx_decisions_pending
    ON decision_requests(status, kind, expires_at)
    """,
    """
    CREATE INDEX idx_decisions_memorial
    ON decision_requests(memorial_id, created_at)
    """,
    """
    CREATE INDEX idx_run_states_edict
    ON run_states(edict_id, updated_at)
    """,
    """
    CREATE TRIGGER decision_resolutions_no_update
    BEFORE UPDATE ON decision_resolutions
    BEGIN
        SELECT RAISE(ABORT, 'decision resolutions are immutable');
    END
    """,
    """
    CREATE TRIGGER decision_resolutions_no_delete
    BEFORE DELETE ON decision_resolutions
    BEGIN
        SELECT RAISE(ABORT, 'decision resolutions are immutable');
    END
    """,
)
_DECISIONS_RUN_STATE_CHECKSUM = hashlib.sha256(
    (
        "0011_decisions_run_state\n"
        + "\n".join(" ".join(statement.split()) for statement in _DECISIONS_RUN_STATE_STATEMENTS)
    ).encode("utf-8")
).hexdigest()


def _decisions_run_state_upgrade(conn: MigrationConnection) -> None:
    for statement in _DECISIONS_RUN_STATE_STATEMENTS:
        conn.execute(statement)


# --- V12: additive validation and no-replace guards for v11 durable rows ---

_RUN_STATE_CONTINUATION_SHAPE = """
CASE
    WHEN json_valid({json}) THEN
        CASE WHEN json_type({json}) = 'object' THEN
            COALESCE(CASE {kind}
            WHEN 'agent' THEN
                json_extract({json}, '$.kind') = 'agent'
                AND COALESCE(json_type({json}, '$.messages'), 'missing') = 'array'
                AND COALESCE(json_type({json}, '$.pending_tool'), 'missing')
                    IN ('null', 'object')
                AND COALESCE(json_type({json}, '$.iteration'), 'missing') = 'integer'
                AND COALESCE(json_type({json}, '$.usage'), 'missing') = 'object'
                AND COALESCE(json_type({json}, '$.checkpoint_ref'), 'missing')
                    IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.resolved_decision_id'), 'missing')
                    IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.side_effect_cursor'), 'missing') = 'integer'
            WHEN 'outer_loop' THEN
                json_extract({json}, '$.kind') = 'outer_loop'
                AND json_extract({json}, '$.level') IN ('L0', 'L1', 'L2', 'L3')
                AND COALESCE(json_type({json}, '$.iteration'), 'missing') = 'integer'
                AND COALESCE(json_type({json}, '$.best_output'), 'missing')
                    IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.feedback'), 'missing')
                    IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.steer'), 'missing') IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.history'), 'missing') = 'array'
                AND COALESCE(json_type({json}, '$.same_issue_streak'), 'missing') = 'integer'
                AND COALESCE(json_type({json}, '$.last_critic_issue_class'), 'missing')
                    IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.l1_rounds_used'), 'missing') = 'integer'
                AND COALESCE(json_type({json}, '$.l2_rounds_used'), 'missing') = 'integer'
                AND COALESCE(json_type({json}, '$.consultation_advice'), 'missing')
                    IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.usage'), 'missing') = 'object'
                AND COALESCE(json_type({json}, '$.total_cost_cny'), 'missing')
                    IN ('integer', 'real', 'text')
                AND COALESCE(json_type({json}, '$.checkpoint_ref'), 'missing')
                    IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.resolved_decision_id'), 'missing')
                    IN ('null', 'text')
                AND COALESCE(json_type({json}, '$.side_effect_cursor'), 'missing') = 'integer'
            ELSE 0
            END, 0)
            AND json_extract({json}, '$.checkpoint_ref') IS {checkpoint}
            AND COALESCE(
                json_extract({json}, '$.side_effect_cursor') = {cursor},
                0
            )
        ELSE 0 END
    ELSE 0
END
"""

_DECISION_RUN_STATE_GUARD_VALIDATIONS = (
    (
        "decision request payload",
        """
        SELECT decision_request_id
        FROM decision_requests
        WHERE CASE
            WHEN json_valid(payload_json) THEN json_type(payload_json) != 'object'
            ELSE 1
        END
        LIMIT 1
        """,
    ),
    (
        "decision resolution payload",
        """
        SELECT decision_request_id
        FROM decision_resolutions
        WHERE CASE
            WHEN json_valid(payload_json) THEN json_type(payload_json) != 'object'
            ELSE 1
        END
        LIMIT 1
        """,
    ),
    (
        "run state continuation",
        """
        SELECT memorial_id
        FROM run_states
        WHERE NOT (
        """
        + _RUN_STATE_CONTINUATION_SHAPE.format(
            json="continuation_json",
            kind="continuation_kind",
            checkpoint="checkpoint_ref",
            cursor="side_effect_cursor",
        )
        + """
        )
        LIMIT 1
        """,
    ),
)

_DECISION_RUN_STATE_GUARD_STATEMENTS = (
    """
    CREATE TRIGGER decision_requests_validate_payload_insert_v12
    BEFORE INSERT ON decision_requests
    WHEN NOT (
        CASE
            WHEN json_valid(NEW.payload_json) THEN json_type(NEW.payload_json) = 'object'
            ELSE 0
        END
    )
    BEGIN
        SELECT RAISE(ABORT, 'decision request payload must be a JSON object');
    END
    """,
    """
    CREATE TRIGGER decision_requests_validate_payload_update_v12
    BEFORE UPDATE OF payload_json ON decision_requests
    WHEN NOT (
        CASE
            WHEN json_valid(NEW.payload_json) THEN json_type(NEW.payload_json) = 'object'
            ELSE 0
        END
    )
    BEGIN
        SELECT RAISE(ABORT, 'decision request payload must be a JSON object');
    END
    """,
    """
    CREATE TRIGGER decision_resolutions_validate_payload_insert_v12
    BEFORE INSERT ON decision_resolutions
    WHEN NOT (
        CASE
            WHEN json_valid(NEW.payload_json) THEN json_type(NEW.payload_json) = 'object'
            ELSE 0
        END
    )
    BEGIN
        SELECT RAISE(ABORT, 'decision resolution payload must be a JSON object');
    END
    """,
    """
    CREATE TRIGGER run_states_validate_insert_v12
    BEFORE INSERT ON run_states
    WHEN NOT (
    """
    + _RUN_STATE_CONTINUATION_SHAPE.format(
        json="NEW.continuation_json",
        kind="NEW.continuation_kind",
        checkpoint="NEW.checkpoint_ref",
        cursor="NEW.side_effect_cursor",
    )
    + """
    )
    BEGIN
        SELECT RAISE(ABORT, 'run state continuation has invalid top-level shape');
    END
    """,
    """
    CREATE TRIGGER run_states_validate_update_v12
    BEFORE UPDATE ON run_states
    WHEN NOT (
    """
    + _RUN_STATE_CONTINUATION_SHAPE.format(
        json="NEW.continuation_json",
        kind="NEW.continuation_kind",
        checkpoint="NEW.checkpoint_ref",
        cursor="NEW.side_effect_cursor",
    )
    + """
    )
    BEGIN
        SELECT RAISE(ABORT, 'run state continuation has invalid top-level shape');
    END
    """,
    """
    CREATE TRIGGER decision_requests_no_replace
    BEFORE INSERT ON decision_requests
    WHEN EXISTS (
        SELECT 1 FROM decision_requests
        WHERE decision_request_id = NEW.decision_request_id
           OR (
               memorial_id = NEW.memorial_id
               AND kind = NEW.kind
               AND request_key = NEW.request_key
           )
    )
    BEGIN
        SELECT RAISE(ABORT, 'decision requests are immutable by replacement');
    END
    """,
    """
    CREATE TRIGGER decision_resolutions_no_replace
    BEFORE INSERT ON decision_resolutions
    WHEN EXISTS (
        SELECT 1 FROM decision_resolutions
        WHERE decision_request_id = NEW.decision_request_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'decision resolutions are immutable by replacement');
    END
    """,
    """
    CREATE TRIGGER run_states_no_replace
    BEFORE INSERT ON run_states
    WHEN EXISTS (
        SELECT 1 FROM run_states
        WHERE memorial_id = NEW.memorial_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'run states are immutable by replacement');
    END
    """,
)
_DECISION_RUN_STATE_GUARD_CHECKSUM = hashlib.sha256(
    (
        "0012_decision_run_state_guards\n"
        + "\n".join(
            " ".join(statement.split()) for _, statement in _DECISION_RUN_STATE_GUARD_VALIDATIONS
        )
        + "\n"
        + "\n".join(
            " ".join(statement.split()) for statement in _DECISION_RUN_STATE_GUARD_STATEMENTS
        )
    ).encode("utf-8")
).hexdigest()


def _decision_run_state_guards_upgrade(conn: MigrationConnection) -> None:
    for label, query in _DECISION_RUN_STATE_GUARD_VALIDATIONS:
        if conn.execute(query).fetchone() is not None:
            raise SchemaCompatibilityError(f"invalid existing {label}")
    for statement in _DECISION_RUN_STATE_GUARD_STATEMENTS:
        conn.execute(statement)


# --- V13: bind governed apply projections to generic decisions ---

_GOVERNED_APPLY_DECISION_BINDING_STATEMENTS = (
    """
    ALTER TABLE apply_decisions
    ADD COLUMN decision_request_id TEXT
        REFERENCES decision_requests(decision_request_id)
    """,
    """
    CREATE UNIQUE INDEX idx_apply_decisions_decision_request
    ON apply_decisions(decision_request_id)
    WHERE decision_request_id IS NOT NULL
    """,
    """
    CREATE TRIGGER validate_governed_apply_decision_projection
    BEFORE INSERT ON apply_decisions
    WHEN NEW.decision_request_id IS NOT NULL
    BEGIN
        SELECT CASE WHEN NEW.id <> NEW.decision_request_id
            THEN RAISE(ABORT, 'governed apply projection identity mismatch')
        END;
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1
            FROM decision_requests AS request
            JOIN decision_resolutions AS resolution
              ON resolution.decision_request_id = request.decision_request_id
            WHERE request.decision_request_id = NEW.decision_request_id
              AND request.kind = 'governed_apply'
              AND request.status = 'resolved'
              AND resolution.action = 'approve'
        ) THEN RAISE(
            ABORT,
            'governed apply projection requires resolved approve'
        ) END;
    END
    """,
)
_GOVERNED_APPLY_DECISION_BINDING_CHECKSUM = hashlib.sha256(
    (
        "0013_governed_apply_decision_binding\n"
        + "\n".join(
            " ".join(statement.split()) for statement in _GOVERNED_APPLY_DECISION_BINDING_STATEMENTS
        )
    ).encode("utf-8")
).hexdigest()


def _governed_apply_decision_binding_upgrade(conn: MigrationConnection) -> None:
    for statement in _GOVERNED_APPLY_DECISION_BINDING_STATEMENTS:
        conn.execute(statement)


# --- V14: fenced durable execution-attempt ledger ---

_EXECUTION_ATTEMPT_LEDGER_STATEMENTS = (
    """
    CREATE TABLE execution_attempts (
        attempt_id TEXT PRIMARY KEY CHECK (length(trim(attempt_id)) > 0),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE CASCADE,
        attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
        status TEXT NOT NULL CHECK (status IN (
            'claimable','claimed','suspended','succeeded','failed','dead_letter'
        )),
        owner_id TEXT,
        fencing_token INTEGER NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
        lease_expires_at TEXT,
        heartbeat_at TEXT,
        available_at TEXT NOT NULL CHECK (length(trim(available_at)) > 0),
        max_attempts INTEGER NOT NULL CHECK (
            max_attempts > 0 AND attempt_no <= max_attempts
        ),
        failure_json TEXT CHECK (
            failure_json IS NULL OR (
                json_valid(failure_json) AND json_type(failure_json) = 'object'
            )
        ),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
        UNIQUE (memorial_id, attempt_no),
        CHECK (
            (
                status = 'claimed'
                AND owner_id IS NOT NULL AND length(trim(owner_id)) > 0
                AND lease_expires_at IS NOT NULL
                AND heartbeat_at IS NOT NULL
                AND lease_expires_at > heartbeat_at
                AND fencing_token > 0
            ) OR (
                status <> 'claimed'
                AND owner_id IS NULL
                AND lease_expires_at IS NULL
            )
        ),
        CHECK (
            (status IN ('failed','dead_letter') AND failure_json IS NOT NULL)
            OR
            (status NOT IN ('failed','dead_letter') AND failure_json IS NULL)
        )
    )
    """,
    """
    CREATE UNIQUE INDEX idx_execution_attempts_active_memorial
    ON execution_attempts(memorial_id)
    WHERE status IN ('claimable','claimed','suspended')
    """,
    """
    CREATE INDEX idx_execution_attempts_claim
    ON execution_attempts(status, available_at, lease_expires_at, created_at, attempt_id)
    """,
    """
    CREATE INDEX idx_execution_attempts_memorial
    ON execution_attempts(memorial_id, attempt_no)
    """,
    """
    CREATE TRIGGER execution_attempts_no_replace
    BEFORE INSERT ON execution_attempts
    WHEN EXISTS (
        SELECT 1 FROM execution_attempts
        WHERE attempt_id = NEW.attempt_id
           OR (memorial_id = NEW.memorial_id AND attempt_no = NEW.attempt_no)
    )
    BEGIN
        SELECT RAISE(ABORT, 'execution attempts are immutable by replacement');
    END
    """,
    """
    CREATE TRIGGER execution_attempts_identity_immutable
    BEFORE UPDATE OF attempt_id, schema_version, memorial_id, attempt_no,
                     max_attempts, created_at
    ON execution_attempts
    WHEN OLD.attempt_id IS NOT NEW.attempt_id
      OR OLD.schema_version IS NOT NEW.schema_version
      OR OLD.memorial_id IS NOT NEW.memorial_id
      OR OLD.attempt_no IS NOT NEW.attempt_no
      OR OLD.max_attempts IS NOT NEW.max_attempts
      OR OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(ABORT, 'execution attempt identity is immutable');
    END
    """,
)
_EXECUTION_ATTEMPT_LEDGER_CHECKSUM = hashlib.sha256(
    (
        "0014_execution_attempt_ledger\n"
        + "\n".join(
            " ".join(statement.split()) for statement in _EXECUTION_ATTEMPT_LEDGER_STATEMENTS
        )
    ).encode("utf-8")
).hexdigest()


def _execution_attempt_ledger_upgrade(conn: MigrationConnection) -> None:
    for statement in _EXECUTION_ATTEMPT_LEDGER_STATEMENTS:
        conn.execute(statement)


# --- V15: strict managed side-effect intent/receipt journal ---

_SIDE_EFFECT_JOURNAL_STATEMENTS = (
    """
    CREATE TABLE side_effect_journal (
        intent_id TEXT PRIMARY KEY CHECK (
            length(intent_id) = 64 AND intent_id NOT GLOB '*[^0-9a-f]*'
        ),
        effect_id TEXT NOT NULL UNIQUE CHECK (length(trim(effect_id)) > 0),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE CASCADE,
        memorial_id TEXT NOT NULL REFERENCES memorials(id) ON DELETE CASCADE,
        attempt_id TEXT NOT NULL REFERENCES execution_attempts(attempt_id) ON DELETE RESTRICT,
        owner_id TEXT NOT NULL CHECK (length(trim(owner_id)) > 0),
        fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
        sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
        boundary TEXT NOT NULL CHECK (length(trim(boundary)) > 0),
        operation TEXT NOT NULL CHECK (length(trim(operation)) > 0),
        semantics TEXT NOT NULL CHECK (semantics IN (
            'provider_idempotent','receipt_lookup','workspace_only',
            'untracked_external','opaque_cli'
        )),
        provider_idempotency_key TEXT,
        request_metadata_json TEXT NOT NULL CHECK (
            json_valid(request_metadata_json)
            AND json_type(request_metadata_json) = 'object'
        ),
        request_hash TEXT NOT NULL CHECK (
            length(request_hash) = 64 AND request_hash NOT GLOB '*[^0-9a-f]*'
        ),
        intent_hash TEXT NOT NULL CHECK (
            length(intent_hash) = 64 AND intent_hash NOT GLOB '*[^0-9a-f]*'
        ),
        status TEXT NOT NULL CHECK (status IN ('intended','receipted','uncertain')),
        reason_code TEXT,
        uncertainty_decision_id TEXT
            REFERENCES decision_requests(decision_request_id) ON DELETE RESTRICT,
        receipt_attempt_id TEXT
            REFERENCES execution_attempts(attempt_id) ON DELETE RESTRICT,
        receipt_owner_id TEXT,
        receipt_fencing_token INTEGER,
        provider_receipt_id TEXT,
        receipt_metadata_json TEXT CHECK (
            receipt_metadata_json IS NULL OR (
                json_valid(receipt_metadata_json)
                AND json_type(receipt_metadata_json) = 'object'
            )
        ),
        result_hash TEXT CHECK (
            result_hash IS NULL OR (
                length(result_hash) = 64 AND result_hash NOT GLOB '*[^0-9a-f]*'
            )
        ),
        effective_at TEXT,
        recorded_at TEXT,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
        UNIQUE (memorial_id, sequence_no),
        CHECK (
            (semantics IN ('provider_idempotent','receipt_lookup')
             AND provider_idempotency_key = intent_id)
            OR
            (semantics NOT IN ('provider_idempotent','receipt_lookup')
             AND provider_idempotency_key IS NULL)
        ),
        CHECK (
            (status = 'intended'
             AND reason_code IS NULL AND uncertainty_decision_id IS NULL
             AND receipt_attempt_id IS NULL AND receipt_owner_id IS NULL
             AND receipt_fencing_token IS NULL AND provider_receipt_id IS NULL
             AND receipt_metadata_json IS NULL AND result_hash IS NULL
             AND effective_at IS NULL AND recorded_at IS NULL)
            OR
            (status = 'uncertain'
             AND reason_code IS NOT NULL AND length(trim(reason_code)) > 0
             AND uncertainty_decision_id IS NOT NULL
             AND receipt_attempt_id IS NULL AND receipt_owner_id IS NULL
             AND receipt_fencing_token IS NULL AND provider_receipt_id IS NULL
             AND receipt_metadata_json IS NULL AND result_hash IS NULL
             AND effective_at IS NULL AND recorded_at IS NULL)
            OR
            (status = 'receipted'
             AND reason_code IS NULL AND uncertainty_decision_id IS NULL
             AND receipt_attempt_id IS NOT NULL
             AND receipt_owner_id IS NOT NULL AND length(trim(receipt_owner_id)) > 0
             AND receipt_fencing_token > 0
             AND provider_receipt_id IS NOT NULL
             AND length(trim(provider_receipt_id)) > 0
             AND receipt_metadata_json IS NOT NULL AND result_hash IS NOT NULL
             AND effective_at IS NOT NULL AND recorded_at IS NOT NULL)
        )
    )
    """,
    """
    CREATE UNIQUE INDEX uq_side_effect_journal_provider_key
    ON side_effect_journal(boundary, provider_idempotency_key)
    WHERE provider_idempotency_key IS NOT NULL
    """,
    """
    CREATE INDEX idx_side_effect_journal_attempt
    ON side_effect_journal(attempt_id, sequence_no)
    """,
    """
    CREATE INDEX idx_side_effect_journal_uncertain
    ON side_effect_journal(status, updated_at, intent_id)
    WHERE status = 'uncertain'
    """,
    """
    CREATE TRIGGER side_effect_journal_no_replace
    BEFORE INSERT ON side_effect_journal
    WHEN EXISTS (
        SELECT 1 FROM side_effect_journal
        WHERE intent_id = NEW.intent_id
           OR effect_id = NEW.effect_id
           OR (memorial_id = NEW.memorial_id AND sequence_no = NEW.sequence_no)
    )
    BEGIN
        SELECT RAISE(ABORT, 'side-effect intents are immutable by replacement');
    END
    """,
    """
    CREATE TRIGGER side_effect_journal_identity_immutable
    BEFORE UPDATE OF intent_id, effect_id, schema_version, edict_id, memorial_id,
                     attempt_id, owner_id, fencing_token, sequence_no, boundary,
                     operation, semantics, provider_idempotency_key,
                     request_metadata_json, request_hash, intent_hash, created_at
    ON side_effect_journal
    WHEN OLD.intent_id IS NOT NEW.intent_id
      OR OLD.effect_id IS NOT NEW.effect_id
      OR OLD.schema_version IS NOT NEW.schema_version
      OR OLD.edict_id IS NOT NEW.edict_id
      OR OLD.memorial_id IS NOT NEW.memorial_id
      OR OLD.attempt_id IS NOT NEW.attempt_id
      OR OLD.owner_id IS NOT NEW.owner_id
      OR OLD.fencing_token IS NOT NEW.fencing_token
      OR OLD.sequence_no IS NOT NEW.sequence_no
      OR OLD.boundary IS NOT NEW.boundary
      OR OLD.operation IS NOT NEW.operation
      OR OLD.semantics IS NOT NEW.semantics
      OR OLD.provider_idempotency_key IS NOT NEW.provider_idempotency_key
      OR OLD.request_metadata_json IS NOT NEW.request_metadata_json
      OR OLD.request_hash IS NOT NEW.request_hash
      OR OLD.intent_hash IS NOT NEW.intent_hash
      OR OLD.created_at IS NOT NEW.created_at
    BEGIN
        SELECT RAISE(ABORT, 'side-effect intent identity is immutable');
    END
    """,
)
_SIDE_EFFECT_JOURNAL_CHECKSUM = hashlib.sha256(
    (
        "0015_side_effect_journal\n"
        + "\n".join(" ".join(statement.split()) for statement in _SIDE_EFFECT_JOURNAL_STATEMENTS)
    ).encode("utf-8")
).hexdigest()


def _side_effect_journal_upgrade(conn: MigrationConnection) -> None:
    for statement in _SIDE_EFFECT_JOURNAL_STATEMENTS:
        conn.execute(statement)


# --- V16: content-addressed artifacts and immutable Evidence Bundle v1 ---

_ARTIFACTS_EVIDENCE_STATEMENTS = (
    """
    CREATE TABLE artifact_records (
        digest TEXT PRIMARY KEY CHECK (
            length(digest) = 64 AND digest NOT GLOB '*[^0-9a-f]*'
        ),
        schema_version TEXT NOT NULL CHECK (schema_version = '1.0'),
        size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
        media_type TEXT NOT NULL CHECK (
            length(trim(media_type)) BETWEEN 1 AND 255
        ),
        redaction TEXT NOT NULL CHECK (
            length(trim(redaction)) BETWEEN 1 AND 128
        ),
        uri TEXT NOT NULL UNIQUE CHECK (
            uri = 'artifact://sha256/' || digest
        ),
        root_fingerprint TEXT NOT NULL CHECK (
            length(root_fingerprint) = 64
            AND root_fingerprint NOT GLOB '*[^0-9a-f]*'
        ),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0)
    )
    """,
    """
    CREATE TABLE evidence_bundles (
        bundle_id TEXT PRIMARY KEY CHECK (length(trim(bundle_id)) > 0),
        schema_version TEXT NOT NULL CHECK (schema_version = '1.0'),
        edict_id TEXT NOT NULL REFERENCES edicts(id) ON DELETE RESTRICT,
        memorial_id TEXT NOT NULL UNIQUE REFERENCES memorials(id) ON DELETE RESTRICT,
        status TEXT NOT NULL CHECK (status IN ('open','closed')),
        body_json TEXT NOT NULL CHECK (
            json_valid(body_json) AND json_type(body_json) = 'object'
            AND length(body_json) <= 4194304
        ),
        content_hash TEXT CHECK (
            content_hash IS NULL OR (
                length(content_hash) = 64
                AND content_hash NOT GLOB '*[^0-9a-f]*'
            )
        ),
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        closed_at TEXT,
        CHECK (
            (status = 'open' AND content_hash IS NULL AND closed_at IS NULL)
            OR
            (status = 'closed' AND content_hash IS NOT NULL AND closed_at IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX idx_evidence_bundles_edict
    ON evidence_bundles(edict_id, created_at, bundle_id)
    """,
    """
    CREATE TRIGGER artifact_records_no_update
    BEFORE UPDATE ON artifact_records
    BEGIN
        SELECT RAISE(ABORT, 'artifact metadata is immutable');
    END
    """,
    """
    CREATE TRIGGER artifact_records_no_delete
    BEFORE DELETE ON artifact_records
    BEGIN
        SELECT RAISE(ABORT, 'artifact metadata is immutable');
    END
    """,
    """
    CREATE TRIGGER evidence_bundles_closed_no_update
    BEFORE UPDATE ON evidence_bundles
    WHEN OLD.status = 'closed'
    BEGIN
        SELECT RAISE(ABORT, 'closed evidence bundle is immutable');
    END
    """,
    """
    CREATE TRIGGER evidence_bundles_closed_no_delete
    BEFORE DELETE ON evidence_bundles
    WHEN OLD.status = 'closed'
    BEGIN
        SELECT RAISE(ABORT, 'closed evidence bundle is immutable');
    END
    """,
)
_ARTIFACTS_EVIDENCE_CHECKSUM = hashlib.sha256(
    (
        "0016_artifacts_evidence\n"
        + "\n".join(" ".join(statement.split()) for statement in _ARTIFACTS_EVIDENCE_STATEMENTS)
    ).encode("utf-8")
).hexdigest()
_ARTIFACTS_EVIDENCE_OBJECT_NAMES = (
    "artifact_records",
    "evidence_bundles",
    "idx_evidence_bundles_edict",
    "artifact_records_no_update",
    "artifact_records_no_delete",
    "evidence_bundles_closed_no_update",
    "evidence_bundles_closed_no_delete",
)


def _artifacts_evidence_upgrade(conn: MigrationConnection) -> None:
    placeholders = ",".join("?" for _ in _ARTIFACTS_EVIDENCE_OBJECT_NAMES)
    rows = conn.execute(
        f"SELECT name, sql FROM sqlite_master WHERE name IN ({placeholders})",
        _ARTIFACTS_EVIDENCE_OBJECT_NAMES,
    ).fetchall()
    if rows:
        actual = {str(row[0]): " ".join(str(row[1]).split()) for row in rows}
        expected = {
            name: " ".join(statement.split())
            for name, statement in zip(
                _ARTIFACTS_EVIDENCE_OBJECT_NAMES,
                _ARTIFACTS_EVIDENCE_STATEMENTS,
                strict=True,
            )
        }
        if actual != expected:
            raise SchemaCompatibilityError(
                "existing artifact/evidence schema does not match migration v16"
            )
        return
    for statement in _ARTIFACTS_EVIDENCE_STATEMENTS:
        conn.execute(statement)


# --- V17: durable internal notification delivery and core correlation columns ---

_INTERNAL_DELIVERY_STATEMENTS = (
    """
    CREATE TABLE internal_notification_deliveries (
        delivery_id TEXT PRIMARY KEY CHECK (length(delivery_id) = 64),
        event_id TEXT NOT NULL UNIQUE CHECK (length(trim(event_id)) > 0),
        event_type TEXT NOT NULL CHECK (length(trim(event_type)) > 0),
        correlation_id TEXT NOT NULL CHECK (length(trim(correlation_id)) BETWEEN 1 AND 256),
        edict_id TEXT REFERENCES edicts(id) ON DELETE RESTRICT,
        memorial_id TEXT REFERENCES memorials(id) ON DELETE RESTRICT,
        status TEXT NOT NULL CHECK (
            status IN ('pending','claimed','retry_wait','delivered','dead_letter')
        ),
        available_at TEXT NOT NULL CHECK (length(trim(available_at)) > 0),
        deadline_at TEXT NOT NULL CHECK (length(trim(deadline_at)) > 0),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
        max_attempts INTEGER NOT NULL CHECK (max_attempts > 0),
        lease_owner TEXT,
        lease_expires_at TEXT,
        last_error_json TEXT CHECK (
            last_error_json IS NULL OR (
                json_valid(last_error_json) AND json_type(last_error_json) = 'object'
            )
        ),
        delivered_at TEXT,
        version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
        CHECK (deadline_at >= created_at),
        CHECK (
            (status = 'claimed' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
            OR
            (status <> 'claimed' AND lease_owner IS NULL AND lease_expires_at IS NULL)
        ),
        CHECK (
            (status = 'delivered' AND delivered_at IS NOT NULL)
            OR
            (status <> 'delivered' AND delivered_at IS NULL)
        )
    )
    """,
    """
    CREATE INDEX idx_internal_notification_delivery_claim
    ON internal_notification_deliveries(status, available_at, lease_expires_at, deadline_at)
    """,
    """
    CREATE INDEX idx_internal_notification_delivery_correlation
    ON internal_notification_deliveries(correlation_id, created_at)
    """,
)
_CORE_CORRELATION_COLUMNS = (
    "outbox_events",
    "decision_requests",
    "run_states",
    "execution_attempts",
    "side_effect_journal",
    "evidence_bundles",
)
_INTERNAL_DELIVERY_CHECKSUM = hashlib.sha256(
    (
        "0017_internal_notification_delivery\n"
        + "\n".join(" ".join(statement.split()) for statement in _INTERNAL_DELIVERY_STATEMENTS)
        + "\ncorrelation-columns-v1:"
        + ",".join(_CORE_CORRELATION_COLUMNS)
    ).encode("utf-8")
).hexdigest()


def _internal_notification_delivery_upgrade(conn: MigrationConnection) -> None:
    existing = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE name IN (
            'internal_notification_deliveries',
            'idx_internal_notification_delivery_claim',
            'idx_internal_notification_delivery_correlation'
        )
        ORDER BY name
        """
    ).fetchall()
    if existing:
        raise SchemaCompatibilityError(
            "internal notification delivery objects already exist outside migration v17"
        )
    for table in _CORE_CORRELATION_COLUMNS:
        columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "correlation_id" in columns:
            raise SchemaCompatibilityError(
                f"{table}.correlation_id already exists outside migration v17"
            )
        conn.execute(f"ALTER TABLE {table} ADD COLUMN correlation_id TEXT")
    for statement in _INTERNAL_DELIVERY_STATEMENTS:
        conn.execute(statement)


_PRE_EVOLUTION_MIGRATIONS = (
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
    Migration(
        version=5,
        name="0005_governed_apply_bindings",
        checksum=_GOVERNED_APPLY_BINDING_CHECKSUM,
        upgrade=_governed_apply_bindings_upgrade,
    ),
    Migration(
        version=6,
        name="0006_seed_default_personas",
        checksum=_SEED_DEFAULT_PERSONAS_CHECKSUM,
        upgrade=_seed_default_personas_upgrade,
    ),
    Migration(
        version=7,
        name="0007_system_audit_events",
        checksum=_SYSTEM_AUDIT_CHECKSUM,
        upgrade=_system_audit_upgrade,
    ),
    Migration(
        version=8,
        name="0008_encrypt_mcp_secret_mappings",
        checksum=_MCP_SECRET_MAPPING_CHECKSUM,
        upgrade=_mcp_secret_mapping_upgrade,
    ),
    Migration(
        version=9,
        name="0009_durable_edict_ingress",
        checksum=_DURABLE_EDICT_INGRESS_CHECKSUM,
        upgrade=_durable_edict_ingress_upgrade,
    ),
    Migration(
        version=10,
        name="0010_telegram_seen_instance_identity",
        checksum=_TELEGRAM_SEEN_INSTANCE_IDENTITY_CHECKSUM,
        upgrade=_telegram_seen_instance_identity_upgrade,
    ),
    Migration(
        version=11,
        name="0011_decisions_run_state",
        checksum=_DECISIONS_RUN_STATE_CHECKSUM,
        upgrade=_decisions_run_state_upgrade,
    ),
    Migration(
        version=12,
        name="0012_decision_run_state_guards",
        checksum=_DECISION_RUN_STATE_GUARD_CHECKSUM,
        upgrade=_decision_run_state_guards_upgrade,
    ),
    Migration(
        version=13,
        name="0013_governed_apply_decision_binding",
        checksum=_GOVERNED_APPLY_DECISION_BINDING_CHECKSUM,
        upgrade=_governed_apply_decision_binding_upgrade,
    ),
    Migration(
        version=14,
        name="0014_execution_attempt_ledger",
        checksum=_EXECUTION_ATTEMPT_LEDGER_CHECKSUM,
        upgrade=_execution_attempt_ledger_upgrade,
    ),
    Migration(
        version=15,
        name="0015_side_effect_journal",
        checksum=_SIDE_EFFECT_JOURNAL_CHECKSUM,
        upgrade=_side_effect_journal_upgrade,
    ),
    Migration(
        version=16,
        name="0016_artifacts_evidence",
        checksum=_ARTIFACTS_EVIDENCE_CHECKSUM,
        upgrade=_artifacts_evidence_upgrade,
    ),
    Migration(
        version=17,
        name="0017_internal_notification_delivery",
        checksum=_INTERNAL_DELIVERY_CHECKSUM,
        upgrade=_internal_notification_delivery_upgrade,
    ),
)


# --- Live tail + 1: governed evolution candidate persistence ---

_EVOLUTION_CANDIDATE_MIGRATION_VERSION = _PRE_EVOLUTION_MIGRATIONS[-1].version + 1
_EVOLUTION_CANDIDATE_MIGRATION_NAME = (
    f"{_EVOLUTION_CANDIDATE_MIGRATION_VERSION:04d}_governed_evolution_candidates"
)
_EVOLUTION_CANDIDATE_STATEMENTS = (
    """
    CREATE TABLE evolution_candidates (
        candidate_id TEXT PRIMARY KEY CHECK (length(trim(candidate_id)) BETWEEN 1 AND 256),
        schema_version INTEGER NOT NULL CHECK (schema_version = 1),
        kind TEXT NOT NULL CHECK (kind IN ('memory','skill','policy','persona','code')),
        subject_key TEXT NOT NULL CHECK (length(trim(subject_key)) BETWEEN 1 AND 512),
        provenance_json TEXT NOT NULL CHECK (
            json_valid(provenance_json) AND json_type(provenance_json) = 'object'
        ),
        provenance_hash TEXT NOT NULL CHECK (length(provenance_hash) = 64),
        base_json TEXT NOT NULL CHECK (json_valid(base_json) AND json_type(base_json) = 'object'),
        candidate_ref_json TEXT NOT NULL CHECK (
            json_valid(candidate_ref_json) AND json_type(candidate_ref_json) = 'object'
        ),
        diff_artifact_digest TEXT NOT NULL CHECK (length(diff_artifact_digest) = 64),
        evolution_contract_json TEXT NOT NULL CHECK (
            json_valid(evolution_contract_json) AND json_type(evolution_contract_json) = 'object'
        ),
        evolution_contract_hash TEXT NOT NULL CHECK (length(evolution_contract_hash) = 64),
        gate_snapshot_version INTEGER NOT NULL DEFAULT 0 CHECK (gate_snapshot_version >= 0),
        evidence_bundle_ids_json TEXT NOT NULL CHECK (
            json_valid(evidence_bundle_ids_json)
            AND json_type(evidence_bundle_ids_json) = 'array'
        ),
        routing_json TEXT CHECK (
            routing_json IS NULL OR (
                json_valid(routing_json) AND json_type(routing_json) = 'object'
            )
        ),
        rollback_json TEXT NOT NULL CHECK (
            json_valid(rollback_json) AND json_type(rollback_json) = 'object'
        ),
        lifecycle TEXT NOT NULL CHECK (
            lifecycle IN (
                'proposed','staged','evaluating','blocked','ready','canary','promoted',
                'rejected','rollback_pending','rolled_back','archived'
            )
        ),
        version INTEGER NOT NULL CHECK (version > 0),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0),
        UNIQUE (kind, subject_key, candidate_id)
    )
    """,
    "CREATE INDEX idx_evolution_candidates_lifecycle ON evolution_candidates(lifecycle, updated_at)",
    """
    CREATE TABLE evolution_gate_snapshots (
        gate_snapshot_id TEXT PRIMARY KEY CHECK (length(trim(gate_snapshot_id)) BETWEEN 1 AND 256),
        candidate_id TEXT NOT NULL REFERENCES evolution_candidates(candidate_id) ON DELETE RESTRICT,
        candidate_version INTEGER NOT NULL CHECK (candidate_version > 0),
        gate_snapshot_version INTEGER NOT NULL CHECK (gate_snapshot_version > 0),
        snapshot_json TEXT NOT NULL CHECK (
            json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'
        ),
        snapshot_hash TEXT NOT NULL CHECK (length(snapshot_hash) = 64),
        evidence_bundle_ids_json TEXT NOT NULL CHECK (
            json_valid(evidence_bundle_ids_json)
            AND json_type(evidence_bundle_ids_json) = 'array'
        ),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        UNIQUE (candidate_id, gate_snapshot_version)
    )
    """,
    """
    CREATE TABLE evolution_lifecycle_journal (
        journal_id TEXT PRIMARY KEY CHECK (length(trim(journal_id)) BETWEEN 1 AND 256),
        candidate_id TEXT NOT NULL REFERENCES evolution_candidates(candidate_id) ON DELETE RESTRICT,
        candidate_version INTEGER NOT NULL CHECK (candidate_version > 0),
        from_lifecycle TEXT,
        to_lifecycle TEXT NOT NULL CHECK (
            to_lifecycle IN (
                'proposed','staged','evaluating','blocked','ready','canary','promoted',
                'rejected','rollback_pending','rolled_back','archived'
            )
        ),
        decision_request_id TEXT REFERENCES decision_requests(decision_request_id) ON DELETE RESTRICT,
        entry_json TEXT NOT NULL CHECK (json_valid(entry_json) AND json_type(entry_json) = 'object'),
        entry_hash TEXT NOT NULL CHECK (length(entry_hash) = 64),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        CHECK (
            from_lifecycle IS NULL OR from_lifecycle IN (
                'proposed','staged','evaluating','blocked','ready','canary','promoted',
                'rejected','rollback_pending','rolled_back','archived'
            )
        ),
        UNIQUE (candidate_id, candidate_version)
    )
    """,
    """
    CREATE TABLE evolution_promotion_journal (
        promotion_journal_id TEXT PRIMARY KEY CHECK (
            length(trim(promotion_journal_id)) BETWEEN 1 AND 256
        ),
        command_key TEXT NOT NULL CHECK (length(trim(command_key)) BETWEEN 1 AND 256),
        candidate_id TEXT NOT NULL REFERENCES evolution_candidates(candidate_id) ON DELETE RESTRICT,
        candidate_version INTEGER NOT NULL CHECK (candidate_version > 0),
        gate_snapshot_version INTEGER NOT NULL CHECK (gate_snapshot_version > 0),
        action TEXT NOT NULL CHECK (action IN ('start_canary','promote','rollback')),
        status TEXT NOT NULL CHECK (
            status IN ('intended','applied','denied','rollback_pending','completed','failed')
        ),
        decision_request_id TEXT REFERENCES decision_requests(decision_request_id) ON DELETE RESTRICT,
        entry_json TEXT NOT NULL CHECK (json_valid(entry_json) AND json_type(entry_json) = 'object'),
        entry_hash TEXT NOT NULL CHECK (length(entry_hash) = 64),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        UNIQUE (command_key, status)
    )
    """,
    """
    CREATE TABLE evolution_routing_allocations (
        candidate_id TEXT PRIMARY KEY REFERENCES evolution_candidates(candidate_id) ON DELETE RESTRICT,
        routing_version INTEGER NOT NULL CHECK (routing_version > 0),
        allocation_basis_points INTEGER NOT NULL CHECK (
            allocation_basis_points BETWEEN 0 AND 10000
        ),
        allocation_seed_id TEXT NOT NULL CHECK (length(trim(allocation_seed_id)) BETWEEN 1 AND 256),
        routing_json TEXT NOT NULL CHECK (json_valid(routing_json) AND json_type(routing_json) = 'object'),
        routing_hash TEXT NOT NULL CHECK (length(routing_hash) = 64),
        version INTEGER NOT NULL CHECK (version > 0),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0),
        updated_at TEXT NOT NULL CHECK (length(trim(updated_at)) > 0)
    )
    """,
    """
    CREATE TABLE run_evolution_assignments (
        assignment_id TEXT PRIMARY KEY CHECK (length(trim(assignment_id)) BETWEEN 1 AND 256),
        memorial_id TEXT NOT NULL UNIQUE REFERENCES memorials(id) ON DELETE RESTRICT,
        candidate_id TEXT REFERENCES evolution_candidates(candidate_id) ON DELETE RESTRICT,
        routing_version INTEGER NOT NULL CHECK (routing_version > 0),
        bucket INTEGER NOT NULL CHECK (bucket BETWEEN 0 AND 9999),
        champion_ref_json TEXT NOT NULL CHECK (
            json_valid(champion_ref_json) AND json_type(champion_ref_json) = 'object'
        ),
        selected_ref_json TEXT NOT NULL CHECK (
            json_valid(selected_ref_json) AND json_type(selected_ref_json) = 'object'
        ),
        overlay_digest TEXT NOT NULL CHECK (length(overlay_digest) = 64),
        assignment_json TEXT NOT NULL CHECK (
            json_valid(assignment_json) AND json_type(assignment_json) = 'object'
        ),
        assignment_hash TEXT NOT NULL CHECK (length(assignment_hash) = 64),
        created_at TEXT NOT NULL CHECK (length(trim(created_at)) > 0)
    )
    """,
    """
    CREATE TRIGGER evolution_gate_snapshots_no_update
    BEFORE UPDATE ON evolution_gate_snapshots BEGIN
        SELECT RAISE(ABORT, 'evolution gate snapshot is immutable');
    END
    """,
    """
    CREATE TRIGGER evolution_gate_snapshots_no_delete
    BEFORE DELETE ON evolution_gate_snapshots BEGIN
        SELECT RAISE(ABORT, 'evolution gate snapshot is immutable');
    END
    """,
    """
    CREATE TRIGGER evolution_lifecycle_journal_no_update
    BEFORE UPDATE ON evolution_lifecycle_journal BEGIN
        SELECT RAISE(ABORT, 'evolution lifecycle journal is immutable');
    END
    """,
    """
    CREATE TRIGGER evolution_lifecycle_journal_no_delete
    BEFORE DELETE ON evolution_lifecycle_journal BEGIN
        SELECT RAISE(ABORT, 'evolution lifecycle journal is immutable');
    END
    """,
    """
    CREATE TRIGGER evolution_promotion_journal_no_update
    BEFORE UPDATE ON evolution_promotion_journal BEGIN
        SELECT RAISE(ABORT, 'evolution promotion journal is immutable');
    END
    """,
    """
    CREATE TRIGGER evolution_promotion_journal_no_delete
    BEFORE DELETE ON evolution_promotion_journal BEGIN
        SELECT RAISE(ABORT, 'evolution promotion journal is immutable');
    END
    """,
    """
    CREATE TRIGGER run_evolution_assignments_no_update
    BEFORE UPDATE ON run_evolution_assignments BEGIN
        SELECT RAISE(ABORT, 'evolution assignment is immutable');
    END
    """,
    """
    CREATE TRIGGER run_evolution_assignments_no_delete
    BEFORE DELETE ON run_evolution_assignments BEGIN
        SELECT RAISE(ABORT, 'evolution assignment is immutable');
    END
    """,
)
_EVOLUTION_CANDIDATE_CHECKSUM = hashlib.sha256(
    (
        _EVOLUTION_CANDIDATE_MIGRATION_NAME
        + "\n"
        + "\n".join(" ".join(statement.split()) for statement in _EVOLUTION_CANDIDATE_STATEMENTS)
    ).encode("utf-8")
).hexdigest()
_EVOLUTION_CANDIDATE_OBJECT_NAMES = tuple(
    statement.split()[2]
    for statement in _EVOLUTION_CANDIDATE_STATEMENTS
    if statement.lstrip().startswith(("CREATE TABLE", "CREATE INDEX", "CREATE TRIGGER"))
)


def _governed_evolution_candidates_upgrade(conn: MigrationConnection) -> None:
    placeholders = ",".join("?" for _ in _EVOLUTION_CANDIDATE_OBJECT_NAMES)
    rows = conn.execute(
        f"SELECT name, sql FROM sqlite_master WHERE name IN ({placeholders})",
        _EVOLUTION_CANDIDATE_OBJECT_NAMES,
    ).fetchall()
    if rows:
        actual = {str(row[0]): " ".join(str(row[1]).split()) for row in rows}
        expected = {
            name: " ".join(statement.split())
            for name, statement in zip(
                _EVOLUTION_CANDIDATE_OBJECT_NAMES,
                _EVOLUTION_CANDIDATE_STATEMENTS,
                strict=True,
            )
        }
        if actual != expected:
            raise SchemaCompatibilityError(
                "existing governed evolution schema does not match the live migration"
            )
        return
    for statement in _EVOLUTION_CANDIDATE_STATEMENTS:
        conn.execute(statement)


MIGRATIONS = _PRE_EVOLUTION_MIGRATIONS + (
    Migration(
        version=_EVOLUTION_CANDIDATE_MIGRATION_VERSION,
        name=_EVOLUTION_CANDIDATE_MIGRATION_NAME,
        checksum=_EVOLUTION_CANDIDATE_CHECKSUM,
        upgrade=_governed_evolution_candidates_upgrade,
    ),
)


def _matches_canonical_schema(conn: _Connection) -> bool:
    """检查库的迁移 owned 对象是否与全部迁移完成后的权威形状语义等价。

    比对基准是"内存库执行完整 MIGRATIONS 序列"的最终形状，因此随版本演进自动
    保持最新；库中额外的非 owned 表（用户扩展）被忽略，`memory_entries` 上由
    FTS 初始化建立的可选触发器按既有 `_EXPECTED_OPTIONAL_TRIGGERS` 准则豁免。
    """

    conn_tables = _table_names(conn)
    if _RESERVED_TEMP_TABLES & conn_tables:
        # 残留迁移临时表意味着此前有迁移中断，必须交由重放路径拒绝。
        return False
    reference = sqlite3.connect(":memory:")
    reference.execute("PRAGMA foreign_keys=ON")
    try:
        apply_migrations(reference, MIGRATIONS)
        owned_tables = {
            name
            for name in _table_names(reference)
            if name != "schema_migrations" and not name.startswith("sqlite_")
        }
        if conn_tables & owned_tables != owned_tables:
            return False
        for table in owned_tables:
            if _table_signature(conn, table) != _table_signature(reference, table):
                return False
        if _named_indexes(conn, owned_tables) != _named_indexes(reference, owned_tables):
            return False
        expected_triggers = _workspace_trigger_signatures(reference, owned_tables)
        actual_triggers = _workspace_trigger_signatures(conn, owned_tables)
        required_actual = {
            name: signature
            for name, signature in actual_triggers.items()
            if _EXPECTED_OPTIONAL_TRIGGERS.get(name) != signature
        }
        return required_actual == expected_triggers
    finally:
        reference.close()


def run_migrations(conn: sqlite3.Connection) -> tuple[int, ...]:
    """Apply the versioned schema sequence and return newly applied versions.

    Pre-ledger databases that already match the complete canonical shape are
    adopted in place (the ledger is recorded without re-executing upgrades), so
    each migration callback only ever observes schemas at or before its own
    version during a replay.
    """

    if (
        not ledger_exists(conn)
        and _table_names(conn) & _OWNED_TABLES
        and _matches_canonical_schema(conn)
    ):
        return adopt_migrations(conn, MIGRATIONS)
    return apply_migrations(conn, MIGRATIONS)


__all__ = [
    "MIGRATIONS",
    "SCHEMA_V1_STATEMENTS",
    "SchemaCompatibilityError",
    "run_migrations",
]
