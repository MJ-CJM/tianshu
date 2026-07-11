"""Outer-loop bash/lint acceptance checks must use the injected gateway."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tianshu.executor import execution_gateway as gateway
from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.orchestrator.checks import run_checks
from tianshu.models.acceptance import CheckSpec
from tianshu.models.governance_contract import (
    AcceptanceCheckV1,
    AcceptancePolicyV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind


class _RecordingGateway:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        now = datetime.now(UTC)
        return gateway.ExecutionResult(
            stdout="ok\n",
            stderr="",
            receipt=gateway.ExecutionReceipt(
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
            ),
        )


@pytest.mark.asyncio
async def test_acceptance_bash_uses_frozen_contract_and_injected_gateway(
    tmp_path,
    monkeypatch,
):
    effective = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="verify output"),
            acceptance=AcceptancePolicyV1(
                checks=(
                    AcceptanceCheckV1(
                        kind="bash",
                        name="focused",
                        command="echo ok",
                        timeout_seconds=7,
                    ),
                )
            ),
        ),
        native_manifest(),
        probe_host_capabilities(),
    )
    context = gateway.ExecutionContext(
        correlation_id="memorial-acceptance",
        actor=Principal(
            id="principal-acceptance",
            kind=PrincipalKind.HUMAN,
            display_name="Acceptance Principal",
        ),
        effective_contract=effective,
        workspace_lease_id="legacy-workspace",
    )
    recording_gateway = _RecordingGateway()

    async def _direct_spawn_forbidden(*_args, **_kwargs):
        raise AssertionError("acceptance check bypassed ExecutionGateway")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", _direct_spawn_forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _direct_spawn_forbidden)

    with gateway.bind_execution_context(context):
        result = await run_checks(
            [CheckSpec(kind="bash", name="focused", command="echo ok", timeout_seconds=7)],
            actor_output="",
            llm=None,
            execution_gateway=recording_gateway,
            workspace_root=tmp_path,
        )

    assert result.all_passed is True
    assert len(recording_gateway.requests) == 1
    request = recording_gateway.requests[0]
    assert request.purpose == "acceptance"
    assert request.effective_contract == effective
    assert request.shell_command.script == "echo ok"
    assert request.command_grant.source == "acceptance-contract"
    assert request.command_grant.scope == "acceptance"
    assert request.timeout_seconds == 7
    assert request.workspace_root == tmp_path.resolve()
