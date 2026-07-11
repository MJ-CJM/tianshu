"""Run-local workspace binding and isolation contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import ExecutionContext, bind_execution_context
from tianshu.executor.workspace_context import (
    BoundWorkspace,
    WorkspaceBindingError,
    bind_workspace,
    get_bound_workspace,
    require_bound_workspace,
    resolve_workspace_root,
    validate_current_workspace_binding,
)
from tianshu.models.governance_contract import (
    ObjectiveV1,
    RequestedGovernanceContractV1,
    WorkspacePolicyV1,
)
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.models.workspace import WorkspaceLease, WorkspaceLeaseState

_SHA = "a" * 40


def _effective():
    base = resolve_governance_contract(
        RequestedGovernanceContractV1(objective=ObjectiveV1(goal="bind workspace")),
        native_manifest(),
        probe_host_capabilities(),
    )
    workspace = WorkspacePolicyV1(
        source_id="source-main",
        base_revision=_SHA,
        staging_mode="isolated",
        apply_mode="governed",
        require_clean_source=True,
    )
    return base.model_copy(
        update={
            "workspace": workspace,
            "resolved_source_id": workspace.source_id,
            "resolved_base_revision": _SHA,
        }
    )


def _lease(root: Path, *, run_id: str = "run-1") -> WorkspaceLease:
    root.mkdir(parents=True, exist_ok=True)
    return WorkspaceLease(
        id=f"lease-{run_id}",
        run_id=run_id,
        lineage_root_run_id=run_id,
        attempt=0,
        source_kind="git",
        apply_mode="governed",
        source_root=str(root.parent / "source"),
        source_repository_id="repo-identity",
        source_git_dir=str(root.parent / "source/.git"),
        source_git_dir_identity="b" * 64,
        base_revision=_SHA,
        staging_root=str(root),
        staging_git_dir=str(root / ".git"),
        staging_git_dir_identity="c" * 64,
        state=WorkspaceLeaseState.ACTIVE,
        state_version=2,
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
    )


def _bound(root: Path, *, run_id: str = "run-1") -> BoundWorkspace:
    return BoundWorkspace(lease=_lease(root, run_id=run_id), effective_contract=_effective())


def test_nested_binding_restores_outer_context_after_error(tmp_path: Path) -> None:
    outer = _bound(tmp_path / "outer", run_id="outer")
    inner = _bound(tmp_path / "inner", run_id="inner")
    assert get_bound_workspace() is None

    with bind_workspace(outer):
        assert require_bound_workspace() is outer
        assert resolve_workspace_root(tmp_path / "legacy") == outer.root
        with pytest.raises(RuntimeError, match="boom"), bind_workspace(inner):
            assert get_bound_workspace() is inner
            raise RuntimeError("boom")
        assert get_bound_workspace() is outer

    assert get_bound_workspace() is None


@pytest.mark.asyncio
async def test_cancelled_binding_does_not_leak_to_parent(tmp_path: Path) -> None:
    bound = _bound(tmp_path / "cancel")
    entered = asyncio.Event()

    async def child() -> None:
        with bind_workspace(bound):
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(child())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert get_bound_workspace() is None


@pytest.mark.asyncio
async def test_concurrent_tasks_keep_distinct_roots_and_locks(tmp_path: Path) -> None:
    first = _bound(tmp_path / "first", run_id="first")
    second = _bound(tmp_path / "second", run_id="second")
    release = asyncio.Event()

    async def observe(bound: BoundWorkspace) -> tuple[Path, asyncio.Lock]:
        with bind_workspace(bound):
            await release.wait()
            current = require_bound_workspace()
            return resolve_workspace_root(tmp_path / "legacy"), current.tool_lock

    tasks = [asyncio.create_task(observe(item)) for item in (first, second)]
    release.set()
    results = await asyncio.gather(*tasks)

    assert results == [(first.root, first.tool_lock), (second.root, second.tool_lock)]
    assert first.tool_lock is not second.tool_lock
    assert get_bound_workspace() is None


def test_bound_workspace_rejects_inactive_or_mismatched_lease(tmp_path: Path) -> None:
    lease = _lease(tmp_path / "staging")
    effective = _effective()
    with pytest.raises(WorkspaceBindingError, match="active"):
        BoundWorkspace(
            lease=lease.model_copy(update={"state": WorkspaceLeaseState.CLOSED}),
            effective_contract=effective,
        )
    with pytest.raises(WorkspaceBindingError, match="base revision"):
        BoundWorkspace(
            lease=lease.model_copy(update={"base_revision": "b" * 40}),
            effective_contract=effective,
        )


def test_require_validates_run_contract_lease_and_root(tmp_path: Path) -> None:
    bound = _bound(tmp_path / "staging")
    with bind_workspace(bound):
        assert (
            require_bound_workspace(
                run_id=bound.lease.run_id,
                lease_id=bound.lease.id,
                effective_contract_hash=bound.effective_contract.content_hash,
                root=bound.root,
            )
            is bound
        )
        for kwargs, message in (
            ({"run_id": "other"}, "run"),
            ({"lease_id": "other"}, "lease"),
            ({"effective_contract_hash": "0" * 64}, "contract"),
            ({"root": tmp_path / "source"}, "root"),
        ):
            with pytest.raises(WorkspaceBindingError, match=message):
                require_bound_workspace(**kwargs)


def test_child_run_must_be_explicitly_authorized_on_shared_lease(tmp_path: Path) -> None:
    bound = _bound(tmp_path / "staging")
    with bind_workspace(bound):
        with pytest.raises(WorkspaceBindingError, match="run"):
            require_bound_workspace(run_id="child-run")

        bound.authorize_run("child-run")

        assert require_bound_workspace(run_id="child-run") is bound
        with pytest.raises(WorkspaceBindingError, match="run"):
            require_bound_workspace(run_id="untrusted-run")


def test_bound_workspace_rejects_staging_path_replaced_after_bind(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    bound = _bound(staging)
    original = tmp_path / "staging-original"
    outside = tmp_path / "outside"
    outside.mkdir()
    staging.rename(original)
    staging.symlink_to(outside, target_is_directory=True)

    with bind_workspace(bound), pytest.raises(WorkspaceBindingError, match="identity"):
        require_bound_workspace()


def test_legacy_execution_context_does_not_fabricate_bound_workspace() -> None:
    effective = resolve_governance_contract(
        RequestedGovernanceContractV1(objective=ObjectiveV1(goal="legacy workspace")),
        native_manifest(),
        probe_host_capabilities(),
    )
    context = ExecutionContext(
        correlation_id="legacy-run",
        actor=Principal(
            id="legacy-actor",
            kind=PrincipalKind.SERVICE,
            display_name="Legacy Actor",
        ),
        effective_contract=effective,
        workspace_lease_id="fabricated-lease",
    )

    with bind_execution_context(context):
        assert validate_current_workspace_binding() is None
