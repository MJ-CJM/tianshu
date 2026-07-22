"""Root-anchored governed apply transaction regressions."""

import os
from pathlib import Path

import pytest

from tianshu.executor.anchored_fs import AnchoredFilesystemError, AnchoredRoot
from tianshu.executor.git_backend import GitBackend, GitWorktreeEntry
from tianshu.executor.workspace_apply import (
    ApplyPlan,
    SourceBackup,
    WorkspaceApplyEngine,
)
from tianshu.models.workspace import CanonicalChange


def test_restore_retries_after_owned_postimage_was_unlinked(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "tracked.txt"
    target.write_bytes(b"before\n")
    anchored = AnchoredRoot(root, byte_limit=1_000_000)
    preimage = anchored.capture("tracked.txt")
    root_entry = anchored.capture_directory(".")
    root_device = anchored.device
    root_inode = anchored.inode
    target.write_bytes(b"owned-after\n")
    postimage = anchored.capture("tracked.txt")
    backup = SourceBackup(
        root=root,
        root_device=root_device,
        root_inode=root_inode,
        filesystem=anchored,
        entries={"tracked.txt": preimage},
        parents={".": root_entry},
        owned_postimages={"tracked.txt": postimage},
        mutated_paths={"tracked.txt"},
        mutation_started=True,
    )

    def fail_after_remove(stage: str) -> None:
        if stage == "after_restore_remove":
            raise RuntimeError("injected after owned postimage unlink")

    with pytest.raises(RuntimeError, match="after owned postimage unlink"):
        WorkspaceApplyEngine(GitBackend(), fail_after_remove).restore(backup)

    assert not target.exists()
    WorkspaceApplyEngine(GitBackend()).restore(backup)
    assert target.read_bytes() == b"before\n"
    assert not backup.rollback_removed_paths
    WorkspaceApplyEngine.release(backup)


def test_modify_write_failure_restores_prior_owned_missing_state_and_retries(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "tracked.txt"
    target.write_bytes(b"before\n")
    change = CanonicalChange(
        kind="modify",
        old_path="tracked.txt",
        new_path="tracked.txt",
        old_oid="a" * 40,
        new_oid="b" * 40,
        old_mode="100644",
        new_mode="100644",
        old_size=7,
        new_size=6,
        binary=False,
    )
    plan = ApplyPlan(
        changes=(change,),
        desired={
            "tracked.txt": GitWorktreeEntry(
                mode="100644",
                oid="b" * 40,
                size=6,
                content=b"after\n",
            )
        },
        removals=frozenset(),
        affected=("tracked.txt",),
    )
    engine = WorkspaceApplyEngine(GitBackend())
    backup = engine.backup(root, plan)

    def fail_before_write_leaf(stage: str) -> None:
        if stage == "after_write_parent_open_before_leaf":
            raise RuntimeError("injected before write leaf")

    with pytest.raises(RuntimeError, match="injected before write leaf"):
        WorkspaceApplyEngine(GitBackend(), fail_before_write_leaf).materialize(backup, plan)

    assert not target.exists()
    assert backup.pending_paths == {"tracked.txt"}
    assert backup.mutated_paths == {"tracked.txt"}
    WorkspaceApplyEngine(GitBackend()).restore(backup)
    assert target.read_bytes() == b"before\n"
    WorkspaceApplyEngine(GitBackend()).restore(backup)
    assert target.read_bytes() == b"before\n"
    WorkspaceApplyEngine.release(backup)


def test_published_root_swap_is_rejected_without_touching_replacement(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    anchored = AnchoredRoot(root, byte_limit=1_000_000)
    displaced = tmp_path / "displaced"
    root.rename(displaced)
    root.mkdir()
    replacement = root / "replacement.txt"
    replacement.write_bytes(b"external\n")

    with pytest.raises(AnchoredFilesystemError, match="published workspace root"):
        anchored.assert_published_root()

    assert replacement.read_bytes() == b"external\n"
    anchored.close()


def test_regular_publish_fails_closed_when_target_appears_before_link(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "tracked.txt"

    def publish_race(stage: str, relative_path: str) -> None:
        if stage == "after_write_absence_check_before_publish":
            assert relative_path == "tracked.txt"
            target.write_bytes(b"external\n")

    with (
        AnchoredRoot(root, byte_limit=1_000_000, operation_hook=publish_race) as anchored,
        pytest.raises(AnchoredFilesystemError, match="regular write failed"),
    ):
        anchored.write_regular("tracked.txt", b"approved\n", 0o644)

    assert target.read_bytes() == b"external\n"


def test_regular_publish_never_claims_replacement_after_link(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "tracked.txt"
    external_inode: int | None = None

    def publish_race(stage: str, relative_path: str) -> None:
        nonlocal external_inode
        if stage == "after_regular_publish_before_identity_check":
            assert relative_path == "tracked.txt"
            target.unlink()
            target.write_bytes(b"approved\n")
            external_inode = target.stat().st_ino

    with (
        AnchoredRoot(root, byte_limit=1_000_000, operation_hook=publish_race) as anchored,
        pytest.raises(AnchoredFilesystemError, match="published file identity changed"),
    ):
        anchored.write_regular("tracked.txt", b"approved\n", 0o644)

    assert target.read_bytes() == b"approved\n"
    assert target.stat().st_ino == external_inode


def test_symlink_publish_never_claims_replacement_after_link(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    target = root / "tracked-link"
    external_inode: int | None = None

    def publish_race(stage: str, relative_path: str) -> None:
        nonlocal external_inode
        if stage == "after_symlink_publish_before_identity_check":
            assert relative_path == "tracked-link"
            target.unlink()
            target.symlink_to("approved-target")
            external_inode = target.lstat().st_ino

    with (
        AnchoredRoot(root, byte_limit=1_000_000, operation_hook=publish_race) as anchored,
        pytest.raises(AnchoredFilesystemError, match="published symlink identity changed"),
    ):
        anchored.write_symlink("tracked-link", b"approved-target")

    assert target.readlink() == Path("approved-target")
    assert target.lstat().st_ino == external_inode


@pytest.mark.parametrize(
    ("mode", "content", "stage"),
    [
        ("100644", b"approved\n", "after_regular_publish_before_identity_check"),
        ("120000", b"approved-target", "after_symlink_publish_before_identity_check"),
    ],
)
def test_post_link_failure_recovers_transaction_owned_object(
    tmp_path: Path,
    mode: str,
    content: bytes,
    stage: str,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    change = CanonicalChange(
        kind="add",
        new_path="added",
        new_oid="b" * 40,
        new_mode=mode,
        new_size=len(content),
        binary=False,
    )
    plan = ApplyPlan(
        changes=(change,),
        desired={
            "added": GitWorktreeEntry(
                mode=mode,
                oid="b" * 40,
                size=len(content),
                content=content,
            )
        },
        removals=frozenset(),
        affected=("added",),
    )

    def fail_after_link(current_stage: str) -> None:
        if current_stage == stage:
            raise RuntimeError("injected immediately after publish")

    engine = WorkspaceApplyEngine(GitBackend(), fail_after_link)
    backup = engine.backup(root, plan)

    with pytest.raises(RuntimeError, match="injected immediately after publish"):
        engine.materialize(backup, plan)

    assert os.path.lexists(root / "added")
    WorkspaceApplyEngine(GitBackend()).restore(backup)
    assert not os.path.lexists(root / "added")
    assert not backup.pending_paths
    WorkspaceApplyEngine.release(backup)
