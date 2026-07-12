"""Append-only persistence for governed workspace records."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime

from tianshu.models.workspace import (
    ApplyDecision,
    ApplyReceipt,
    CanonicalChangeSet,
    RestorePoint,
    WorkspaceLease,
    WorkspaceLeaseState,
    WorkspaceStagingIdentity,
)
from tianshu.storage.mappers import (
    row_to_apply_decision,
    row_to_apply_receipt,
    row_to_canonical_change_set,
    row_to_restore_point,
    row_to_workspace_lease,
)


class WorkspaceStateConflict(RuntimeError):
    """The caller attempted a stale or invalid lease transition."""


_LEASE_SELECT = """
SELECT l.*,
       a.git_dir AS staging_git_dir,
       a.git_dir_identity AS staging_git_dir_identity,
       s.state,
       s.version AS state_version
FROM workspace_leases AS l
JOIN workspace_lease_states AS s ON s.lease_id = l.id
LEFT JOIN workspace_staging_identities AS a ON a.lease_id = l.id
WHERE {predicate}
  AND s.version = (
      SELECT MAX(latest.version)
      FROM workspace_lease_states AS latest
      WHERE latest.lease_id = l.id
  )
"""

_DECISION_SELECT = """
SELECT d.*, s.state, s.version AS state_version
FROM apply_decisions AS d
JOIN apply_decision_states AS s ON s.decision_id = d.id
WHERE {predicate}
  AND s.version = (
      SELECT MAX(latest.version)
      FROM apply_decision_states AS latest
      WHERE latest.decision_id = d.id
  )
"""


class WorkspaceMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    def create_workspace_foundation(
        self,
        lease: WorkspaceLease,
        restore_point: RestorePoint | None,
    ) -> None:
        if lease.state is not WorkspaceLeaseState.STARTING or lease.state_version != 1:
            raise ValueError("new workspace leases must start at version 1")
        if lease.source_kind == "git":
            if restore_point is None:
                raise ValueError("Git workspace leases require a restore point")
            restore_binding = (
                restore_point.lease_id,
                restore_point.source_repository_id,
                restore_point.source_root,
                restore_point.source_git_dir,
                restore_point.source_git_dir_identity,
                restore_point.base_revision,
            )
            lease_binding = (
                lease.id,
                lease.source_repository_id,
                lease.source_root,
                lease.source_git_dir,
                lease.source_git_dir_identity,
                lease.base_revision,
            )
            if restore_binding != lease_binding:
                raise ValueError("restore point must exactly bind the new Git lease")
        elif restore_point is not None:
            raise ValueError("scratch workspace leases must not have a restore point")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO workspace_leases (
                    id, schema_version, run_id, lineage_root_run_id, parent_run_id,
                    attempt, source_kind, apply_mode, source_root,
                    source_repository_id, source_git_dir, source_git_dir_identity,
                    base_revision, staging_root, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lease.id,
                    lease.schema_version,
                    lease.run_id,
                    lease.lineage_root_run_id,
                    lease.parent_run_id,
                    lease.attempt,
                    lease.source_kind,
                    lease.apply_mode,
                    lease.source_root,
                    lease.source_repository_id,
                    lease.source_git_dir,
                    lease.source_git_dir_identity,
                    lease.base_revision,
                    lease.staging_root,
                    lease.created_at.isoformat(),
                ),
            )
            self._conn.execute(
                """
                INSERT INTO workspace_lease_states
                    (lease_id, version, state, detail, created_at)
                VALUES (?, 1, ?, NULL, ?)
                """,
                (lease.id, lease.state.value, lease.created_at.isoformat()),
            )
            if restore_point is not None:
                self._insert_restore_point(restore_point)

    def _insert_restore_point(self, point: RestorePoint) -> None:
        self._conn.execute(
            """
            INSERT INTO restore_points (
                id, schema_version, lease_id, source_repository_id, source_root,
                source_git_dir, source_git_dir_identity, base_revision,
                source_head_revision, source_head_ref, source_index_tree,
                source_status_hash, canonical_json, content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                point.id,
                point.schema_version,
                point.lease_id,
                point.source_repository_id,
                point.source_root,
                point.source_git_dir,
                point.source_git_dir_identity,
                point.base_revision,
                point.source_head_revision,
                point.source_head_ref,
                point.source_index_tree,
                point.source_status_hash,
                point.canonical_json(),
                point.content_hash,
                point.created_at.isoformat(),
            ),
        )

    def save_workspace_staging_identity(
        self, identity: WorkspaceStagingIdentity
    ) -> WorkspaceStagingIdentity:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO workspace_staging_identities (
                    lease_id, schema_version, staging_root, git_dir,
                    git_dir_identity, source_repository_id, base_revision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity.lease_id,
                    identity.schema_version,
                    identity.staging_root,
                    identity.git_dir,
                    identity.git_dir_identity,
                    identity.source_repository_id,
                    identity.base_revision,
                    identity.created_at.isoformat(),
                ),
            )
        return identity

    def get_workspace_lease(self, lease_id: str) -> WorkspaceLease | None:
        with self._lock:
            row = self._conn.execute(
                _LEASE_SELECT.format(predicate="l.id = ?"), (lease_id,)
            ).fetchone()
        return row_to_workspace_lease(row) if row is not None else None

    def get_workspace_lease_by_run(self, run_id: str) -> WorkspaceLease | None:
        with self._lock:
            row = self._conn.execute(
                _LEASE_SELECT.format(predicate="l.run_id = ?"), (run_id,)
            ).fetchone()
        return row_to_workspace_lease(row) if row is not None else None

    def list_open_workspace_leases(self) -> tuple[WorkspaceLease, ...]:
        query = (
            _LEASE_SELECT.format(
                predicate="s.state IN ('starting', 'active', 'closing', 'cleanup_failed')"
            )
            + " ORDER BY l.created_at, l.id"
        )
        with self._lock:
            rows = self._conn.execute(query).fetchall()
        return tuple(row_to_workspace_lease(row) for row in rows)

    def transition_workspace_lease(
        self,
        lease_id: str,
        *,
        expected_version: int,
        expected_state: WorkspaceLeaseState,
        new_state: WorkspaceLeaseState,
        created_at: datetime,
        detail: str | None = None,
    ) -> WorkspaceLease:
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT version, state FROM workspace_lease_states
                WHERE lease_id = ? ORDER BY version DESC LIMIT 1
                """,
                (lease_id,),
            ).fetchone()
            if (
                row is None
                or int(row["version"]) != expected_version
                or str(row["state"]) != expected_state.value
            ):
                raise WorkspaceStateConflict("workspace lease state changed")
            try:
                self._conn.execute(
                    """
                    INSERT INTO workspace_lease_states
                        (lease_id, version, state, detail, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        lease_id,
                        expected_version + 1,
                        new_state.value,
                        detail,
                        created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise WorkspaceStateConflict("invalid workspace lease transition") from exc
            updated_row = self._conn.execute(
                _LEASE_SELECT.format(predicate="l.id = ? AND s.version = ?"),
                (lease_id, expected_version + 1),
            ).fetchone()
            if updated_row is None:  # pragma: no cover - protected by the transaction
                raise WorkspaceStateConflict("workspace lease disappeared")
            updated = row_to_workspace_lease(updated_row)
        return updated

    def get_restore_point(self, point_id: str) -> RestorePoint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM restore_points WHERE id = ?", (point_id,)
            ).fetchone()
        return row_to_restore_point(row) if row is not None else None

    def get_restore_point_for_lease(self, lease_id: str) -> RestorePoint | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM restore_points WHERE lease_id = ?", (lease_id,)
            ).fetchone()
        return row_to_restore_point(row) if row is not None else None

    def next_change_set_sequence(self, lease_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM canonical_change_sets WHERE lease_id = ?",
                (lease_id,),
            ).fetchone()
        return int(row[0])

    def save_canonical_change_set(self, change_set: CanonicalChangeSet) -> CanonicalChangeSet:
        with self._lock, self._conn:
            latest = self._conn.execute(
                """
                SELECT * FROM canonical_change_sets
                WHERE lease_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (change_set.lease_id,),
            ).fetchone()
            if latest is not None and str(latest["content_hash"]) == change_set.content_hash:
                return row_to_canonical_change_set(latest)
            expected_sequence = 1 if latest is None else int(latest["sequence"]) + 1
            if change_set.sequence != expected_sequence:
                raise WorkspaceStateConflict("canonical change set sequence changed")
            self._conn.execute(
                """
                INSERT INTO canonical_change_sets (
                    id, schema_version, lease_id, restore_point_id,
                    source_repository_id, base_revision, sequence, canonical_json,
                    content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change_set.id,
                    change_set.schema_version,
                    change_set.lease_id,
                    change_set.restore_point_id,
                    change_set.source_repository_id,
                    change_set.base_revision,
                    change_set.sequence,
                    change_set.canonical_json(),
                    change_set.content_hash,
                    change_set.created_at.isoformat(),
                ),
            )
        return change_set

    def get_canonical_change_set(self, change_set_id: str) -> CanonicalChangeSet | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM canonical_change_sets WHERE id = ?", (change_set_id,)
            ).fetchone()
        return row_to_canonical_change_set(row) if row is not None else None

    def get_latest_canonical_change_set_for_lease(self, lease_id: str) -> CanonicalChangeSet | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM canonical_change_sets
                WHERE lease_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (lease_id,),
            ).fetchone()
        return row_to_canonical_change_set(row) if row is not None else None

    def save_apply_decision(self, decision: ApplyDecision) -> None:
        if decision.state != "pending" or decision.state_version != 1:
            raise ValueError("new apply decisions must be pending at version 1")
        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT 1 FROM apply_decisions WHERE lease_id = ? LIMIT 1",
                (decision.lease_id,),
            ).fetchone()
            if existing is not None:
                raise WorkspaceStateConflict("apply authority was already issued for this lease")
            try:
                self._conn.execute(
                    """
                    INSERT INTO apply_decisions (
                        id, schema_version, run_id, lease_id, restore_point_id,
                        restore_point_hash, change_set_id, change_set_hash,
                        source_repository_id, source_root, source_git_dir_identity,
                        base_revision, source_head_revision, source_head_ref,
                        source_index_tree, source_status_hash, staging_root,
                        staging_git_dir_identity, principal_digest, apply_scope, reason,
                        decision_hash, token_hash, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision.id,
                        decision.schema_version,
                        decision.run_id,
                        decision.lease_id,
                        decision.restore_point_id,
                        decision.restore_point_hash,
                        decision.change_set_id,
                        decision.change_set_hash,
                        decision.source_repository_id,
                        decision.source_root,
                        decision.source_git_dir_identity,
                        decision.base_revision,
                        decision.source_head_revision,
                        decision.source_head_ref,
                        decision.source_index_tree,
                        decision.source_status_hash,
                        decision.staging_root,
                        decision.staging_git_dir_identity,
                        decision.principal_digest,
                        decision.apply_scope,
                        decision.reason,
                        decision.decision_hash,
                        decision.token_hash,
                        decision.expires_at.isoformat(),
                        decision.created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "apply authority already issued" in str(exc):
                    raise WorkspaceStateConflict(
                        "apply authority was already issued for this lease"
                    ) from exc
                raise
            self._conn.execute(
                """
                INSERT INTO apply_decision_states
                    (decision_id, version, state, receipt_id, created_at)
                VALUES (?, 1, 'pending', NULL, ?)
                """,
                (decision.id, decision.created_at.isoformat()),
            )

    def get_apply_decision(self, decision_id: str) -> ApplyDecision | None:
        with self._lock:
            row = self._conn.execute(
                _DECISION_SELECT.format(predicate="d.id = ?"), (decision_id,)
            ).fetchone()
        return row_to_apply_decision(row) if row is not None else None

    def get_latest_apply_decision_for_lease(self, lease_id: str) -> ApplyDecision | None:
        with self._lock:
            row = self._conn.execute(
                _DECISION_SELECT.format(predicate="d.lease_id = ?")
                + """
                  ORDER BY
                    CASE
                      WHEN EXISTS (
                        SELECT 1 FROM apply_receipts AS receipt
                        WHERE receipt.decision_id = d.id
                      ) THEN 0
                      WHEN s.state <> 'pending' THEN 1
                      ELSE 2
                    END,
                    d.created_at DESC,
                    d.id DESC
                  LIMIT 1
                """,
                (lease_id,),
            ).fetchone()
        return row_to_apply_decision(row) if row is not None else None

    def has_unreceipted_apply_claim_for_lease(self, lease_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM apply_decisions AS d
                    JOIN apply_decision_states AS s ON s.decision_id = d.id
                    LEFT JOIN apply_receipts AS r
                      ON r.decision_id = d.id AND r.id = s.receipt_id
                    WHERE d.lease_id = ?
                      AND s.state = 'consumed'
                      AND s.version = (
                          SELECT MAX(latest.version)
                          FROM apply_decision_states AS latest
                          WHERE latest.decision_id = d.id
                      )
                      AND (s.receipt_id IS NULL OR r.id IS NULL)
                )
                """,
                (lease_id,),
            ).fetchone()
        return bool(row[0])

    def transition_apply_decision(
        self,
        decision_id: str,
        *,
        expected_version: int,
        new_state: str,
        created_at: datetime,
        receipt_id: str | None = None,
    ) -> ApplyDecision:
        if new_state not in {"consumed", "expired", "revoked"}:
            raise ValueError("apply decision terminal state is invalid")
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT version, state FROM apply_decision_states
                WHERE decision_id = ? ORDER BY version DESC LIMIT 1
                """,
                (decision_id,),
            ).fetchone()
            if (
                row is None
                or int(row["version"]) != expected_version
                or str(row["state"]) != "pending"
            ):
                raise WorkspaceStateConflict("apply decision state changed")
            try:
                self._conn.execute(
                    """
                    INSERT INTO apply_decision_states
                        (decision_id, version, state, receipt_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        decision_id,
                        expected_version + 1,
                        new_state,
                        receipt_id,
                        created_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise WorkspaceStateConflict("invalid apply decision transition") from exc
            updated_row = self._conn.execute(
                _DECISION_SELECT.format(predicate="d.id = ? AND s.version = ?"),
                (decision_id, expected_version + 1),
            ).fetchone()
            if updated_row is None:  # pragma: no cover - protected by the transaction
                raise WorkspaceStateConflict("apply decision disappeared")
            updated = row_to_apply_decision(updated_row)
        return updated

    def claim_apply_decision(
        self,
        decision_id: str,
        *,
        expected_version: int,
        receipt_id: str,
        created_at: datetime,
    ) -> ApplyDecision:
        return self.transition_apply_decision(
            decision_id,
            expected_version=expected_version,
            new_state="consumed",
            receipt_id=receipt_id,
            created_at=created_at,
        )

    def save_apply_receipt(self, receipt: ApplyReceipt) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO apply_receipts (
                    id, schema_version, decision_id, decision_hash, lease_id,
                    change_set_id, change_set_hash, outcome, detail,
                    pre_source_head, pre_source_status_hash, post_source_head,
                    post_source_status_hash, rollback_status, failure_code,
                    evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.id,
                    receipt.schema_version,
                    receipt.decision_id,
                    receipt.decision_hash,
                    receipt.lease_id,
                    receipt.change_set_id,
                    receipt.change_set_hash,
                    receipt.outcome,
                    receipt.detail,
                    receipt.pre_source_head,
                    receipt.pre_source_status_hash,
                    receipt.post_source_head,
                    receipt.post_source_status_hash,
                    receipt.rollback_status,
                    receipt.failure_code,
                    json.dumps(receipt.evidence, separators=(",", ":"), sort_keys=True),
                    receipt.created_at.isoformat(),
                ),
            )

    def get_apply_receipt(self, receipt_id: str) -> ApplyReceipt | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM apply_receipts WHERE id = ?", (receipt_id,)
            ).fetchone()
        return row_to_apply_receipt(row) if row is not None else None

    def get_apply_receipt_for_decision(self, decision_id: str) -> ApplyReceipt | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM apply_receipts WHERE decision_id = ?", (decision_id,)
            ).fetchone()
        return row_to_apply_receipt(row) if row is not None else None


__all__ = ["WorkspaceMixin", "WorkspaceStateConflict"]
