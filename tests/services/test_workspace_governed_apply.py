"""Governed workspace apply is a single-use, source-safe authority."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.executor.capabilities import (
    CapabilityState,
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.git_backend import GitBackend
from tianshu.executor.workspace_service import (
    WorkspaceApplyError,
    WorkspaceConflict,
    WorkspaceLeaseRequest,
    WorkspaceService,
)
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.governance_contract import (
    LegacyEdictGovernanceMapper,
    RecoveryPolicyV1,
    WorkspacePolicyV1,
)
from tianshu.models.principal import AuthContext, Principal, PrincipalKind
from tianshu.models.workspace import WorkspaceLeaseState

_GIT = shutil.which("git")


def _git(repo: Path, *args: str) -> bytes:
    assert _GIT is not None
    return subprocess.run([_GIT, *args], cwd=repo, check=True, capture_output=True).stdout


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Fixture")
    _git(path, "config", "user.email", "fixture@example.invalid")
    for relative, content in {
        "modify.txt": b"before\n",
        "delete.txt": b"delete\n",
        "rename.txt": b"rename\n",
        "copy.txt": b"copy\n",
        "mode.txt": b"mode\n",
        "binary.bin": b"old\x00binary",
        "nested/file.txt": b"nested\n",
    }.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    if hasattr(os, "symlink"):
        (path / "link").symlink_to("modify.txt")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "base")
    return path


def _effective(base: str, *, enforce_apply: bool):
    requested = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="governed apply"),
        default_workspace_id="workspace-main",
    )
    manifest = native_manifest()
    manifest = manifest.model_copy(
        update={
            "capabilities": tuple(
                declaration.model_copy(
                    update={
                        "state": (
                            CapabilityState.ENFORCED
                            if enforce_apply
                            else CapabilityState.UNSUPPORTED
                        ),
                        "evidence": ("test:governed-apply-fixture",),
                    }
                )
                if declaration.capability == "governed_apply_merge"
                else declaration
                for declaration in manifest.capabilities
            )
        }
    )
    effective = resolve_governance_contract(
        requested,
        manifest,
        probe_host_capabilities(),
    )
    return effective.model_copy(
        update={
            "workspace": WorkspacePolicyV1(
                source_id="workspace-main",
                base_revision=base,
                staging_mode="isolated",
                apply_mode="governed",
                require_clean_source=True,
            ),
            "recovery": RecoveryPolicyV1(require_restore_point=True),
            "resolved_source_id": "workspace-main",
            "resolved_base_revision": base,
        }
    )


@dataclass(frozen=True)
class _Prepared:
    source: Path
    staging: Path
    service: WorkspaceService
    run_id: str
    lease_id: str
    source_head: bytes
    source_ref: bytes
    source_refs: bytes
    source_index: bytes


async def _prepared(
    storage,
    tmp_path: Path,
    *,
    enforce_apply: bool = True,
) -> _Prepared:
    source = _repository(tmp_path / "source")
    base = _git(source, "rev-parse", "HEAD").decode().strip()
    edict = Edict(goal="apply the reviewed workspace")
    storage.save_edict(edict)
    memorial = Memorial(
        edict_id=edict.id,
        instruction="apply",
        status=TaskStatus.COMPLETED,
        effective_governance_contract=_effective(base, enforce_apply=enforce_apply),
    )
    storage.save_memorial(memorial)
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    lease = await service.create_lease(
        WorkspaceLeaseRequest(
            run_id=memorial.id,
            lineage_root_run_id=memorial.id,
            source_root=source,
            base_revision=base,
            apply_mode="governed",
        )
    )
    return _Prepared(
        source=source,
        staging=Path(lease.staging_root),
        service=service,
        run_id=memorial.id,
        lease_id=lease.id,
        source_head=_git(source, "rev-parse", "HEAD"),
        source_ref=_git(source, "symbolic-ref", "HEAD"),
        source_refs=_git(source, "for-each-ref", "--format=%(refname)%00%(objectname)"),
        source_index=(source / ".git" / "index").read_bytes(),
    )


def _principal(identifier: str = "reviewer") -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=identifier,
            kind=PrincipalKind.HUMAN,
            display_name="Workspace Reviewer",
            scopes=frozenset({"workspace:apply", "tasks:read"}),
        ),
        source="trusted-local",
        client_kind="system",
        correlation_id=f"workspace-test:{identifier}",
    )


def _unscoped_principal() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="reviewer",
            kind=PrincipalKind.HUMAN,
            display_name="Workspace Reviewer",
            scopes=frozenset({"tasks:read"}),
        ),
        source="trusted-local",
        client_kind="system",
        correlation_id="workspace-test:unscoped",
    )


def _source_authority(prepared: _Prepared) -> tuple[bytes, bytes, bytes, bytes]:
    return (
        _git(prepared.source, "rev-parse", "HEAD"),
        _git(prepared.source, "symbolic-ref", "HEAD"),
        _git(prepared.source, "for-each-ref", "--format=%(refname)%00%(objectname)"),
        (prepared.source / ".git" / "index").read_bytes(),
    )


def _loose_objects(repo: Path) -> set[str]:
    objects = repo / ".git" / "objects"
    return {
        path.relative_to(objects).as_posix()
        for path in objects.rglob("*")
        if path.is_file() and len(path.parent.name) == 2
    }


def _object_database_snapshot(repo: Path) -> tuple[tuple[str, bytes], ...]:
    objects = repo / ".git" / "objects"
    return tuple(
        (path.relative_to(objects).as_posix(), path.read_bytes())
        for path in sorted(
            (candidate for candidate in objects.rglob("*") if candidate.is_file()),
            key=lambda candidate: os.fsencode(candidate.relative_to(objects)),
        )
    )


def _filesystem_snapshot(root: Path) -> tuple[tuple[str, int, bytes], ...]:
    entries: list[tuple[str, int, bytes]] = []
    for path in sorted(
        (
            candidate
            for candidate in root.rglob("*")
            if ".git" not in candidate.relative_to(root).parts
        ),
        key=lambda candidate: os.fsencode(candidate.relative_to(root)),
    ):
        if path.is_symlink():
            payload = os.fsencode(os.readlink(path))
        elif path.is_file():
            payload = path.read_bytes()
        else:
            payload = b""
        entries.append((path.relative_to(root).as_posix(), path.lstat().st_mode, payload))
    return tuple(entries)


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_decision_binds_persisted_generic_authority_and_uses_canonical_compat_token(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    changes = await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)

    status = await prepared.service.get_run_status(prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id,
        _principal(),
        "reviewed exact canonical changes",
        timedelta(minutes=5),
    )

    assert status.lease.id == prepared.lease_id
    assert status.change_set == changes
    assert (
        status.effective_contract_hash
        == storage.get_effective_governance_contract(prepared.run_id).content_hash
    )
    assert decision.run_id == prepared.run_id
    assert decision.change_set_hash == changes.content_hash
    assert decision.restore_point_hash == status.restore_point.content_hash
    assert decision.source_head_revision == status.restore_point.source_head_revision
    assert decision.source_index_tree == status.restore_point.source_index_tree
    assert decision.source_status_hash == status.restore_point.source_status_hash
    assert decision.staging_git_dir_identity == status.lease.staging_git_dir_identity
    assert token == decision.id == decision.decision_request_id
    assert decision.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token in repr(decision)
    with storage._lock:  # noqa: SLF001 - prove the one-time secret is absent at rest
        dump = " ".join(
            str(value)
            for row in storage._conn.iterdump()  # noqa: SLF001
            for value in (row,)
        )
    assert token in dump
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_apply_materializes_exact_canonical_set_without_git_authority_mutation(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"after\n")
    (prepared.staging / "delete.txt").unlink()
    (prepared.staging / "rename.txt").rename(prepared.staging / "renamed.txt")
    shutil.copyfile(prepared.staging / "copy.txt", prepared.staging / "copied.txt")
    (prepared.staging / "mode.txt").chmod(0o755)
    (prepared.staging / "binary.bin").write_bytes(b"new\x00binary\xff")
    (prepared.staging / "added.txt").write_bytes(b"added\n")
    if (prepared.staging / "link").is_symlink():
        (prepared.staging / "link").unlink()
        (prepared.staging / "link").symlink_to("mode.txt")
    changes = await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    objects_before = _loose_objects(prepared.source)

    receipt = await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert receipt.outcome == "succeeded"
    assert receipt.change_set_hash == changes.content_hash
    assert (prepared.source / "modify.txt").read_bytes() == b"after\n"
    assert not (prepared.source / "delete.txt").exists()
    assert not (prepared.source / "rename.txt").exists()
    assert (prepared.source / "renamed.txt").read_bytes() == b"rename\n"
    assert (prepared.source / "copied.txt").read_bytes() == b"copy\n"
    assert (prepared.source / "mode.txt").stat().st_mode & 0o111
    assert (prepared.source / "binary.bin").read_bytes() == b"new\x00binary\xff"
    assert (prepared.source / "added.txt").read_bytes() == b"added\n"
    if hasattr(os, "symlink"):
        assert (prepared.source / "link").is_symlink()
        assert os.readlink(prepared.source / "link") == "mode.txt"
    assert _source_authority(prepared) == (
        prepared.source_head,
        prepared.source_ref,
        prepared.source_refs,
        prepared.source_index,
    )
    assert _loose_objects(prepared.source) == objects_before
    assert storage.get_apply_receipt_for_decision(decision.id) == receipt
    assert storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLOSED
    assert not prepared.staging.exists()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["wrong-token", "wrong-principal", "wrong-run"])
async def test_wrong_credentials_fail_closed_without_claim_or_source_mutation(
    storage, tmp_path: Path, credential: str
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"after\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    source_before = (prepared.source / "modify.txt").read_bytes()

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(
            "another-run" if credential == "wrong-run" else prepared.run_id,
            decision.id,
            "invalid" if credential == "wrong-token" else token,
            _principal("intruder") if credential == "wrong-principal" else _principal(),
        )

    assert caught.value.code in {"token_invalid", "principal_mismatch", "binding_mismatch"}
    assert (prepared.source / "modify.txt").read_bytes() == source_before
    assert storage.get_apply_decision(decision.id).state == "pending"
    assert storage.get_apply_receipt_for_decision(decision.id) is None
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_staging_change_after_decision_is_rejected_as_stale(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    (prepared.staging / "modify.txt").write_bytes(b"changed after approval\n")

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "change_set_stale"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    assert storage.get_apply_decision(decision.id).state == "pending"
    assert storage.get_apply_receipt_for_decision(decision.id) is None
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_concurrent_and_replayed_apply_have_one_persisted_winner(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"winner\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    results = await asyncio.gather(
        prepared.service.apply(prepared.run_id, decision.id, token, _principal()),
        prepared.service.apply(prepared.run_id, decision.id, token, _principal()),
        return_exceptions=True,
    )

    receipts = [item for item in results if not isinstance(item, BaseException)]
    errors = [item for item in results if isinstance(item, WorkspaceApplyError)]
    assert len(receipts) == 1
    assert len(errors) == 1
    assert errors[0].code in {"decision_not_pending", "lease_not_active"}
    assert (prepared.source / "modify.txt").read_bytes() == b"winner\n"
    assert storage.get_apply_receipt_for_decision(decision.id) == receipts[0]

    with pytest.raises(WorkspaceApplyError) as replay:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())
    assert replay.value.code in {"decision_not_pending", "lease_not_active"}


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_source_drift_fails_before_materialization(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"after\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    (prepared.source / "modify.txt").write_bytes(b"external drift\n")

    with pytest.raises(WorkspaceApplyError) as drift:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())
    assert drift.value.code == "source_drift"
    assert storage.get_apply_decision(decision.id).state == "pending"
    assert (prepared.source / "modify.txt").read_bytes() == b"external drift\n"
    (prepared.source / "modify.txt").write_bytes(b"before\n")
    with pytest.raises(WorkspaceApplyError) as reissue:
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal(),
            "replacement",
            timedelta(minutes=5),
        )
    assert reissue.value.code == "decision_not_pending"
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_expired_authority_closes_staging_and_cannot_be_reissued(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    current_time = datetime(2026, 7, 16, 12, tzinfo=UTC)
    monkeypatch.setattr(prepared.service, "_now", lambda: current_time)
    monkeypatch.setattr(
        prepared.service._decision_service,  # noqa: SLF001 - shared deterministic clock
        "_clock",
        lambda: current_time,
    )
    expired, expired_token = await prepared.service.issue_apply_decision(
        prepared.run_id,
        _principal(),
        "already expired",
        timedelta(microseconds=1),
    )
    current_time = expired.expires_at
    with pytest.raises(WorkspaceApplyError) as expiry:
        await prepared.service.apply(prepared.run_id, expired.id, expired_token, _principal())
    assert expiry.value.code == "decision_expired"
    assert storage.get_apply_decision(expired.id).state == "expired"
    assert storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLOSED
    assert not prepared.staging.exists()
    with pytest.raises(WorkspaceApplyError) as reissue:
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal(),
            "replacement",
            timedelta(minutes=5),
        )
    assert reissue.value.code == "lease_not_active"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_authority_expiring_during_validation_cannot_be_claimed(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    current_time = datetime(2026, 7, 12, 12, tzinfo=UTC)
    monkeypatch.setattr(prepared.service, "_now", lambda: current_time)
    monkeypatch.setattr(
        prepared.service._decision_service,  # noqa: SLF001 - shared deterministic clock
        "_clock",
        lambda: current_time,
    )
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    def expire_after_validation(stage: str) -> None:
        nonlocal current_time
        if stage == "after_validate":
            current_time = decision.expires_at

    prepared.service._apply_failure_injector = expire_after_validation  # noqa: SLF001

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "decision_expired"
    assert storage.get_apply_decision(decision.id).state == "expired"
    assert storage.get_apply_receipt_for_decision(decision.id) is None
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    assert storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLOSED
    assert not prepared.staging.exists()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_stage", "expected_rollback_status"),
    [
        ("after_claim", "not_required"),
        ("after_backup", "not_required"),
        ("before_materialize", "not_required"),
        ("after_materialize", "succeeded"),
        ("before_verify", "succeeded"),
        ("before_success_receipt", "succeeded"),
    ],
)
async def test_synchronous_failure_injection_restores_exact_source_snapshot(
    storage,
    tmp_path: Path,
    failure_stage: str,
    expected_rollback_status: str,
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"after\n")
    (prepared.staging / "added.txt").write_bytes(b"new\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    before_tree = _filesystem_snapshot(prepared.source)
    prepared.service._apply_failure_injector = (  # noqa: SLF001 - deterministic fault boundary
        lambda stage: (
            (_ for _ in ()).throw(RuntimeError("injected")) if stage == failure_stage else None
        )
    )

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "materialization_failed"
    after_tree = _filesystem_snapshot(prepared.source)
    assert after_tree == before_tree
    assert _source_authority(prepared) == (
        prepared.source_head,
        prepared.source_ref,
        prepared.source_refs,
        prepared.source_index,
    )
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.outcome == "failed"
    assert receipt.rollback_status == expected_rollback_status
    assert storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLOSED
    assert not prepared.staging.exists()
    with pytest.raises(WorkspaceApplyError) as reissue:
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal(),
            "replacement",
            timedelta(minutes=5),
        )
    assert reissue.value.code == "lease_not_active"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_staging_symlink_parent_redirect_fails_closed(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "nested/file.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_bytes(b"redirected\n")
    shutil.rmtree(prepared.staging / "nested")
    (prepared.staging / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "staging_drift"
    assert (prepared.source / "nested/file.txt").read_bytes() == b"nested\n"
    assert (outside / "file.txt").read_bytes() == b"redirected\n"
    assert storage.get_apply_decision(decision.id).state == "pending"
    (prepared.staging / "nested").unlink()
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_non_utf8_path_is_applied_as_exact_filesystem_bytes(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    raw_name = b"approved-\xff.bin"
    raw_staging = os.fsencode(prepared.staging) + b"/" + raw_name
    try:
        descriptor = os.open(raw_staging, os.O_CREAT | os.O_WRONLY, 0o600)
    except (OSError, UnicodeError):
        pytest.skip("filesystem does not support non-UTF8 fixture names")
    os.write(descriptor, b"raw\x00content")
    os.close(descriptor)
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    receipt = await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    raw_source = os.fsencode(prepared.source) + b"/" + raw_name
    assert receipt.outcome == "succeeded"
    descriptor = os.open(raw_source, os.O_RDONLY)
    try:
        assert os.read(descriptor, 100) == b"raw\x00content"
    finally:
        os.close(descriptor)


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_process_lock_serializes_independent_service_instances(
    storage, tmp_path: Path
) -> None:
    first = WorkspaceService(storage, GitBackend(), tmp_path / "leases-a")
    second = WorkspaceService(storage, GitBackend(), tmp_path / "leases-b")
    acquired = asyncio.Event()

    async def acquire_second() -> None:
        async with second._process_source_lock("a" * 64):  # noqa: SLF001
            acquired.set()

    async with first._process_source_lock("a" * 64):  # noqa: SLF001
        waiter = asyncio.create_task(acquire_second())
        await asyncio.sleep(0.05)
        assert not acquired.is_set()
    await asyncio.wait_for(waiter, timeout=2)
    assert acquired.is_set()
    await first.shutdown()
    await second.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_close_lease_waits_for_source_lock_and_preserves_pending_authority(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, _token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    second = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    source_key = prepared.service._canonical_digest(  # noqa: SLF001
        {"repository": decision.source_repository_id, "root": decision.source_root}
    )

    async with prepared.service._process_source_lock(source_key):  # noqa: SLF001
        closing = asyncio.create_task(second.close_lease(prepared.lease_id, run_id=prepared.run_id))
        await asyncio.sleep(0.05)
        waited_for_source_lock = not closing.done()

    with pytest.raises(WorkspaceConflict, match="apply decision is pending"):
        await closing
    assert waited_for_source_lock
    assert storage.get_apply_decision(decision.id).state == "pending"
    assert storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.ACTIVE
    assert prepared.staging.exists()
    await prepared.service.revoke_apply_decision(prepared.run_id, decision.id, _principal())
    await second.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_claim_without_receipt_is_reported_as_host_crash_gap(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, _token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    storage.claim_apply_decision(
        decision.id,
        expected_version=decision.state_version,
        receipt_id="missing-after-host-crash",
        created_at=datetime.now(UTC),
    )

    status = await prepared.service.get_run_status(prepared.run_id)

    assert status.host_crash_gap is True
    assert status.latest_decision is not None
    assert status.latest_decision.state == "consumed"
    assert status.latest_receipt is None
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_service_rejects_missing_workspace_apply_scope(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _unscoped_principal(),
            "approved",
            timedelta(minutes=5),
        )

    assert caught.value.code == "scope_denied"
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id,
        _principal(),
        "approved",
        timedelta(minutes=5),
    )
    with pytest.raises(WorkspaceApplyError) as apply_caught:
        await prepared.service.apply(
            prepared.run_id,
            decision.id,
            token,
            _unscoped_principal(),
        )
    assert apply_caught.value.code == "scope_denied"
    assert storage.get_apply_decision(decision.id).state == "pending"
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_legacy_token_without_generic_link_fails_before_git_or_receipt(
    storage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id,
        _principal(),
        "approved",
        timedelta(minutes=5),
    )
    persisted = storage.get_apply_decision(decision.id)
    assert persisted is not None
    legacy = persisted.model_copy(update={"decision_request_id": None})
    monkeypatch.setattr(storage, "get_apply_decision", lambda _decision_id: legacy)
    monkeypatch.setattr(
        prepared.service._git,  # noqa: SLF001
        "inspect_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Git inspected")),
    )
    source_before = (prepared.source / "modify.txt").read_bytes()

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(
            prepared.run_id,
            decision.id,
            token,
            _principal(),
        )

    assert caught.value.code == "binding_mismatch"
    assert (prepared.source / "modify.txt").read_bytes() == source_before
    assert persisted.state == "pending"
    assert storage.get_apply_receipt_for_decision(decision.id) is None


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch",
    ("payload", "decision_hash", "lease", "actor"),
)
async def test_linked_generic_mismatch_fails_before_git_source_or_receipt(
    storage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id,
        _principal(),
        "approved",
        timedelta(minutes=5),
    )
    persisted = storage.get_apply_decision(decision.id)
    assert persisted is not None
    record = prepared.service._decision_service.get(decision.id)  # noqa: SLF001
    assert record is not None and record.resolution is not None
    if mismatch in {"payload", "lease"}:
        payload = dict(record.request.payload)
        payload["reason" if mismatch == "payload" else "lease_id"] = "tampered"
        mismatched = record.model_copy(
            update={"request": record.request.model_copy(update={"payload": payload})}
        )
        monkeypatch.setattr(
            prepared.service._decision_service,  # noqa: SLF001
            "get",
            lambda _decision_id: mismatched,
        )
    elif mismatch == "decision_hash":
        altered = persisted.model_copy(update={"decision_hash": "0" * 64})
        monkeypatch.setattr(storage, "get_apply_decision", lambda _decision_id: altered)
    else:
        mismatched = record.model_copy(
            update={
                "resolution": record.resolution.model_copy(
                    update={"actor_principal_id": "user:tampered"}
                )
            }
        )
        monkeypatch.setattr(
            prepared.service._decision_service,  # noqa: SLF001
            "get",
            lambda _decision_id: mismatched,
        )
    monkeypatch.setattr(
        prepared.service._git,  # noqa: SLF001
        "inspect_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Git inspected")),
    )
    source_before = (prepared.source / "modify.txt").read_bytes()

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(
            prepared.run_id,
            decision.id,
            token,
            _principal(),
        )

    assert caught.value.code == "binding_mismatch"
    assert (prepared.source / "modify.txt").read_bytes() == source_before
    assert persisted.state == "pending"
    assert storage.get_apply_receipt_for_decision(decision.id) is None


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_service_rejects_effective_contract_without_enforced_apply_capability(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path, enforce_apply=False)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal(),
            "approved",
            timedelta(minutes=5),
        )

    assert caught.value.code == "capability_not_enforced"
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_successful_apply_cleanup_failure_is_persisted_and_retryable(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"applied\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    backend = prepared.service._git  # noqa: SLF001 - deterministic cleanup failure
    real_remove = backend.remove_worktree

    def fail_remove(*_args, **_kwargs) -> None:
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(backend, "remove_worktree", fail_remove)
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "cleanup_failed"
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None and receipt.outcome == "succeeded"
    assert (
        storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLEANUP_FAILED
    )
    assert (prepared.source / "modify.txt").read_bytes() == b"applied\n"
    assert prepared.staging.exists()

    monkeypatch.setattr(backend, "remove_worktree", real_remove)
    closed = await prepared.service.close_lease(
        prepared.lease_id,
        run_id=prepared.run_id,
    )
    assert closed.state is WorkspaceLeaseState.CLOSED
    assert not prepared.staging.exists()
    assert (prepared.source / "modify.txt").read_bytes() == b"applied\n"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_validation_failure_injection_never_claims_decision(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    prepared.service._apply_failure_injector = (  # noqa: SLF001
        lambda stage: (
            (_ for _ in ()).throw(RuntimeError("injected")) if stage == "after_validate" else None
        )
    )

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "materialization_failed"
    assert storage.get_apply_decision(decision.id).state == "pending"
    assert storage.get_apply_receipt_for_decision(decision.id) is None
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    prepared.service._apply_failure_injector = None  # noqa: SLF001
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_restore_failure_injection_is_persisted_as_rollback_failed(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    prepared.service._apply_failure_injector = (  # noqa: SLF001
        lambda stage: (
            (_ for _ in ()).throw(RuntimeError("injected"))
            if stage in {"after_materialize", "after_restore"}
            else None
        )
    )

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "rollback_failed"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "failed"
    assert (
        storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLEANUP_FAILED
    )
    prepared.service._apply_failure_injector = None  # noqa: SLF001
    closed = await prepared.service.close_lease(
        prepared.lease_id,
        run_id=prepared.run_id,
    )
    assert closed.state is WorkspaceLeaseState.CLOSED


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
@pytest.mark.parametrize("rewrite_kind", ["same-inode", "atomic-replace"])
async def test_rollback_postimage_cas_preserves_external_rewrite(
    storage, tmp_path: Path, rewrite_kind: str
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    def rewrite_after_materialize(stage: str) -> None:
        if stage == "after_materialize":
            target = prepared.source / "modify.txt"
            if rewrite_kind == "same-inode":
                target.write_bytes(b"external\n")
            else:
                replacement = prepared.source / "external-replacement"
                replacement.write_bytes(b"external\n")
                os.replace(replacement, target)
            raise RuntimeError("force rollback after external rewrite")

    prepared.service._apply_failure_injector = rewrite_after_materialize  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "rollback_failed"
    assert (prepared.source / "modify.txt").read_bytes() == b"external\n"
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "failed"
    assert receipt.failure_code == "rollback_failed"
    assert storage.get_workspace_lease(prepared.lease_id).state is (
        WorkspaceLeaseState.CLEANUP_FAILED
    )
    assert prepared.staging.exists()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_nested_add_failure_removes_owned_directories_and_restores_exact_source(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    nested = prepared.staging / "new" / "deep" / "approved.txt"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    before = _filesystem_snapshot(prepared.source)
    prepared.service._apply_failure_injector = (  # noqa: SLF001
        lambda stage: (
            (_ for _ in ()).throw(RuntimeError("nested rollback"))
            if stage == "after_materialize"
            else None
        )
    )

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "materialization_failed"
    assert _filesystem_snapshot(prepared.source) == before
    assert not (prepared.source / "new").exists()
    assert storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLOSED


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_success_receipt_persistence_failure_rolls_back_and_records_failure(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    real_save = storage.save_apply_receipt
    calls = 0

    def fail_once(receipt) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected receipt failure")
        real_save(receipt)

    monkeypatch.setattr(storage, "save_apply_receipt", fail_once)
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "materialization_failed"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None and receipt.outcome == "failed"
    assert receipt.rollback_status == "succeeded"
    await prepared.service.shutdown()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_final_preimage_cas_preserves_concurrent_source_edit(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    def inject_source_race(stage: str) -> None:
        if stage == "before_materialize":
            (prepared.source / "modify.txt").write_bytes(b"external-concurrent-edit\n")

    prepared.service._apply_failure_injector = inject_source_race  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "source_drift"
    assert (prepared.source / "modify.txt").read_bytes() == b"external-concurrent-edit\n"
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.failure_code == "source_drift"
    assert receipt.rollback_status == "not_attempted"
    assert (
        storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLEANUP_FAILED
    )


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_any_unreceipted_claim_remains_visible_and_blocks_new_authority(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    first, _first_token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "first", timedelta(minutes=5)
    )
    with pytest.raises(WorkspaceApplyError) as duplicate:
        await prepared.service.issue_apply_decision(
            prepared.run_id, _principal(), "second", timedelta(minutes=5)
        )
    assert duplicate.value.code == "decision_not_pending"
    storage.claim_apply_decision(
        first.id,
        expected_version=first.state_version,
        receipt_id="missing-after-host-crash",
        created_at=datetime.now(UTC),
    )

    status = await prepared.service.get_run_status(prepared.run_id)
    assert status.latest_decision is not None and status.latest_decision.id == first.id
    assert status.host_crash_gap is True
    with pytest.raises(WorkspaceApplyError) as issue_caught:
        await prepared.service.issue_apply_decision(
            prepared.run_id, _principal(), "third", timedelta(minutes=5)
        )
    assert issue_caught.value.code == "apply_claim_lost"
    with pytest.raises(WorkspaceConflict, match="claim"):
        await prepared.service.close_lease(
            prepared.lease_id,
            run_id=prepared.run_id,
        )


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_revoked_authority_closes_staging_and_remains_terminal(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, _token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    revoked = await prepared.service.revoke_apply_decision(
        prepared.run_id,
        decision.id,
        _principal(),
    )

    status = await prepared.service.get_run_status(prepared.run_id)

    assert revoked.state == "revoked"
    assert status.latest_decision is not None
    assert status.latest_decision.state == "revoked"
    assert status.lease.state is WorkspaceLeaseState.CLOSED
    assert not prepared.staging.exists()
    with pytest.raises(WorkspaceApplyError) as reissue:
        await prepared.service.issue_apply_decision(
            prepared.run_id, _principal(), "replacement", timedelta(minutes=5)
        )
    assert reissue.value.code == "lease_not_active"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_denied_receipt_closes_staging_and_remains_observable(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, _token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    with pytest.raises(WorkspaceApplyError) as wrong_principal:
        await prepared.service.deny_apply_decision(
            prepared.run_id,
            decision.id,
            _principal("another-reviewer"),
            "must not consume another principal's authority",
        )
    assert wrong_principal.value.code == "principal_mismatch"
    assert storage.get_apply_decision(decision.id).state == "pending"
    denied = await prepared.service.deny_apply_decision(
        prepared.run_id,
        decision.id,
        _principal(),
        "reviewer denied governed apply",
    )

    status = await prepared.service.get_run_status(prepared.run_id)

    assert status.latest_receipt == denied
    assert status.lease.state is WorkspaceLeaseState.CLOSED
    assert not prepared.staging.exists()
    with pytest.raises(WorkspaceApplyError) as reissue:
        await prepared.service.issue_apply_decision(
            prepared.run_id, _principal(), "replacement", timedelta(minutes=5)
        )
    assert reissue.value.code == "lease_not_active"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_unreceipted_claim_status_remains_visible_when_source_already_changed(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, _token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    storage.claim_apply_decision(
        decision.id,
        expected_version=decision.state_version,
        receipt_id="missing-after-effect",
        created_at=datetime.now(UTC),
    )
    (prepared.source / "modify.txt").write_bytes(b"possible-crash-effect\n")

    status = await prepared.service.get_run_status(prepared.run_id)

    assert status.host_crash_gap is True
    assert status.latest_decision is not None
    assert status.latest_decision.id == decision.id
    assert status.latest_receipt is None


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_committed_success_receipt_remains_authoritative_if_save_raises(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    real_save = storage.save_apply_receipt

    def commit_then_raise(receipt) -> None:
        real_save(receipt)
        raise RuntimeError("ambiguous post-commit failure")

    monkeypatch.setattr(storage, "save_apply_receipt", commit_then_raise)

    receipt = await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert receipt.outcome == "succeeded"
    assert storage.get_apply_receipt_for_decision(decision.id) == receipt
    assert (prepared.source / "modify.txt").read_bytes() == b"approved\n"
    assert storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLOSED


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_committed_failure_receipt_remains_authoritative_if_save_raises(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    real_save = storage.save_apply_receipt

    def commit_then_raise(receipt) -> None:
        real_save(receipt)
        raise RuntimeError("ambiguous post-commit failure")

    monkeypatch.setattr(storage, "save_apply_receipt", commit_then_raise)
    prepared.service._apply_failure_injector = (  # noqa: SLF001
        lambda stage: (
            (_ for _ in ()).throw(RuntimeError("injected"))
            if stage == "after_materialize"
            else None
        )
    )

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "materialization_failed"
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.outcome == "failed"
    assert receipt.rollback_status == "succeeded"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_modify_preserves_exact_non_git_posix_mode(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    source_file = prepared.source / "modify.txt"
    source_file.chmod(0o600)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    receipt = await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert receipt.outcome == "succeeded"
    assert source_file.read_bytes() == b"approved\n"
    assert source_file.stat().st_mode & 0o7777 == 0o600


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_post_materialization_non_git_mode_drift_fails_closed(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    source_file = prepared.source / "modify.txt"
    source_file.chmod(0o600)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    def drift_non_git_mode(stage: str) -> None:
        if stage == "after_materialize":
            source_file.chmod(0o640)

    prepared.service._apply_failure_injector = drift_non_git_mode  # noqa: SLF001

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "rollback_failed"
    assert source_file.read_bytes() == b"approved\n"
    assert source_file.stat().st_mode & 0o7777 == 0o640
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.outcome == "failed"
    assert receipt.rollback_status == "failed"
    assert storage.get_workspace_lease(prepared.lease_id).state is (
        WorkspaceLeaseState.CLEANUP_FAILED
    )


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_closed_success_status_and_receipt_are_read_only_observable(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    receipt = await prepared.service.apply(prepared.run_id, decision.id, token, _principal())
    monkeypatch.setattr(
        prepared.service._git,  # noqa: SLF001 - terminal status must not inspect Git
        "inspect_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("must not inspect")),
    )

    status = await prepared.service.get_run_status(prepared.run_id)

    assert status.lease.state is WorkspaceLeaseState.CLOSED
    assert status.latest_decision is not None
    assert status.latest_decision.state == "consumed"
    assert status.latest_receipt == receipt
    with pytest.raises(WorkspaceApplyError) as issue_caught:
        await prepared.service.issue_apply_decision(
            prepared.run_id, _principal(), "again", timedelta(minutes=5)
        )
    assert issue_caught.value.code == "lease_not_active"
    with pytest.raises(WorkspaceApplyError) as apply_caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())
    assert apply_caught.value.code == "decision_not_pending"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_cleanup_failed_status_and_success_receipt_remain_observable(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    monkeypatch.setattr(
        prepared.service._git,  # noqa: SLF001 - deterministic cleanup failure
        "remove_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())
    assert caught.value.code == "cleanup_failed"

    status = await prepared.service.get_run_status(prepared.run_id)

    assert status.lease.state is WorkspaceLeaseState.CLEANUP_FAILED
    assert status.latest_receipt is not None
    assert status.latest_receipt.outcome == "succeeded"
    with pytest.raises(WorkspaceApplyError) as issue_caught:
        await prepared.service.issue_apply_decision(
            prepared.run_id, _principal(), "again", timedelta(minutes=5)
        )
    assert issue_caught.value.code == "lease_not_active"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_success_terminal_state_precommit_failure_is_recoverable(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    real_transition = storage.transition_workspace_lease
    failed = False

    def fail_before_terminal_commit(*args, **kwargs):
        nonlocal failed
        if (
            not failed
            and kwargs.get("expected_state") is WorkspaceLeaseState.CLOSING
            and kwargs.get("new_state") is WorkspaceLeaseState.CLOSED
        ):
            failed = True
            raise RuntimeError("injected before terminal commit")
        return real_transition(*args, **kwargs)

    monkeypatch.setattr(storage, "transition_workspace_lease", fail_before_terminal_commit)
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "cleanup_failed"
    assert (
        storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLEANUP_FAILED
    )
    assert not prepared.staging.exists()
    recovered = await prepared.service.close_lease(
        prepared.lease_id,
        run_id=prepared.run_id,
    )
    assert recovered.state is WorkspaceLeaseState.CLOSED


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_success_terminal_state_postcommit_error_is_disambiguated(
    storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    real_transition = storage.transition_workspace_lease
    failed = False

    def fail_after_terminal_commit(*args, **kwargs):
        nonlocal failed
        result = real_transition(*args, **kwargs)
        if (
            not failed
            and kwargs.get("expected_state") is WorkspaceLeaseState.CLOSING
            and kwargs.get("new_state") is WorkspaceLeaseState.CLOSED
        ):
            failed = True
            raise RuntimeError("injected after terminal commit")
        return result

    monkeypatch.setattr(storage, "transition_workspace_lease", fail_after_terminal_commit)

    receipt = await prepared.service.apply(
        prepared.run_id,
        decision.id,
        token,
        _principal(),
    )

    assert receipt.outcome == "succeeded"
    assert storage.get_workspace_lease(prepared.lease_id).state is WorkspaceLeaseState.CLOSED
    assert not prepared.staging.exists()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_final_preimage_cas_rejects_parent_chain_replacement(storage, tmp_path: Path) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "nested/file.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    displaced = prepared.source / "external-parent"

    def replace_parent(stage: str) -> None:
        if stage == "before_materialize":
            (prepared.source / "nested").rename(displaced)
            (prepared.source / "nested").mkdir(mode=0o755)
            (prepared.source / "nested/file.txt").write_bytes(b"nested\n")

    prepared.service._apply_failure_injector = replace_parent  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "source_drift"
    assert (prepared.source / "nested/file.txt").read_bytes() == b"nested\n"
    assert (displaced / "file.txt").read_bytes() == b"nested\n"
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "not_attempted"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_failed_apply_preserves_refs_head_index_and_object_database(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    authority_before = _source_authority(prepared)
    objects_before = _object_database_snapshot(prepared.source)
    prepared.service._apply_failure_injector = (  # noqa: SLF001
        lambda stage: (
            (_ for _ in ()).throw(RuntimeError("injected"))
            if stage == "after_materialize"
            else None
        )
    )

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "materialization_failed"
    assert _source_authority(prepared) == authority_before
    assert _object_database_snapshot(prepared.source) == objects_before


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_final_git_authority_cas_preserves_external_index_change(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    objects_before = _object_database_snapshot(prepared.source)

    def change_index(stage: str) -> None:
        if stage == "before_materialize":
            _git(prepared.source, "update-index", "--chmod=+x", "modify.txt")

    prepared.service._apply_failure_injector = change_index  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "source_drift"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    assert (prepared.source / ".git/index").read_bytes() != prepared.source_index
    assert _git(prepared.source, "rev-parse", "HEAD") == prepared.source_head
    assert _git(prepared.source, "symbolic-ref", "HEAD") == prepared.source_ref
    assert (
        _git(prepared.source, "for-each-ref", "--format=%(refname)%00%(objectname)")
        == prepared.source_refs
    )
    assert _object_database_snapshot(prepared.source) == objects_before
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "not_attempted"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_final_git_authority_cas_preserves_external_ref_change(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    def change_ref(stage: str) -> None:
        if stage == "before_materialize":
            _git(prepared.source, "update-ref", "refs/heads/external", "HEAD")

    prepared.service._apply_failure_injector = change_ref  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "source_drift"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    assert _git(prepared.source, "rev-parse", "refs/heads/external") == prepared.source_head
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "not_attempted"


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_post_materialization_ref_change_is_preserved_after_owned_rollback(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    def change_ref(stage: str) -> None:
        if stage == "after_materialize":
            _git(prepared.source, "update-ref", "refs/heads/external", "HEAD")

    prepared.service._apply_failure_injector = change_ref  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "source_drift"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    assert _git(prepared.source, "rev-parse", "refs/heads/external") == prepared.source_head
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "succeeded"
    assert receipt.evidence[-1] == (
        "governed paths restored; independent source authority change preserved"
    )
    assert str(prepared.source) not in " ".join(receipt.evidence)


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_post_materialization_object_change_is_preserved_after_owned_rollback(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    external_oid: str | None = None

    def write_object(stage: str) -> None:
        nonlocal external_oid
        if stage == "after_materialize":
            assert _GIT is not None
            external_oid = (
                subprocess.run(
                    [_GIT, "hash-object", "-w", "--stdin"],
                    cwd=prepared.source,
                    check=True,
                    input=b"external concurrent object\n",
                    capture_output=True,
                )
                .stdout.decode("ascii")
                .strip()
            )

    prepared.service._apply_failure_injector = write_object  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "source_drift"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    assert external_oid is not None
    assert (prepared.source / ".git" / "objects" / external_oid[:2] / external_oid[2:]).is_file()
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "succeeded"
    assert receipt.evidence[-1] == (
        "governed paths restored; independent source authority change preserved"
    )
    assert str(prepared.source) not in " ".join(receipt.evidence)


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_same_inode_same_size_object_control_change_is_detected_and_preserved(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    control = prepared.source / ".git" / "objects" / "info" / "governed-control"
    control.parent.mkdir(exist_ok=True)
    control.write_bytes(b"before-control\n")
    original_inode = control.stat().st_ino
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )

    def rewrite_control(stage: str) -> None:
        if stage == "after_materialize":
            control.write_bytes(b"extern-control\n")

    prepared.service._apply_failure_injector = rewrite_control  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "source_drift"
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    assert control.read_bytes() == b"extern-control\n"
    assert control.stat().st_ino == original_inode
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "succeeded"
    assert storage.get_workspace_lease(prepared.lease_id).state is (
        WorkspaceLeaseState.CLEANUP_FAILED
    )
    assert prepared.staging.exists()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_materialize_parent_swap_after_dirfd_open_never_touches_outside(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "nested/file.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    outside = tmp_path / "outside-parent"
    outside.mkdir()
    victim = outside / "file.txt"
    victim.write_bytes(b"outside-victim\n")
    displaced = prepared.source / "nested-displaced"
    swapped = False

    def swap_parent(stage: str) -> None:
        nonlocal swapped
        if not swapped and stage == "after_materialize_parent_open":
            (prepared.source / "nested").rename(displaced)
            (prepared.source / "nested").symlink_to(outside, target_is_directory=True)
            swapped = True

    prepared.service._apply_failure_injector = swap_parent  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "rollback_failed"
    assert swapped is True
    assert victim.read_bytes() == b"outside-victim\n"
    assert (displaced / "file.txt").read_bytes() == b"nested\n"
    assert storage.get_workspace_lease(prepared.lease_id).state is (
        WorkspaceLeaseState.CLEANUP_FAILED
    )
    assert prepared.staging.exists()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_published_source_root_swap_fails_closed_and_preserves_replacement(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_bytes(b"approved\n")
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    replacement = tmp_path / "replacement-source"
    shutil.copytree(prepared.source, replacement, symlinks=True)
    replacement_snapshot = _filesystem_snapshot(replacement)
    displaced = tmp_path / "displaced-source"
    swapped = False

    def swap_published_root(stage: str) -> None:
        nonlocal swapped
        if not swapped and stage == "after_materialize":
            prepared.source.rename(displaced)
            replacement.rename(prepared.source)
            swapped = True

    prepared.service._apply_failure_injector = swap_published_root  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "rollback_failed"
    assert swapped is True
    assert _filesystem_snapshot(prepared.source) == replacement_snapshot
    assert (prepared.source / "modify.txt").read_bytes() == b"before\n"
    assert (displaced / "modify.txt").read_bytes() == b"before\n"
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "failed"
    assert storage.get_workspace_lease(prepared.lease_id).state is (
        WorkspaceLeaseState.CLEANUP_FAILED
    )
    assert prepared.staging.exists()


@pytest.mark.skipif(_GIT is None, reason="git is required")
@pytest.mark.asyncio
async def test_pre_publish_same_content_external_add_is_never_claimed_for_rollback(
    storage, tmp_path: Path
) -> None:
    prepared = await _prepared(storage, tmp_path)
    approved = b"approved external-looking bytes\n"
    (prepared.staging / "added.txt").write_bytes(approved)
    await prepared.service.capture_change_set(prepared.lease_id, run_id=prepared.run_id)
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id, _principal(), "approved", timedelta(minutes=5)
    )
    external = prepared.source / "added.txt"
    injected = False

    def create_external_before_publish(stage: str) -> None:
        nonlocal injected
        if not injected and stage == "after_write_absence_check_before_publish":
            external.write_bytes(approved)
            injected = True

    prepared.service._apply_failure_injector = create_external_before_publish  # noqa: SLF001
    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.apply(prepared.run_id, decision.id, token, _principal())

    assert caught.value.code == "rollback_failed"
    assert injected is True
    assert external.read_bytes() == approved
    receipt = storage.get_apply_receipt_for_decision(decision.id)
    assert receipt is not None
    assert receipt.rollback_status == "failed"
    assert storage.get_workspace_lease(prepared.lease_id).state is (
        WorkspaceLeaseState.CLEANUP_FAILED
    )
    assert prepared.staging.exists()
