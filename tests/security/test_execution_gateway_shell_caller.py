"""The built-in shell tool must delegate to the injected ExecutionGateway."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tianshu.executor import execution_gateway as gateway
from tianshu.executor.adapters import PreparedExecution, PreparedExecutor
from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.models.edict import Edict
from tianshu.models.governance_contract import (
    ObjectiveV1,
    PermissionPolicyV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.tools.builtins import register_builtins
from tianshu.tools.registry import ToolRegistry


class _RecordingGateway:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        now = datetime.now(UTC)
        receipt = gateway.ExecutionReceipt(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            actor_id=request.actor.id,
            purpose=request.purpose,
            effective_contract_hash=request.effective_contract_hash,
            workspace_lease_id=request.workspace_lease_id,
            cwd=request.cwd,
            command_kind="shell",
            executable=request.command_argv[0],
            env_keys=(),
            secret_refs=(),
            network_mode=request.network.mode,
            sandbox_mode=request.sandbox.mode,
            sandbox_enforced=False,
            status="succeeded",
            started_at=now,
            finished_at=now,
            duration_ms=1,
            exit_code=0,
            terminating_signal=None,
            stdout_bytes=3,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
        )
        return gateway.ExecutionResult(
            stdout="ok\n",
            stderr="",
            receipt=receipt,
        )


@pytest.mark.asyncio
async def test_shell_exec_uses_injected_gateway_and_bound_effective_contract(
    tmp_path,
    monkeypatch,
):
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    effective = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="run a shell command"),
            permissions=PermissionPolicyV1(allowed_bash_prefixes=("echo ",)),
        ),
        native_manifest(),
        probe_host_capabilities(),
    )
    context = gateway.ExecutionContext(
        correlation_id="memorial-1",
        actor=Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Test Principal",
        ),
        effective_contract=effective,
        workspace_lease_id="legacy-workspace",
    )
    recording_gateway = _RecordingGateway()
    registry = ToolRegistry()
    register_builtins(
        registry,
        workspace_dir=str(tmp_path),
        execution_gateway=recording_gateway,
    )

    async def _direct_spawn_forbidden(*_args, **_kwargs):
        raise AssertionError("shell_exec bypassed ExecutionGateway")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _direct_spawn_forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _direct_spawn_forbidden)

    with gateway.bind_execution_context(context):
        result = await registry.execute(
            "shell_exec",
            {"command": "echo ok", "cwd": "subdir"},
        )

    assert result.is_error is False
    assert result.content == "ok\n"
    assert result.details["exit_code"] == 0
    assert len(recording_gateway.requests) == 1
    request = recording_gateway.requests[0]
    assert request.actor == context.actor
    assert request.effective_contract == effective
    assert request.correlation_id == "memorial-1"
    assert request.purpose == "tool"
    assert request.argv_command is None
    assert request.shell_command.script == "echo ok"
    assert request.shell_command.interpreter == ("bash", "--noprofile", "--norc")
    assert request.cwd == "subdir"
    assert request.workspace_root == tmp_path.resolve()
    assert request.command_grant.source == "effective-permissions"
    assert request.command_grant.scope == "shell_exec"
    assert request.command_grant.shell_digest is not None


@pytest.mark.asyncio
async def test_prepared_executor_binds_authoritative_contract_for_nested_callers():
    effective = resolve_governance_contract(
        RequestedGovernanceContractV1(objective=ObjectiveV1(goal="bind context")),
        native_manifest(),
        probe_host_capabilities(),
    )

    class _ContextCapturingAdapter:
        async def execute(self, _prepared, _edict, **_kwargs):
            return gateway.get_execution_context()

    prepared_execution = PreparedExecution(
        run_id="memorial-2",
        effective=effective,
        instruction="bind context",
        execution_mode="single",
    )
    prepared = PreparedExecutor(
        adapter=_ContextCapturingAdapter(),
        effective=effective,
        prepared=prepared_execution,
    )
    edict = Edict(goal="bind context", submitter="authenticated-principal")

    captured = await prepared.execute(edict)

    assert captured is not None
    assert captured.correlation_id == "memorial-2"
    assert captured.actor.id == "authenticated-principal"
    assert captured.effective_contract == effective
    assert gateway.get_execution_context() is None
