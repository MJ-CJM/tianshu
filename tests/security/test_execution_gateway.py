"""ExecutionGateway request validation and fail-closed guard semantics."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.models.governance_contract import (
    NetworkPolicyV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind


def _gateway_module():
    try:
        return importlib.import_module("tianshu.executor.execution_gateway")
    except ModuleNotFoundError:
        pytest.fail("ExecutionGateway module is missing", pytrace=False)


@pytest.fixture(scope="module")
def effective_contract():
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="exercise the execution boundary"),
        network=NetworkPolicyV1(mode="unrestricted_requested"),
    )
    return resolve_governance_contract(
        requested,
        native_manifest(),
        probe_host_capabilities(),
    )


@pytest.fixture
def request_data(tmp_path: Path, effective_contract):
    gateway = _gateway_module()
    argv = (sys.executable, "-c", "print('ok')")
    return {
        "execution_id": "exec-1",
        "correlation_id": "corr-1",
        "actor": Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Test Principal",
            scopes=frozenset({"api"}),
        ),
        "purpose": "tool",
        "effective_contract": effective_contract,
        "argv_command": gateway.ArgvCommand(argv=argv),
        "workspace_lease_id": "legacy-workspace",
        "workspace_root": tmp_path,
        "cwd": ".",
        "environment": gateway.EnvironmentPolicy(),
        "network": gateway.NetworkPolicy(mode="unrestricted"),
        "timeout_seconds": 2.0,
        "stdout_limit_bytes": 4096,
        "stderr_limit_bytes": 4096,
        "sandbox": gateway.SandboxRequirement(
            trust_level="trusted-local",
            mode="host",
            allow_host=True,
        ),
        "command_grant": gateway.CommandGrant.for_argv(
            argv,
            source="tool-policy",
        ),
    }


def test_request_is_strict_immutable_and_requires_actor_and_contract(request_data):
    gateway = _gateway_module()

    request = gateway.ExecutionRequest(**request_data)
    with pytest.raises(ValidationError):
        request.timeout_seconds = 10
    with pytest.raises(ValidationError):
        gateway.ExecutionRequest(**request_data, unexpected=True)

    missing_actor = dict(request_data)
    missing_actor.pop("actor")
    with pytest.raises(ValidationError):
        gateway.ExecutionRequest(**missing_actor)

    missing_contract = dict(request_data)
    missing_contract.pop("effective_contract")
    with pytest.raises(ValidationError):
        gateway.ExecutionRequest(**missing_contract)


def test_request_requires_exactly_one_of_argv_and_shell(request_data):
    gateway = _gateway_module()

    neither = dict(request_data)
    neither.pop("argv_command")
    with pytest.raises(ValidationError, match="exactly one"):
        gateway.ExecutionRequest(**neither)

    both = dict(request_data)
    both["shell_command"] = gateway.ShellCommand(script="echo ok")
    with pytest.raises(ValidationError, match="exactly one"):
        gateway.ExecutionRequest(**both)


def test_request_rejects_parent_cwd_traversal(request_data):
    gateway = _gateway_module()

    request_data["cwd"] = "../outside"
    with pytest.raises(ValidationError, match="relative cwd"):
        gateway.ExecutionRequest(**request_data)


@pytest.mark.asyncio
async def test_gateway_rejects_symlink_cwd_escape_before_spawn(request_data, tmp_path):
    gateway = _gateway_module()
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    request_data["cwd"] = "escape"
    request = gateway.ExecutionRequest(**request_data)

    backend = _RecordingBackend()
    with pytest.raises(gateway.ExecutionDenied, match="cwd_boundary"):
        await gateway.ExecutionGateway(backend=backend).run(request)
    assert backend.spawn_calls == []


@pytest.mark.asyncio
async def test_gateway_rejects_missing_command_grant_before_spawn(request_data):
    gateway = _gateway_module()
    request_data["command_grant"] = None
    request = gateway.ExecutionRequest(**request_data)
    backend = _RecordingBackend()

    with pytest.raises(gateway.ExecutionDenied, match="command_grant"):
        await gateway.ExecutionGateway(backend=backend).run(request)
    assert backend.spawn_calls == []


@pytest.mark.asyncio
async def test_shell_grant_checks_every_segment_and_bash_structure(request_data):
    gateway = _gateway_module()
    request_data.pop("argv_command")
    request_data["shell_command"] = gateway.ShellCommand(script="echo ok; uname -a")
    request_data["command_grant"] = gateway.CommandGrant.for_shell_prefixes(
        ("echo ",),
        source="tool-policy",
    )
    backend = _RecordingBackend()

    with pytest.raises(gateway.ExecutionDenied, match="command_grant"):
        await gateway.ExecutionGateway(backend=backend).run(
            gateway.ExecutionRequest(**request_data)
        )
    assert backend.spawn_calls == []

    request_data["shell_command"] = gateway.ShellCommand(script="echo $(whoami)")
    request_data["command_grant"] = gateway.CommandGrant.for_shell_prefixes(
        ("echo ",),
        source="tool-policy",
    )
    with pytest.raises(gateway.ExecutionDenied, match="bash_analysis"):
        await gateway.ExecutionGateway(backend=backend).run(
            gateway.ExecutionRequest(**request_data)
        )
    assert backend.spawn_calls == []


class _ExplodingGuard:
    name = "exploding_guard"

    async def evaluate(self, _request):
        raise RuntimeError("guard implementation failed")


class _SlowGuard:
    name = "slow_guard"

    async def evaluate(self, _request):
        await __import__("asyncio").sleep(1)
        raise AssertionError("unreachable")


class _UnavailableAdvisoryGuard:
    name = "optional_scanner"

    async def evaluate(self, _request):
        gateway = _gateway_module()
        return gateway.GuardDecision.abstain(
            code="scanner_unavailable",
            detail="optional scanner is not installed",
        )


class _RecordingBackend:
    def __init__(
        self,
        *,
        supports_sandbox: bool = False,
        supports_network_enforcement: bool = False,
    ) -> None:
        self.supports_sandbox = supports_sandbox
        self.supports_network_enforcement = supports_network_enforcement
        self.spawn_calls: list[dict] = []

    async def spawn(self, **kwargs):
        self.spawn_calls.append(kwargs)
        raise AssertionError("backend spawn must not be reached")


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", [_ExplodingGuard(), _SlowGuard()])
async def test_mandatory_guard_exception_or_timeout_fails_closed(
    guard,
    request_data,
):
    gateway = _gateway_module()
    backend = _RecordingBackend()
    request = gateway.ExecutionRequest(**request_data)

    with pytest.raises(gateway.ExecutionDenied, match=guard.name):
        await gateway.ExecutionGateway(
            backend=backend,
            mandatory_guards=(guard,),
            guard_timeout_seconds=0.01,
        ).run(request)
    assert backend.spawn_calls == []


@pytest.mark.asyncio
async def test_advisory_abstention_is_recorded_as_structured_gap(request_data):
    gateway = _gateway_module()
    request = gateway.ExecutionRequest(**request_data)

    result = await gateway.ExecutionGateway(
        advisory_guards=(_UnavailableAdvisoryGuard(),),
    ).run(request)

    assert result.receipt.status == "succeeded"
    assert result.stdout == "ok\n"
    assert [gap.code for gap in result.receipt.advisory_gaps] == ["scanner_unavailable"]
    assert result.receipt.advisory_gaps[0].guard == "optional_scanner"


@pytest.mark.asyncio
async def test_secure_remote_required_sandbox_never_falls_back_to_host(request_data):
    gateway = _gateway_module()
    request_data["sandbox"] = gateway.SandboxRequirement(
        trust_level="secure-remote",
        mode="required",
        allow_host=False,
    )
    request = gateway.ExecutionRequest(**request_data)
    backend = _RecordingBackend(supports_sandbox=False)

    with pytest.raises(gateway.ExecutionDenied, match="sandbox"):
        await gateway.ExecutionGateway(backend=backend).run(request)
    assert backend.spawn_calls == []


@pytest.mark.asyncio
async def test_trusted_local_host_execution_must_be_explicit(request_data):
    gateway = _gateway_module()
    request_data["sandbox"] = gateway.SandboxRequirement(
        trust_level="trusted-local",
        mode="host",
        allow_host=False,
    )
    request = gateway.ExecutionRequest(**request_data)
    backend = _RecordingBackend()

    with pytest.raises(gateway.ExecutionDenied, match="sandbox"):
        await gateway.ExecutionGateway(backend=backend).run(request)
    assert backend.spawn_calls == []


@pytest.mark.asyncio
async def test_request_cannot_downgrade_effective_network_policy(request_data):
    gateway = _gateway_module()
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="deny network"),
        network=NetworkPolicyV1(mode="deny"),
    )
    request_data["effective_contract"] = resolve_governance_contract(
        requested,
        native_manifest(),
        probe_host_capabilities(),
    )
    request_data["network"] = gateway.NetworkPolicy(mode="unrestricted")
    request = gateway.ExecutionRequest(**request_data)
    backend = _RecordingBackend()

    with pytest.raises(gateway.ExecutionDenied, match="network"):
        await gateway.ExecutionGateway(backend=backend).run(request)
    assert backend.spawn_calls == []
