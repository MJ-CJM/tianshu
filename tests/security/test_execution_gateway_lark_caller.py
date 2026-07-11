"""lark_cli must preserve its tool schema while using ExecutionGateway."""

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
from tianshu.models.governance_contract import ObjectiveV1, RequestedGovernanceContractV1
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.tools import lark_cli as lark_module


class _RecordingGateway:
    def __init__(self) -> None:
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        now = datetime.now(UTC)
        return gateway.ExecutionResult(
            stdout='{"ok":true}\n',
            stderr="",
            receipt=gateway.ExecutionReceipt(
                execution_id=request.execution_id,
                correlation_id=request.correlation_id,
                actor_id=request.actor.id,
                purpose=request.purpose,
                effective_contract_hash=request.effective_contract_hash,
                workspace_lease_id=request.workspace_lease_id,
                cwd=request.cwd,
                command_kind="argv",
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
                stdout_bytes=12,
                stderr_bytes=0,
                stdout_truncated=False,
                stderr_truncated=False,
            ),
        )


@pytest.mark.asyncio
async def test_lark_cli_uses_injected_gateway_without_direct_spawn(tmp_path, monkeypatch):
    effective = resolve_governance_contract(
        RequestedGovernanceContractV1(objective=ObjectiveV1(goal="read Lark data")),
        native_manifest(),
        probe_host_capabilities(),
    )
    context = gateway.ExecutionContext(
        correlation_id="memorial-lark",
        actor=Principal(
            id="principal-lark",
            kind=PrincipalKind.HUMAN,
            display_name="Lark Principal",
        ),
        effective_contract=effective,
        workspace_lease_id="legacy-workspace",
    )
    recording_gateway = _RecordingGateway()
    monkeypatch.setattr(lark_module, "_resolve_bin", lambda: "/opt/bin/lark-cli")

    async def _direct_spawn_forbidden(*_args, **_kwargs):
        raise AssertionError("lark_cli bypassed ExecutionGateway")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _direct_spawn_forbidden)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", _direct_spawn_forbidden)

    with gateway.bind_execution_context(context):
        result = await lark_module.lark_cli(
            ["message", "list", "--format", "json"],
            execution_gateway=recording_gateway,
            workspace_root=tmp_path,
        )

    assert result.is_error is False
    assert result.content == '{"ok":true}\n'
    assert result.details == {
        "exit_code": 0,
        "truncated": False,
        "cmd": "message list --format json",
    }
    assert len(recording_gateway.requests) == 1
    request = recording_gateway.requests[0]
    assert request.purpose == "lark-cli"
    assert request.argv_command.argv == (
        "/opt/bin/lark-cli",
        "message",
        "list",
        "--format",
        "json",
    )
    assert request.shell_command is None
    assert request.command_grant.source == "system-adapter"
    assert request.command_grant.scope == "lark-cli"
    assert request.workspace_root == tmp_path.resolve()
