"""Prepared executors and ExecutionGateway bind real workspace authority."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tianshu.executor import execution_gateway as gateway
from tianshu.executor.adapters import PreparedExecution, PreparedExecutor
from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import grants as gateway_grants
from tianshu.executor.workspace_context import (
    BoundWorkspace,
    WorkspaceBindingError,
    bind_workspace,
)
from tianshu.models.edict import Edict
from tianshu.models.governance_contract import (
    ObjectiveV1,
    PermissionPolicyV1,
    RequestedGovernanceContractV1,
    WorkspacePolicyV1,
)
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.models.workspace import WorkspaceLease, WorkspaceLeaseState

_SHA = "a" * 40


def _effective(*, governed: bool):
    base = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="runtime binding"),
            permissions=PermissionPolicyV1(allowed_bash_prefixes=("echo ",)),
        ),
        native_manifest(),
        probe_host_capabilities(),
    )
    if not governed:
        return base
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


def _bound(root: Path, effective, *, run_id: str = "run-1") -> BoundWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    source = root.parent / "source"
    source.mkdir(exist_ok=True)
    lease = WorkspaceLease(
        id="lease-1",
        run_id=run_id,
        lineage_root_run_id=run_id,
        attempt=0,
        source_kind="git",
        apply_mode="governed",
        source_root=str(source),
        source_repository_id="repo-identity",
        source_git_dir=str(source / ".git"),
        source_git_dir_identity="b" * 64,
        base_revision=_SHA,
        staging_root=str(root),
        staging_git_dir=str(root / ".git"),
        staging_git_dir_identity="c" * 64,
        state=WorkspaceLeaseState.ACTIVE,
        state_version=2,
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
    )
    return BoundWorkspace(lease=lease, effective_contract=effective)


class _Adapter:
    async def execute(self, prepared, edict, **kwargs):
        del prepared, edict, kwargs
        return gateway.get_execution_context()

    async def cancel(self, run_id):
        del run_id
        return True


def _prepared(effective, *, run_id: str = "run-1") -> PreparedExecutor:
    execution = PreparedExecution(
        run_id=run_id,
        effective=effective,
        instruction="runtime binding",
        execution_mode="single",
    )
    return PreparedExecutor(adapter=_Adapter(), effective=effective, prepared=execution)  # type: ignore[arg-type]


def _actor() -> Principal:
    return Principal(id="human-1", kind=PrincipalKind.HUMAN, display_name="Human")


def test_prepared_governed_context_requires_matching_real_lease(tmp_path: Path) -> None:
    effective = _effective(governed=True)
    prepared = _prepared(effective)
    edict = Edict(goal="runtime binding", submitter="human-1")

    with pytest.raises(WorkspaceBindingError, match="bound workspace"):
        prepared.execution_context(edict)

    wrong_run = _bound(tmp_path / "wrong", effective, run_id="run-other")
    with bind_workspace(wrong_run), pytest.raises(WorkspaceBindingError, match="run"):
        prepared.execution_context(edict)

    bound = _bound(tmp_path / "staging", effective)
    with bind_workspace(bound):
        context = prepared.execution_context(edict)
    assert context is not None
    assert context.workspace_lease_id == bound.lease.id
    assert context.correlation_id == bound.lease.run_id


@pytest.mark.asyncio
async def test_prepared_legacy_context_has_no_fabricated_lease(tmp_path: Path) -> None:
    del tmp_path
    effective = _effective(governed=False)
    prepared = _prepared(effective)
    edict = Edict(goal="runtime binding", submitter="human-1")

    observed = await prepared.execute(edict)

    assert observed is not None
    assert observed.workspace_lease_id is None
    assert gateway.get_execution_context() is None


def _execution_context(effective, *, lease_id: str | None) -> gateway.ExecutionContext:
    return gateway.ExecutionContext(
        correlation_id="run-1",
        actor=_actor(),
        effective_contract=effective,
        workspace_lease_id=lease_id,
    )


def _environment() -> gateway.EnvironmentPolicy:
    return gateway.EnvironmentPolicy(allow_names=("PATH",))


def _request_for(root: Path, environment: gateway.EnvironmentPolicy) -> gateway.ExecutionRequest:
    return gateway.request_for_current_execution(
        purpose="tool",
        workspace_root=root,
        cwd=".",
        shell_command=gateway.ShellCommand(script="echo ok"),
        environment=environment,
        timeout_seconds=10,
        stdout_limit_bytes=1024,
        stderr_limit_bytes=1024,
        sandbox=gateway.SandboxRequirement(
            trust_level="trusted-local", mode="host", allow_host=True
        ),
        command_grant=gateway.issue_shell_command_grant(
            "echo ok",
            cwd=".",
            workspace_root=root,
            environment=environment,
        ),
    )


def test_current_request_binds_real_staging_root_and_rejects_source_root(
    tmp_path: Path,
) -> None:
    effective = _effective(governed=True)
    bound = _bound(tmp_path / "staging", effective)
    context = _execution_context(effective, lease_id=bound.lease.id)
    environment = _environment()

    with gateway.bind_execution_context(context), bind_workspace(bound):
        request = _request_for(bound.root, environment)
        with pytest.raises(gateway.ExecutionDenied, match="workspace.*root"):
            _request_for(Path(bound.lease.source_root), environment)

    assert request.workspace_lease_id == bound.lease.id
    assert request.workspace_root == bound.root
    assert request.command_grant is not None
    assert request.command_grant.workspace_lease_id == bound.lease.id
    assert request.command_grant.workspace_root_digest is not None


def test_governed_request_rejects_missing_or_wrong_bound_workspace(tmp_path: Path) -> None:
    effective = _effective(governed=True)
    bound = _bound(tmp_path / "staging", effective)
    environment = _environment()
    correct = _execution_context(effective, lease_id=bound.lease.id)
    missing_lease = _execution_context(effective, lease_id=None)
    wrong = _execution_context(effective, lease_id="wrong-lease")

    with (
        gateway.bind_execution_context(correct),
        pytest.raises(gateway.ExecutionDenied, match="bound workspace"),
    ):
        _request_for(bound.root, environment)
    with (
        gateway.bind_execution_context(missing_lease),
        bind_workspace(bound),
        pytest.raises(gateway.ExecutionDenied, match="lease"),
    ):
        _request_for(bound.root, environment)
    with (
        gateway.bind_execution_context(wrong),
        bind_workspace(bound),
        pytest.raises(gateway.ExecutionDenied, match="lease"),
    ):
        _request_for(bound.root, environment)


def test_legacy_request_remains_compatible_without_fake_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effective = _effective(governed=False)
    context = _execution_context(effective, lease_id=None)
    environment = _environment()
    executable = tmp_path / "rg"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    search_root = tmp_path / "search"
    search_root.mkdir()
    monkeypatch.setattr(
        gateway_grants,
        "_resolve_trusted_adapter_executable",
        lambda _adapter, _root: executable.resolve(),
    )
    argv = (
        str(executable),
        "--json",
        "--line-number",
        "--color=never",
        "--hidden",
        "--max-count=10",
        "--",
        "needle",
        str(search_root),
    )
    with gateway.bind_execution_context(context):
        request = _request_for(tmp_path, environment)
        grep_grant = gateway.issue_grep_command_grant(
            argv,
            workspace_root=tmp_path,
            environment=environment,
        )
        grep_request = gateway.request_for_current_execution(
            purpose="grep",
            workspace_root=tmp_path,
            cwd=".",
            argv_command=gateway.ArgvCommand(argv=argv),
            environment=environment,
            timeout_seconds=10,
            stdout_limit_bytes=1024,
            stderr_limit_bytes=1024,
            sandbox=gateway.SandboxRequirement(
                trust_level="trusted-local", mode="host", allow_host=True
            ),
            command_grant=grep_grant,
        )

    assert request.workspace_root == tmp_path.resolve()
    assert request.workspace_lease_id is None
    assert grep_grant.workspace_lease_id is None
    assert grep_grant.workspace_root_digest is None
    process_gateway = gateway.ExecutionGateway(
        backend=SimpleNamespace(
            supports_sandbox=False,
            supports_network_enforcement=True,
        )
    )
    cwd, _gaps = process_gateway._validate_built_in_guards(grep_request)
    assert cwd == tmp_path.resolve()


def test_legacy_context_cannot_mint_workspace_bound_grant(tmp_path: Path) -> None:
    effective = _effective(governed=False)
    context = _execution_context(effective, lease_id="fabricated-lease")
    environment = _environment()

    with gateway.bind_execution_context(context):
        grant = gateway.issue_shell_command_grant(
            "echo ok",
            cwd=".",
            workspace_root=tmp_path,
            environment=environment,
        )

    assert grant.workspace_lease_id is None
    assert grant.workspace_root_digest is None
    assert grant.resolved_cwd_digest is None
    assert grant.cwd is None
    assert grant.environment_digest is None


@pytest.mark.asyncio
async def test_gateway_rechecks_bound_workspace_on_tampered_request(tmp_path: Path) -> None:
    effective = _effective(governed=True)
    bound = _bound(tmp_path / "staging", effective)
    context = _execution_context(effective, lease_id=bound.lease.id)
    environment = _environment()
    with gateway.bind_execution_context(context), bind_workspace(bound):
        request = _request_for(bound.root, environment)
        tampered = request.model_copy(update={"workspace_root": Path(bound.lease.source_root)})
        with pytest.raises(gateway.ExecutionDenied, match="workspace.*root"):
            await gateway.ExecutionGateway().run(tampered)
