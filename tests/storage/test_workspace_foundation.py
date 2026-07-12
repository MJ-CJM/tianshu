"""G1.4 workspace domain and append-only persistence foundation."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tianshu.models.workspace import (
    ApplyDecision,
    ApplyReceipt,
    CanonicalChange,
    CanonicalChangeSet,
    RestorePoint,
    WorkspaceLease,
    WorkspaceLeaseState,
    WorkspaceStagingIdentity,
)
from tianshu.storage import Storage
from tianshu.storage.migration_ledger import MigrationExecutionError, apply_migrations
from tianshu.storage.migrations import MIGRATIONS, run_migrations
from tianshu.storage.workspace_repo import WorkspaceMixin, WorkspaceStateConflict

_V1_CHECKSUM = "9672603c12dd858ea714b291d6ed94f1a27cb373bfcff97665b6316b4aa552a6"
_V2_CHECKSUM = "a2bbf753e0c3244fccc86be2d4588af2c926399f6dfa0dba0af5d0c060179c5a"
_V3_CHECKSUM = "07cb59c354035674fbcabcf1a037b4b273ae43b4e1e4dd8427cf90361bff2ff8"
_V4_CHECKSUM = "1c0a028e0ea16475b9de5eb0c843f81aa275ddf62c0aca3c067bf8408dd9bee5"
_V5_CHECKSUM = "c73294984096ea15e32d6ce80294f82323408cda12e82efea645ad8f35c5abc6"
_NOW = datetime(2026, 7, 12, tzinfo=UTC)
_SHA = "a" * 40


def _lease(*, run_id: str = "run-1") -> WorkspaceLease:
    return WorkspaceLease(
        id="lease-1",
        run_id=run_id,
        lineage_root_run_id="run-1",
        parent_run_id=None,
        attempt=0,
        source_kind="git",
        apply_mode="governed",
        source_root="/source",
        source_repository_id="repo-1",
        source_git_dir="/source/.git",
        source_git_dir_identity="1" * 64,
        base_revision=_SHA,
        staging_root="/staging/lease-1",
        state=WorkspaceLeaseState.STARTING,
        state_version=1,
        created_at=_NOW,
    )


def _restore_point() -> RestorePoint:
    return RestorePoint(
        id="restore-1",
        lease_id="lease-1",
        source_repository_id="repo-1",
        source_root="/source",
        source_git_dir="/source/.git",
        source_git_dir_identity="1" * 64,
        base_revision=_SHA,
        source_head_revision=_SHA,
        source_head_ref="refs/heads/main",
        source_index_tree="b" * 40,
        source_status_hash="c" * 64,
        created_at=_NOW,
    )


def _staging_identity() -> WorkspaceStagingIdentity:
    return WorkspaceStagingIdentity(
        lease_id="lease-1",
        staging_root="/staging/lease-1",
        git_dir="/source/.git/worktrees/lease-1",
        git_dir_identity="2" * 64,
        source_repository_id="repo-1",
        base_revision=_SHA,
        created_at=_NOW,
    )


def test_migration_v5_appends_without_changing_frozen_checksums() -> None:
    assert [(item.version, item.name) for item in MIGRATIONS] == [
        (1, "0001_adopt_v042_baseline"),
        (2, "0002_auth_tokens"),
        (3, "0003_governance_contracts"),
        (4, "0004_workspace_foundation"),
        (5, "0005_governed_apply_bindings"),
    ]
    assert [item.checksum for item in MIGRATIONS[:3]] == [
        _V1_CHECKSUM,
        _V2_CHECKSUM,
        _V3_CHECKSUM,
    ]
    assert MIGRATIONS[3].checksum == _V4_CHECKSUM
    assert MIGRATIONS[4].checksum == _V5_CHECKSUM


@pytest.mark.parametrize("prior_count", [0, 1, 2, 3, 4])
def test_migration_v5_supports_fresh_and_every_frozen_upgrade(prior_count: int) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if prior_count:
        apply_migrations(conn, MIGRATIONS[:prior_count])

    applied = run_migrations(conn)

    assert applied == tuple(range(prior_count + 1, 6))
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "workspace_leases",
        "workspace_lease_states",
        "workspace_staging_identities",
        "restore_points",
        "canonical_change_sets",
        "apply_decisions",
        "apply_receipts",
    } <= tables
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_frozen_v4_callback_rejects_v5_schema_without_v4_ledger() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn, MIGRATIONS)
    conn.execute("DELETE FROM schema_migrations WHERE version >= 4")
    conn.commit()

    with pytest.raises(MigrationExecutionError):
        apply_migrations(conn, MIGRATIONS[:4])

    assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [
        (1,),
        (2,),
        (3,),
    ]
    conn.close()


def test_migration_v5_backfills_and_preserves_v4_apply_decision() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn, MIGRATIONS[:4])
    conn.execute(
        """
        INSERT INTO workspace_leases (
            id, schema_version, run_id, lineage_root_run_id, parent_run_id,
            attempt, source_kind, apply_mode, source_root, source_repository_id,
            source_git_dir, source_git_dir_identity, base_revision, staging_root,
            created_at
        ) VALUES (
            'lease-old', '1', 'run-old', 'run-old', NULL, 0, 'git', 'governed',
            '/source', 'repo-old', '/source/.git', ?, ?, '/staging/lease-old', ?
        )
        """,
        ("1" * 64, _SHA, _NOW.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO workspace_lease_states
            (lease_id, version, state, detail, created_at)
        VALUES ('lease-old', 1, 'starting', NULL, ?)
        """,
        (_NOW.isoformat(),),
    )
    conn.execute(
        """
        INSERT INTO workspace_staging_identities (
            lease_id, schema_version, staging_root, git_dir, git_dir_identity,
            source_repository_id, base_revision, created_at
        ) VALUES (
            'lease-old', '1', '/staging/lease-old',
            '/source/.git/worktrees/lease-old', ?, 'repo-old', ?, ?
        )
        """,
        ("2" * 64, _SHA, _NOW.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO restore_points (
            id, schema_version, lease_id, source_repository_id, source_root,
            source_git_dir, source_git_dir_identity, base_revision,
            source_head_revision, source_head_ref, source_index_tree,
            source_status_hash, canonical_json, content_hash, created_at
        ) VALUES (
            'restore-old', '1', 'lease-old', 'repo-old', '/source',
            '/source/.git', ?, ?, ?, 'refs/heads/main', ?, ?, '{}', ?, ?
        )
        """,
        ("1" * 64, _SHA, _SHA, "b" * 40, "c" * 64, "d" * 64, _NOW.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO canonical_change_sets (
            id, schema_version, lease_id, restore_point_id,
            source_repository_id, base_revision, sequence, canonical_json,
            content_hash, created_at
        ) VALUES (
            'changes-old', '1', 'lease-old', 'restore-old', 'repo-old', ?,
            1, '{}', ?, ?
        )
        """,
        (_SHA, "e" * 64, _NOW.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO apply_decisions (
            id, schema_version, lease_id, restore_point_id, change_set_id,
            change_set_hash, source_repository_id, source_root, base_revision,
            source_head_ref, principal_digest, apply_scope, reason,
            decision_hash, token_hash, expires_at, created_at
        ) VALUES (
            'decision-old', '1', 'lease-old', 'restore-old', 'changes-old', ?,
            'repo-old', '/source', ?, 'refs/heads/main', ?, 'workspace',
            'legacy decision', ?, ?, ?, ?
        )
        """,
        (
            "e" * 64,
            _SHA,
            "f" * 64,
            "0" * 64,
            "9" * 64,
            datetime(2026, 7, 13, tzinfo=UTC).isoformat(),
            _NOW.isoformat(),
        ),
    )
    conn.execute(
        """
        INSERT INTO apply_decision_states
            (decision_id, version, state, receipt_id, created_at)
        VALUES ('decision-old', 1, 'pending', NULL, ?)
        """,
        (_NOW.isoformat(),),
    )
    conn.execute(
        """
        INSERT INTO apply_decision_states
            (decision_id, version, state, receipt_id, created_at)
        VALUES ('decision-old', 2, 'consumed', NULL, ?)
        """,
        (_NOW.isoformat(),),
    )
    conn.execute(
        """
        INSERT INTO apply_receipts (
            id, schema_version, decision_id, decision_hash, lease_id,
            change_set_id, change_set_hash, outcome, detail,
            pre_source_head, pre_source_status_hash, post_source_head,
            post_source_status_hash, rollback_status, failure_code,
            evidence_json, created_at
        ) VALUES (
            'receipt-old', '1', 'decision-old', ?, 'lease-old',
            'changes-old', ?, 'failed', 'legacy receipt',
            ?, ?, ?, ?, 'not_attempted', 'legacy_failure', '[]', ?
        )
        """,
        ("0" * 64, "e" * 64, _SHA, "c" * 64, _SHA, "c" * 64, _NOW.isoformat()),
    )
    conn.execute(
        """
        INSERT INTO apply_decisions (
            id, schema_version, lease_id, restore_point_id, change_set_id,
            change_set_hash, source_repository_id, source_root, base_revision,
            source_head_ref, principal_digest, apply_scope, reason,
            decision_hash, token_hash, expires_at, created_at
        ) VALUES (
            'decision-newer', '1', 'lease-old', 'restore-old', 'changes-old', ?,
            'repo-old', '/source', ?, 'refs/heads/main', ?, 'workspace',
            'newer legacy decision', ?, ?, ?, ?
        )
        """,
        (
            "e" * 64,
            _SHA,
            "f" * 64,
            "3" * 64,
            "4" * 64,
            datetime(2026, 7, 13, tzinfo=UTC).isoformat(),
            datetime(2026, 7, 12, 0, 0, 1, tzinfo=UTC).isoformat(),
        ),
    )
    conn.execute(
        """
        INSERT INTO apply_decision_states
            (decision_id, version, state, receipt_id, created_at)
        VALUES ('decision-newer', 1, 'pending', NULL, ?)
        """,
        (_NOW.isoformat(),),
    )
    conn.commit()

    applied = run_migrations(conn)

    assert applied == (5,)
    decision = conn.execute("SELECT * FROM apply_decisions WHERE id='decision-old'").fetchone()
    assert decision is not None
    assert decision["run_id"] == "run-old"
    assert decision["restore_point_hash"] == "d" * 64
    assert decision["source_git_dir_identity"] == "1" * 64
    assert decision["source_head_revision"] == _SHA
    assert decision["source_index_tree"] == "b" * 40
    assert decision["source_status_hash"] == "c" * 64
    assert decision["staging_root"] == "/staging/lease-old"
    assert decision["staging_git_dir_identity"] == "2" * 64
    assert (
        conn.execute("SELECT COUNT(*) FROM apply_decisions WHERE lease_id='lease-old'").fetchone()[
            0
        ]
        == 2
    )
    repository = WorkspaceMixin()
    repository._conn = conn
    repository._lock = threading.Lock()
    assert repository.has_unreceipted_apply_claim_for_lease("lease-old") is True
    authoritative = repository.get_latest_apply_decision_for_lease("lease-old")
    assert authoritative is not None and authoritative.id == "decision-old"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_workspace_models_are_frozen_strict_and_canonical() -> None:
    lease = _lease()
    with pytest.raises(ValidationError):
        lease.state = WorkspaceLeaseState.ACTIVE  # type: ignore[misc]
    with pytest.raises(ValidationError):
        WorkspaceLease.model_validate({**lease.model_dump(), "unknown": True})

    first = CanonicalChangeSet(
        id="changes-1",
        lease_id=lease.id,
        restore_point_id="restore-1",
        source_repository_id="repo-1",
        base_revision=_SHA,
        sequence=1,
        changes=(
            CanonicalChange(
                kind="modify",
                old_path="z.txt",
                new_path="z.txt",
                old_oid="1" * 40,
                new_oid="2" * 40,
                old_mode="100644",
                new_mode="100644",
                old_size=1,
                new_size=2,
                binary=False,
            ),
            CanonicalChange(
                kind="untracked",
                new_path="a.txt",
                new_oid="3" * 40,
                new_mode="100644",
                new_size=3,
                binary=False,
            ),
        ),
        created_at=_NOW,
    )
    second = first.model_copy(
        update={"id": "changes-2", "created_at": datetime(2027, 1, 1, tzinfo=UTC)}
    )

    assert tuple(change.new_path or change.old_path for change in first.changes) == (
        "a.txt",
        "z.txt",
    )
    assert first.canonical_json() == second.canonical_json()
    assert first.content_hash == second.content_hash
    assert len(first.content_hash) == 64


@pytest.mark.parametrize(
    "change",
    [
        CanonicalChange(
            kind="add",
            old_path=None,
            new_path="new.txt",
            new_oid="2" * 40,
            new_mode="100644",
            new_size=1,
            binary=False,
        ).model_copy(update={"old_path": "ghost.txt"}),
        CanonicalChange(
            kind="delete",
            old_path="old.txt",
            old_oid="1" * 40,
            old_mode="100644",
            old_size=1,
            binary=False,
        ).model_copy(update={"new_path": "ghost.txt"}),
        CanonicalChange(
            kind="modify",
            old_path="same.txt",
            new_path="same.txt",
            old_oid="1" * 40,
            new_oid="2" * 40,
            old_mode="100644",
            new_mode="100644",
            old_size=1,
            new_size=1,
            binary=False,
        ).model_copy(update={"new_oid": "1" * 40}),
        CanonicalChange(
            kind="rename",
            old_path="old.txt",
            new_path="new.txt",
            old_oid="1" * 40,
            new_oid="1" * 40,
            old_mode="100644",
            new_mode="100644",
            old_size=1,
            new_size=1,
            binary=False,
        ).model_copy(update={"new_oid": "2" * 40}),
        CanonicalChange(
            kind="copy",
            old_path="old.txt",
            new_path="new.txt",
            old_oid="1" * 40,
            new_oid="1" * 40,
            old_mode="100644",
            new_mode="100644",
            old_size=1,
            new_size=1,
            binary=False,
        ).model_copy(update={"new_oid": "2" * 40}),
    ],
    ids=("add-ghost-old", "delete-ghost-new", "modify-noop", "rename-content", "copy-content"),
)
def test_canonical_change_rejects_ghost_noop_and_non_exact_move_shapes(
    change: CanonicalChange,
) -> None:
    with pytest.raises(ValidationError):
        CanonicalChange.model_validate(change.model_dump())


@pytest.mark.parametrize("kind", ["mode", "rename", "copy"])
def test_same_object_change_rejects_conflicting_sizes(kind: str) -> None:
    change = CanonicalChange(
        kind=kind,
        old_path="same.txt" if kind == "mode" else "old.txt",
        new_path="same.txt" if kind == "mode" else "new.txt",
        old_oid="1" * 40,
        new_oid="1" * 40,
        old_mode="100644",
        new_mode="100755" if kind == "mode" else "100644",
        old_size=1,
        new_size=1,
        binary=False,
    ).model_copy(update={"new_size": 2})

    with pytest.raises(ValidationError, match="size"):
        CanonicalChange.model_validate(change.model_dump())


def test_canonical_change_set_rejects_duplicate_target_paths() -> None:
    first = CanonicalChange(
        kind="add",
        new_path="same.txt",
        new_oid="1" * 40,
        new_mode="100644",
        new_size=1,
        binary=False,
    )
    second = CanonicalChange(
        kind="untracked",
        new_path="same.txt",
        new_oid="2" * 40,
        new_mode="100644",
        new_size=2,
        binary=False,
    )

    with pytest.raises(ValidationError, match="target"):
        CanonicalChangeSet(
            id="changes-duplicate-target",
            lease_id="lease-1",
            restore_point_id="restore-1",
            source_repository_id="repo-1",
            base_revision=_SHA,
            sequence=1,
            changes=(first, second),
            created_at=_NOW,
        )


@pytest.mark.parametrize(
    "updates",
    [
        {"lineage_root_run_id": "another-root"},
        {"parent_run_id": "parent", "attempt": 0},
        {"parent_run_id": None, "attempt": 1},
    ],
    ids=("root-mismatch", "root-has-parent", "retry-missing-parent"),
)
def test_workspace_lease_rejects_invalid_retry_lineage(updates: dict[str, object]) -> None:
    invalid = _lease().model_copy(update=updates)

    with pytest.raises(ValidationError):
        WorkspaceLease.model_validate(invalid.model_dump())


def test_workspace_repository_roundtrips_and_uses_cas_state_versions(storage) -> None:
    lease = _lease()
    restore = _restore_point()

    storage.create_workspace_foundation(lease, restore)
    storage.save_workspace_staging_identity(_staging_identity())
    active = storage.transition_workspace_lease(
        lease.id,
        expected_version=1,
        expected_state=WorkspaceLeaseState.STARTING,
        new_state=WorkspaceLeaseState.ACTIVE,
        created_at=_NOW,
    )

    assert active.state is WorkspaceLeaseState.ACTIVE
    assert active.state_version == 2
    assert storage.get_workspace_lease(lease.id) == active
    assert storage.get_workspace_lease_by_run("run-1") == active
    assert storage.get_restore_point(restore.id) == restore
    with pytest.raises(WorkspaceStateConflict):
        storage.transition_workspace_lease(
            lease.id,
            expected_version=1,
            expected_state=WorkspaceLeaseState.STARTING,
            new_state=WorkspaceLeaseState.ACTIVE,
            created_at=_NOW,
        )


def test_workspace_repository_persists_changes_and_apply_foundation(storage) -> None:
    lease = _lease()
    restore = _restore_point()
    storage.create_workspace_foundation(lease, restore)
    storage.save_workspace_staging_identity(_staging_identity())
    storage.transition_workspace_lease(
        lease.id,
        expected_version=lease.state_version,
        expected_state=WorkspaceLeaseState.STARTING,
        new_state=WorkspaceLeaseState.ACTIVE,
        created_at=_NOW,
    )
    change_set = CanonicalChangeSet(
        id="changes-1",
        lease_id=lease.id,
        restore_point_id=restore.id,
        source_repository_id="repo-1",
        base_revision=_SHA,
        sequence=1,
        changes=(),
        created_at=_NOW,
    )
    decision = ApplyDecision(
        id="decision-1",
        run_id=lease.run_id,
        lease_id=lease.id,
        restore_point_id=restore.id,
        restore_point_hash=restore.content_hash,
        change_set_id=change_set.id,
        change_set_hash=change_set.content_hash,
        source_repository_id="repo-1",
        source_root="/source",
        source_git_dir_identity=restore.source_git_dir_identity,
        base_revision=_SHA,
        source_head_revision=restore.source_head_revision,
        source_head_ref="refs/heads/main",
        source_index_tree=restore.source_index_tree,
        source_status_hash=restore.source_status_hash,
        staging_root=lease.staging_root,
        staging_git_dir_identity=_staging_identity().git_dir_identity,
        principal_digest="d" * 64,
        reason="reviewed",
        decision_hash="e" * 64,
        token_hash="f" * 64,
        expires_at=datetime(2026, 7, 13, tzinfo=UTC),
        created_at=_NOW,
    )
    receipt = ApplyReceipt(
        id="receipt-1",
        decision_id=decision.id,
        decision_hash=decision.decision_hash,
        lease_id=lease.id,
        change_set_id=change_set.id,
        change_set_hash=change_set.content_hash,
        outcome="succeeded",
        detail="foundation only",
        pre_source_head=_SHA,
        pre_source_status_hash="c" * 64,
        post_source_head=_SHA,
        post_source_status_hash="c" * 64,
        rollback_status="not_required",
        created_at=_NOW,
    )

    stored = storage.save_canonical_change_set(change_set)
    storage.save_apply_decision(decision)
    assert storage.get_apply_decision(decision.id) == decision
    with pytest.raises(WorkspaceStateConflict, match="already issued"):
        storage.save_apply_decision(
            decision.model_copy(
                update={
                    "id": "decision-duplicate",
                    "decision_hash": "1" * 64,
                    "token_hash": "2" * 64,
                }
            )
        )
    with pytest.raises(WorkspaceStateConflict):
        storage.transition_apply_decision(
            decision.id,
            expected_version=1,
            new_state="consumed",
            created_at=_NOW,
        )
    with pytest.raises(WorkspaceStateConflict):
        storage.transition_apply_decision(
            decision.id,
            expected_version=1,
            new_state="expired",
            receipt_id=receipt.id,
            created_at=_NOW,
        )
    with pytest.raises(WorkspaceStateConflict):
        storage.claim_apply_decision(
            decision.id,
            expected_version=1,
            receipt_id=receipt.id,
            created_at=decision.expires_at,
        )
    with pytest.raises(sqlite3.IntegrityError, match="binding mismatch"):
        storage.save_apply_receipt(receipt)
    consumed = storage.transition_apply_decision(
        decision.id,
        expected_version=1,
        new_state="consumed",
        receipt_id=receipt.id,
        created_at=_NOW,
    )
    storage.save_apply_receipt(receipt)

    assert stored == change_set
    assert storage.get_canonical_change_set(change_set.id) == change_set
    assert storage.get_apply_receipt(receipt.id) == receipt
    assert consumed.state == "consumed"
    assert consumed.state_version == 2
    with pytest.raises(WorkspaceStateConflict):
        storage.transition_apply_decision(
            decision.id,
            expected_version=1,
            new_state="consumed",
            receipt_id=receipt.id,
            created_at=_NOW,
        )
    with pytest.raises(sqlite3.IntegrityError):
        storage.create_workspace_foundation(_lease(run_id="run-1"), restore)


def test_workspace_repository_preserves_change_set_chronology(storage) -> None:
    lease = _lease()
    restore = _restore_point()
    storage.create_workspace_foundation(lease, restore)
    first = CanonicalChangeSet(
        id="changes-1",
        lease_id=lease.id,
        restore_point_id=restore.id,
        source_repository_id="repo-1",
        base_revision=_SHA,
        sequence=1,
        changes=(),
        created_at=_NOW,
    )
    second = first.model_copy(
        update={
            "id": "changes-2",
            "sequence": 2,
            "changes": (
                CanonicalChange(
                    kind="modify",
                    old_path="file.txt",
                    new_path="file.txt",
                    old_oid="1" * 40,
                    new_oid="2" * 40,
                    old_mode="100644",
                    new_mode="100644",
                    old_size=1,
                    new_size=2,
                    binary=False,
                ),
            ),
        }
    )
    third = first.model_copy(update={"id": "changes-3", "sequence": 3})
    repeated = third.model_copy(update={"id": "changes-4", "sequence": 4})

    assert storage.save_canonical_change_set(first) == first
    assert storage.save_canonical_change_set(second) == second
    assert storage.save_canonical_change_set(third) == third
    assert storage.save_canonical_change_set(repeated) == third
    assert storage.get_latest_canonical_change_set_for_lease(lease.id) == third


def test_workspace_records_and_apply_bindings_are_database_immutable(storage) -> None:
    lease = _lease()
    restore = _restore_point()
    storage.create_workspace_foundation(lease, restore)
    storage.save_workspace_staging_identity(_staging_identity())
    with (
        pytest.raises(sqlite3.IntegrityError, match="immutable"),
        storage._lock,  # noqa: SLF001 - prove database invariant
        storage._conn,  # noqa: SLF001
    ):
        storage._conn.execute(  # noqa: SLF001
            "UPDATE workspace_leases SET run_id='replayed' WHERE id=?", (lease.id,)
        )

    change_set = CanonicalChangeSet(
        id="changes-1",
        lease_id=lease.id,
        restore_point_id=restore.id,
        source_repository_id="repo-1",
        base_revision=_SHA,
        sequence=1,
        changes=(),
        created_at=_NOW,
    )
    storage.save_canonical_change_set(change_set)
    mismatched = ApplyDecision(
        id="decision-bad",
        run_id=lease.run_id,
        lease_id=lease.id,
        restore_point_id=restore.id,
        restore_point_hash=restore.content_hash,
        change_set_id=change_set.id,
        change_set_hash="0" * 64,
        source_repository_id="repo-1",
        source_root="/source",
        source_git_dir_identity=restore.source_git_dir_identity,
        base_revision=_SHA,
        source_head_revision=restore.source_head_revision,
        source_head_ref="refs/heads/main",
        source_index_tree=restore.source_index_tree,
        source_status_hash=restore.source_status_hash,
        staging_root=lease.staging_root,
        staging_git_dir_identity=_staging_identity().git_dir_identity,
        principal_digest="d" * 64,
        reason="mismatch",
        decision_hash="e" * 64,
        token_hash="f" * 64,
        expires_at=datetime(2026, 7, 13, tzinfo=UTC),
        created_at=_NOW,
    )
    with pytest.raises(sqlite3.IntegrityError, match="binding mismatch"):
        storage.save_apply_decision(mismatched)


def test_canonical_change_set_database_binding_rejects_spoofed_source_and_base(storage) -> None:
    lease = _lease()
    restore = _restore_point()
    storage.create_workspace_foundation(lease, restore)
    spoofed = CanonicalChangeSet(
        id="changes-spoofed",
        lease_id=lease.id,
        restore_point_id=restore.id,
        source_repository_id="repo-spoofed",
        base_revision="d" * 40,
        sequence=1,
        changes=(),
        created_at=_NOW,
    )

    with pytest.raises(sqlite3.IntegrityError, match="binding mismatch"):
        storage.save_canonical_change_set(spoofed)


def test_apply_decision_database_binding_rejects_source_ref_drift(storage) -> None:
    lease = _lease()
    restore = _restore_point()
    storage.create_workspace_foundation(lease, restore)
    storage.save_workspace_staging_identity(_staging_identity())
    change_set = CanonicalChangeSet(
        id="changes-1",
        lease_id=lease.id,
        restore_point_id=restore.id,
        source_repository_id="repo-1",
        base_revision=_SHA,
        sequence=1,
        changes=(),
        created_at=_NOW,
    )
    storage.save_canonical_change_set(change_set)
    decision = ApplyDecision(
        id="decision-ref-drift",
        run_id=lease.run_id,
        lease_id=lease.id,
        restore_point_id=restore.id,
        restore_point_hash=restore.content_hash,
        change_set_id=change_set.id,
        change_set_hash=change_set.content_hash,
        source_repository_id="repo-1",
        source_root="/source",
        source_git_dir_identity=restore.source_git_dir_identity,
        base_revision=_SHA,
        source_head_revision=restore.source_head_revision,
        source_head_ref="refs/heads/other",
        source_index_tree=restore.source_index_tree,
        source_status_hash=restore.source_status_hash,
        staging_root=lease.staging_root,
        staging_git_dir_identity=_staging_identity().git_dir_identity,
        principal_digest="d" * 64,
        reason="wrong source ref",
        decision_hash="e" * 64,
        token_hash="f" * 64,
        expires_at=datetime(2026, 7, 13, tzinfo=UTC),
        created_at=_NOW,
    )

    with pytest.raises(sqlite3.IntegrityError, match="binding mismatch"):
        storage.save_apply_decision(decision)


def test_database_rejects_noncontiguous_workspace_lineage(storage) -> None:
    storage.create_workspace_foundation(_lease(), _restore_point())
    invalid_retry = _lease().model_copy(
        update={
            "id": "lease-2",
            "run_id": "run-2",
            "parent_run_id": "run-1",
            "attempt": 2,
            "staging_root": "/staging/lease-2",
        }
    )
    invalid_restore = _restore_point().model_copy(update={"id": "restore-2", "lease_id": "lease-2"})

    with pytest.raises(sqlite3.IntegrityError, match="lineage mismatch"):
        storage.create_workspace_foundation(invalid_retry, invalid_restore)


def test_workspace_transition_returns_exact_inserted_version_under_competing_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "workspace-transition.sqlite")
    first = Storage(db_path)
    second = Storage(db_path)
    first.init_db()
    second.init_db()
    lease = _lease()
    first.create_workspace_foundation(lease, _restore_point())
    first.save_workspace_staging_identity(_staging_identity())
    active = first.transition_workspace_lease(
        lease.id,
        expected_version=1,
        expected_state=WorkspaceLeaseState.STARTING,
        new_state=WorkspaceLeaseState.ACTIVE,
        created_at=_NOW,
    )
    original_get = first.get_workspace_lease
    competed = False

    def get_after_competing_close(lease_id: str):
        nonlocal competed
        if not competed:
            competed = True
            second.transition_workspace_lease(
                lease_id,
                expected_version=3,
                expected_state=WorkspaceLeaseState.CLOSING,
                new_state=WorkspaceLeaseState.CLOSED,
                created_at=_NOW,
            )
        return original_get(lease_id)

    monkeypatch.setattr(first, "get_workspace_lease", get_after_competing_close)
    returned = first.transition_workspace_lease(
        lease.id,
        expected_version=active.state_version,
        expected_state=WorkspaceLeaseState.ACTIVE,
        new_state=WorkspaceLeaseState.CLOSING,
        created_at=_NOW,
    )
    if not competed:
        second.transition_workspace_lease(
            lease.id,
            expected_version=3,
            expected_state=WorkspaceLeaseState.CLOSING,
            new_state=WorkspaceLeaseState.CLOSED,
            created_at=_NOW,
        )

    assert returned.state is WorkspaceLeaseState.CLOSING
    assert returned.state_version == 3
    assert second.get_workspace_lease(lease.id).state is WorkspaceLeaseState.CLOSED
    first.close()
    second.close()


def test_apply_transition_does_not_reread_after_committing_inserted_version(
    storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease = _lease()
    restore = _restore_point()
    storage.create_workspace_foundation(lease, restore)
    storage.save_workspace_staging_identity(_staging_identity())
    change_set = CanonicalChangeSet(
        id="changes-1",
        lease_id=lease.id,
        restore_point_id=restore.id,
        source_repository_id="repo-1",
        base_revision=_SHA,
        sequence=1,
        changes=(),
        created_at=_NOW,
    )
    storage.save_canonical_change_set(change_set)
    decision = ApplyDecision(
        id="decision-1",
        run_id=lease.run_id,
        lease_id=lease.id,
        restore_point_id=restore.id,
        restore_point_hash=restore.content_hash,
        change_set_id=change_set.id,
        change_set_hash=change_set.content_hash,
        source_repository_id="repo-1",
        source_root="/source",
        source_git_dir_identity=restore.source_git_dir_identity,
        base_revision=_SHA,
        source_head_revision=restore.source_head_revision,
        source_head_ref="refs/heads/main",
        source_index_tree=restore.source_index_tree,
        source_status_hash=restore.source_status_hash,
        staging_root=lease.staging_root,
        staging_git_dir_identity=_staging_identity().git_dir_identity,
        principal_digest="d" * 64,
        reason="exact transition",
        decision_hash="e" * 64,
        token_hash="f" * 64,
        expires_at=datetime(2026, 7, 13, tzinfo=UTC),
        created_at=_NOW,
    )
    storage.save_apply_decision(decision)

    def reject_post_commit_reread(_decision_id: str):
        raise AssertionError("transition must return the row read inside its write transaction")

    monkeypatch.setattr(storage, "get_apply_decision", reject_post_commit_reread)

    returned = storage.transition_apply_decision(
        decision.id,
        expected_version=1,
        new_state="revoked",
        created_at=_NOW,
    )

    assert returned.state == "revoked"
    assert returned.state_version == 2
