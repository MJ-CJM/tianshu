from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.git_backend import GitBackend
from tianshu.executor.workspace_runtime import WorkspaceContractError, WorkspaceRuntime
from tianshu.executor.workspace_service import WorkspaceService
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.governance_contract import (
    LegacyEdictGovernanceMapper,
    RecoveryPolicyV1,
    WorkspacePolicyV1,
)
from tianshu.models.workspace import WorkspaceLeaseState

_GIT = shutil.which("git") or "git"


def _workspace_service(storage, backend, staging_root) -> WorkspaceService:
    return WorkspaceService(storage, backend, staging_root, DecisionService(storage))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [_GIT, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Tianshu Test")
    _git(path, "config", "user.email", "tianshu@example.test")
    (path / "tracked.txt").write_text("base\n")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-qm", "base")
    return path


def _effective(
    *,
    workspace: WorkspacePolicyV1 | None = None,
    recovery: RecoveryPolicyV1 | None = None,
):
    edict = Edict(goal="governed runtime")
    requested = LegacyEdictGovernanceMapper.from_edict(
        edict,
        default_workspace_id="workspace-main",
    )
    effective = resolve_governance_contract(requested, native_manifest(), probe_host_capabilities())
    workspace = workspace or effective.workspace
    recovery = recovery or effective.recovery
    return effective.model_copy(
        update={
            "workspace": workspace,
            "recovery": recovery,
            "resolved_source_id": (
                workspace.source_id if workspace.staging_mode == "isolated" else None
            ),
            "resolved_base_revision": (
                workspace.base_revision if workspace.staging_mode == "isolated" else None
            ),
        }
    )


def _governed_effective(*, source_id: str = "workspace-main", base: str = "HEAD"):
    return _effective(
        workspace=WorkspacePolicyV1(
            source_id=source_id,
            base_revision=base,
            staging_mode="isolated",
            apply_mode="governed",
            require_clean_source=True,
        ),
        recovery=RecoveryPolicyV1(require_restore_point=True),
    )


def _saved_memorial(storage, instruction: str, *, parent_id: str | None = None) -> Memorial:
    edict = Edict(goal="workspace runtime")
    storage.save_edict(edict)
    memorial = Memorial(
        edict_id=edict.id,
        instruction=instruction,
        parent_memorial_id=parent_id,
    )
    storage.save_memorial(memorial)
    return memorial


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_governed_prepare_resolves_head_and_binds_active_lease(
    storage, tmp_path: Path
) -> None:
    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    memorial = _saved_memorial(storage, "change the source")

    prepared = await runtime.prepare(_governed_effective(), memorial)

    assert prepared.bound is not None
    assert prepared.bound.lease.run_id == memorial.id
    assert prepared.bound.lease.source_root == str(source.resolve())
    assert prepared.effective.resolved_base_revision == _git(source, "rev-parse", "HEAD")
    assert prepared.bound.effective_contract.content_hash == prepared.effective.content_hash
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_follow_up_derives_contiguous_workspace_lineage(storage, tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    parent = _saved_memorial(storage, "first")
    first = await runtime.prepare(_governed_effective(), parent)
    assert first.bound is not None
    await service.close_lease(first.bound.lease.id, run_id=parent.id)

    child = Memorial(
        edict_id=parent.edict_id, instruction="follow up", parent_memorial_id=parent.id
    )
    storage.save_memorial(child)
    second = await runtime.prepare(_governed_effective(), child)

    assert second.bound is not None
    assert second.bound.lease.lineage_root_run_id == parent.id
    assert second.bound.lease.parent_run_id == parent.id
    assert second.bound.lease.attempt == 1
    await service.shutdown()


@pytest.mark.parametrize(
    "workspace",
    [
        WorkspacePolicyV1(
            source_id="workspace-main",
            base_revision=None,
            staging_mode="isolated",
            apply_mode="governed",
            require_clean_source=True,
        ),
        WorkspacePolicyV1(
            source_id="workspace-main",
            base_revision="",
            staging_mode="isolated",
            apply_mode="governed",
            require_clean_source=True,
        ),
        WorkspacePolicyV1(
            source_id="workspace-main",
            base_revision="HEAD",
            staging_mode="isolated",
            apply_mode="none",
            require_clean_source=True,
        ),
        WorkspacePolicyV1(
            source_id="workspace-main",
            base_revision=None,
            staging_mode="ephemeral",
            apply_mode="none",
            require_clean_source=False,
        ),
        WorkspacePolicyV1(
            source_id="workspace-main",
            base_revision="HEAD",
            staging_mode="isolated",
            apply_mode="governed",
            require_clean_source=True,
        ),
    ],
)
async def test_invalid_workspace_matrix_fails_before_lease_creation(
    storage,
    tmp_path: Path,
    workspace: WorkspacePolicyV1,
) -> None:
    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    memorial = _saved_memorial(storage, "invalid")

    with pytest.raises(WorkspaceContractError):
        await runtime.prepare(_effective(workspace=workspace), memorial)

    assert storage.get_workspace_lease_by_run(memorial.id) is None
    await service.shutdown()


async def test_unknown_source_id_is_rejected(storage, tmp_path: Path) -> None:
    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    memorial = _saved_memorial(storage, "unknown")

    with pytest.raises(WorkspaceContractError, match="workspace-main"):
        await runtime.prepare(_governed_effective(source_id="other-source"), memorial)

    assert storage.get_workspace_lease_by_run(memorial.id) is None
    await service.shutdown()


async def test_legacy_shared_remains_unleased_without_workspace_service(storage) -> None:
    runtime = WorkspaceRuntime(storage=storage, service=None, workspace_sources=None)
    memorial = _saved_memorial(storage, "legacy")
    effective = _effective()

    prepared = await runtime.prepare(effective, memorial)

    assert prepared.bound is None
    assert prepared.effective is effective
    assert storage.get_workspace_lease_by_run(memorial.id) is None


@pytest.mark.parametrize(
    "updates",
    [
        {"resolved_source_id": "stale-source"},
        {"resolved_base_revision": "a" * 40},
    ],
)
async def test_legacy_shared_rejects_stale_resolved_workspace_identity(
    storage,
    updates: dict[str, str],
) -> None:
    runtime = WorkspaceRuntime(storage=storage, service=None, workspace_sources=None)
    memorial = _saved_memorial(storage, "legacy")
    effective = _effective().model_copy(update=updates)

    with pytest.raises(WorkspaceContractError, match="legacy_shared"):
        await runtime.prepare(effective, memorial)


async def test_legacy_shared_resumes_historical_equal_resolved_source(storage) -> None:
    runtime = WorkspaceRuntime(storage=storage, service=None, workspace_sources=None)
    memorial = _saved_memorial(storage, "legacy historical resume")
    historical = _effective().model_copy(update={"resolved_source_id": "workspace-main"})
    storage.save_effective_governance_contract(memorial.id, memorial.edict_id, historical)
    resumed = storage.get_memorial(memorial.id)
    assert resumed is not None
    assert resumed.effective_governance_contract is not None

    prepared = await runtime.prepare(resumed.effective_governance_contract, resumed)

    assert prepared.bound is None
    assert prepared.effective.resolved_source_id == prepared.effective.workspace.source_id


async def test_legacy_shared_rejects_restore_point_residue(storage) -> None:
    runtime = WorkspaceRuntime(storage=storage, service=None, workspace_sources=None)
    memorial = _saved_memorial(storage, "legacy restore residue")
    effective = _effective(recovery=RecoveryPolicyV1(require_restore_point=True)).model_copy(
        update={"resolved_source_id": None, "resolved_base_revision": None}
    )

    with pytest.raises(WorkspaceContractError, match="legacy_shared"):
        await runtime.prepare(effective, memorial)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_ephemeral_scratch_is_leased_and_rejects_stale_resolution(
    storage,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    workspace = WorkspacePolicyV1(
        source_id=None,
        base_revision=None,
        staging_mode="ephemeral",
        apply_mode="none",
        require_clean_source=False,
    )
    memorial = _saved_memorial(storage, "scratch")
    prepared = await runtime.prepare(
        _effective(workspace=workspace).model_copy(
            update={"resolved_source_id": None, "resolved_base_revision": None}
        ),
        memorial,
    )

    assert prepared.bound is not None
    assert prepared.bound.lease.source_kind == "scratch"
    await service.close_lease(prepared.bound.lease.id, run_id=memorial.id)

    stale_memorial = Memorial(
        edict_id=memorial.edict_id,
        instruction="stale scratch",
        parent_memorial_id=memorial.id,
    )
    storage.save_memorial(stale_memorial)
    with pytest.raises(WorkspaceContractError, match="ephemeral"):
        await runtime.prepare(
            _effective(workspace=workspace).model_copy(update={"resolved_base_revision": "a" * 40}),
            stale_memorial,
        )
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_terminal_cleanup_is_shielded_before_cancellation_propagates(
    storage,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    workspace = WorkspacePolicyV1(
        source_id=None,
        base_revision=None,
        staging_mode="ephemeral",
        apply_mode="none",
        require_clean_source=False,
    )
    memorial = _saved_memorial(storage, "scratch cancellation")
    prepared = await runtime.prepare(
        _effective(workspace=workspace).model_copy(
            update={"resolved_source_id": None, "resolved_base_revision": None}
        ),
        memorial,
    )
    assert prepared.bound is not None

    started = asyncio.Event()
    release = asyncio.Event()
    closed = asyncio.Event()
    original_close = service.close_lease

    async def delayed_close(lease_id: str, *, run_id: str):
        started.set()
        await release.wait()
        result = await original_close(lease_id, run_id=run_id)
        closed.set()
        return result

    service.close_lease = delayed_close
    finalizer = asyncio.create_task(runtime.finalize(prepared.bound, TaskStatus.CANCELLED))
    await started.wait()
    finalizer.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await finalizer
    assert closed.is_set()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_git_capture_and_cleanup_finish_before_cancellation_propagates(
    storage,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    memorial = _saved_memorial(storage, "capture cancellation")
    prepared = await runtime.prepare(_governed_effective(), memorial)
    assert prepared.bound is not None
    (prepared.bound.root / "tracked.txt").write_text("captured after cancellation\n")

    started = asyncio.Event()
    release = asyncio.Event()
    original_capture = service.capture_change_set

    async def delayed_capture(lease_id: str, *, run_id: str):
        started.set()
        await release.wait()
        return await original_capture(lease_id, run_id=run_id)

    service.capture_change_set = delayed_capture
    finalizer = asyncio.create_task(runtime.finalize(prepared.bound, TaskStatus.CANCELLED))
    await started.wait()
    finalizer.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await finalizer
    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    change_set = storage.get_latest_canonical_change_set_for_lease(lease.id)
    assert change_set is not None and len(change_set.changes) == 1


@pytest.mark.parametrize("case", ["missing", "cross-edict", "cycle", "depth"])
async def test_invalid_memorial_lineage_fails_closed(
    storage,
    tmp_path: Path,
    case: str,
) -> None:
    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    edict = Edict(goal="lineage")
    storage.save_edict(edict)

    if case == "missing":
        current = Memorial(
            edict_id=edict.id,
            instruction="missing",
            parent_memorial_id="missing-parent",
        )
    elif case == "cross-edict":
        other = Edict(goal="other")
        storage.save_edict(other)
        parent = Memorial(edict_id=other.id, instruction="other parent")
        storage.save_memorial(parent)
        current = Memorial(
            edict_id=edict.id,
            instruction="cross",
            parent_memorial_id=parent.id,
        )
    elif case == "cycle":
        first = Memorial(edict_id=edict.id, instruction="first")
        storage.save_memorial(first)
        second = Memorial(
            edict_id=edict.id,
            instruction="second",
            parent_memorial_id=first.id,
        )
        storage.save_memorial(second)
        with storage._lock, storage._conn:  # noqa: SLF001 - corruption guard fixture
            storage._conn.execute(  # noqa: SLF001
                "UPDATE memorials SET parent_memorial_id = ? WHERE id = ?",
                (second.id, first.id),
            )
        current = Memorial(
            edict_id=edict.id,
            instruction="cycle",
            parent_memorial_id=first.id,
        )
    else:
        parent_id = None
        for index in range(65):
            parent = Memorial(
                edict_id=edict.id,
                instruction=f"parent-{index}",
                parent_memorial_id=parent_id,
            )
            storage.save_memorial(parent)
            parent_id = parent.id
        current = Memorial(
            edict_id=edict.id,
            instruction="too deep",
            parent_memorial_id=parent_id,
        )

    with pytest.raises(WorkspaceContractError, match="lineage"):
        await runtime.prepare(_governed_effective(), current)
    assert storage.get_workspace_lease_by_run(current.id) is None
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_binding_failure_closes_newly_active_lease(
    storage,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from tianshu.executor.workspace_context import WorkspaceBindingError

    source = _repository(tmp_path / "source")
    service = _workspace_service(storage, GitBackend(), tmp_path / "leases")
    runtime = WorkspaceRuntime(
        storage=storage,
        service=service,
        workspace_sources={"workspace-main": source},
    )
    memorial = _saved_memorial(storage, "binding failure")

    def fail_binding(*_args, **_kwargs):
        raise WorkspaceBindingError("binding failed")

    monkeypatch.setattr("tianshu.executor.workspace_runtime.BoundWorkspace", fail_binding)

    with pytest.raises(WorkspaceContractError, match="binding failed"):
        await runtime.prepare(_governed_effective(), memorial)

    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
