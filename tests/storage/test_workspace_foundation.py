"""G1.4 workspace domain and append-only persistence foundation."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

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
)
from tianshu.storage.migration_ledger import apply_migrations
from tianshu.storage.migrations import MIGRATIONS, run_migrations
from tianshu.storage.workspace_repo import WorkspaceStateConflict

_V1_CHECKSUM = "9672603c12dd858ea714b291d6ed94f1a27cb373bfcff97665b6316b4aa552a6"
_V2_CHECKSUM = "a2bbf753e0c3244fccc86be2d4588af2c926399f6dfa0dba0af5d0c060179c5a"
_V3_CHECKSUM = "07cb59c354035674fbcabcf1a037b4b273ae43b4e1e4dd8427cf90361bff2ff8"
_V4_CHECKSUM = "001d79202ec391642f4d8cfac0634902760b2c229d20266260ffc2940405b9fa"
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
        base_revision=_SHA,
        source_head_revision=_SHA,
        source_index_tree="b" * 40,
        source_status_hash="c" * 64,
        created_at=_NOW,
    )


def test_migration_v4_appends_without_changing_frozen_checksums() -> None:
    assert [(item.version, item.name) for item in MIGRATIONS] == [
        (1, "0001_adopt_v042_baseline"),
        (2, "0002_auth_tokens"),
        (3, "0003_governance_contracts"),
        (4, "0004_workspace_foundation"),
    ]
    assert [item.checksum for item in MIGRATIONS[:3]] == [
        _V1_CHECKSUM,
        _V2_CHECKSUM,
        _V3_CHECKSUM,
    ]
    assert MIGRATIONS[3].checksum == _V4_CHECKSUM


@pytest.mark.parametrize("prior_count", [0, 1, 2, 3])
def test_migration_v4_supports_fresh_and_every_frozen_upgrade(prior_count: int) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if prior_count:
        apply_migrations(conn, MIGRATIONS[:prior_count])

    applied = run_migrations(conn)

    assert applied == tuple(range(prior_count + 1, 5))
    tables = {
        row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "workspace_leases",
        "workspace_lease_states",
        "restore_points",
        "canonical_change_sets",
        "apply_decisions",
        "apply_receipts",
    } <= tables
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


def test_workspace_repository_roundtrips_and_uses_cas_state_versions(storage) -> None:
    lease = _lease()
    restore = _restore_point()

    storage.create_workspace_foundation(lease, restore)
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
        lease_id=lease.id,
        restore_point_id=restore.id,
        change_set_id=change_set.id,
        change_set_hash=change_set.content_hash,
        source_repository_id="repo-1",
        source_root="/source",
        base_revision=_SHA,
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
    storage.save_apply_receipt(receipt)

    assert stored == change_set
    assert storage.get_canonical_change_set(change_set.id) == change_set
    assert storage.get_apply_decision(decision.id) == decision
    assert storage.get_apply_receipt(receipt.id) == receipt
    consumed = storage.transition_apply_decision(
        decision.id,
        expected_version=1,
        new_state="consumed",
        receipt_id=receipt.id,
        created_at=_NOW,
    )
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


def test_workspace_records_and_apply_bindings_are_database_immutable(storage) -> None:
    lease = _lease()
    restore = _restore_point()
    storage.create_workspace_foundation(lease, restore)
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
        lease_id=lease.id,
        restore_point_id=restore.id,
        change_set_id=change_set.id,
        change_set_hash="0" * 64,
        source_repository_id="repo-1",
        source_root="/source",
        base_revision=_SHA,
        principal_digest="d" * 64,
        reason="mismatch",
        decision_hash="e" * 64,
        token_hash="f" * 64,
        expires_at=datetime(2026, 7, 13, tzinfo=UTC),
        created_at=_NOW,
    )
    with pytest.raises(sqlite3.IntegrityError, match="binding mismatch"):
        storage.save_apply_decision(mismatched)
