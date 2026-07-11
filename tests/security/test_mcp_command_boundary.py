"""MCP stdio must use the governed process boundary."""

from __future__ import annotations

import ast
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import (
    ArgvCommand,
    CommandGrant,
    EnvironmentPolicy,
    ExecutionContext,
    ExecutionDenied,
    ExecutionGateway,
    ExecutionRequest,
    NetworkPolicy,
    SandboxRequirement,
    bind_execution_context,
)
from tianshu.models.governance_contract import (
    NetworkPolicyV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.tools.mcp import MCPManager
from tianshu.tools.mcp.config import MCPServerConfig
from tianshu.tools.mcp.transport import open_session
from tianshu.tools.registry import ToolRegistry


class _NoSpawnBackend:
    backend_id = "no-spawn"
    supports_sandbox = False
    supports_network_enforcement = False

    def __init__(self) -> None:
        self.spawned = False

    async def spawn(self, **_kwargs):
        self.spawned = True
        raise AssertionError("spawn must not be reached")


@pytest.fixture
def effective_contract():
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="run configured MCP stdio server"),
        network=NetworkPolicyV1(mode="unrestricted_requested"),
    )
    return resolve_governance_contract(
        requested,
        native_manifest(),
        probe_host_capabilities(),
    )


def _context(effective_contract, *, correlation_id: str = "mcp-correlation") -> ExecutionContext:
    return ExecutionContext(
        correlation_id=correlation_id,
        actor=Principal(
            id="system:mcp",
            kind=PrincipalKind.SERVICE,
            display_name="MCP Manager",
        ),
        effective_contract=effective_contract,
        workspace_lease_id="system:mcp",
    )


def _request(
    tmp_path: Path,
    effective_contract,
    argv: tuple[str, ...],
    grant: CommandGrant,
    *,
    server_name: str = "fixture",
    correlation_id: str = "mcp-correlation",
    sandbox: SandboxRequirement | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id="mcp-execution",
        correlation_id=correlation_id,
        actor=_context(effective_contract, correlation_id=correlation_id).actor,
        purpose="mcp_stdio",
        mcp_server_name=server_name,
        effective_contract=effective_contract,
        argv_command=ArgvCommand(argv=argv),
        workspace_lease_id="system:mcp",
        workspace_root=tmp_path,
        cwd=".",
        environment=EnvironmentPolicy(),
        network=NetworkPolicy(mode="unrestricted"),
        timeout_seconds=3,
        stdout_limit_bytes=4096,
        stderr_limit_bytes=4096,
        stdin_mode="pipe",
        stdin_write_limit_bytes=4096,
        sandbox=sandbox
        or SandboxRequirement(
            trust_level="trusted-local",
            mode="host",
            allow_host=True,
        ),
        command_grant=grant,
    )


def test_mcp_manager_requires_the_bootstrapped_gateway() -> None:
    with pytest.raises(TypeError):
        MCPManager(ToolRegistry())


@pytest.mark.asyncio
async def test_mcp_grant_is_exactly_bound_to_server_argv_contract_and_correlation(
    tmp_path: Path,
    effective_contract,
) -> None:
    argv = (sys.executable, "-c", "pass")
    gateway = ExecutionGateway(mcp_stdio_commands={"fixture": argv, "other": argv})
    with bind_execution_context(_context(effective_contract)):
        grant = gateway.issue_mcp_stdio_command_grant("fixture", argv)
        with pytest.raises(ExecutionDenied, match="mcp_command_not_configured"):
            gateway.issue_mcp_stdio_command_grant("unknown", argv)
        with pytest.raises(ExecutionDenied, match="mcp_command_not_configured"):
            gateway.issue_mcp_stdio_command_grant("fixture", (*argv, "changed"))

    assert grant.scope == "mcp_stdio"
    assert grant.server_identity == "fixture"

    cases = (
        _request(tmp_path, effective_contract, (*argv, "different"), grant),
        _request(tmp_path, effective_contract, argv, grant, server_name="other"),
        _request(
            tmp_path,
            effective_contract.model_copy(update={"runtime_probe_id": "other-probe"}),
            argv,
            grant,
        ),
        _request(
            tmp_path,
            effective_contract,
            argv,
            grant,
            correlation_id="different-correlation",
        ),
    )
    for request in cases:
        backend = _NoSpawnBackend()
        with pytest.raises(ExecutionDenied):
            await ExecutionGateway(
                backend=backend,
                mcp_stdio_commands={"fixture": argv, "other": argv},
            ).start(request)
        assert backend.spawned is False


@pytest.mark.asyncio
async def test_forged_mcp_grant_is_rejected_before_spawn(
    tmp_path: Path,
    effective_contract,
) -> None:
    argv = (sys.executable, "-c", "pass")
    forged = CommandGrant(
        source="system-adapter",
        scope="mcp_stdio",
        argv_digest="0" * 64,
        authority_ref="mcp-config:fixture",
        server_identity="fixture",
        actor_id="mcp-principal",
        effective_contract_hash=effective_contract.content_hash,
        correlation_id="mcp-correlation",
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        signature="0" * 64,
    )
    backend = _NoSpawnBackend()

    with pytest.raises(ExecutionDenied, match="invalid_signature"):
        await ExecutionGateway(
            backend=backend,
            mcp_stdio_commands={"fixture": argv},
        ).start(_request(tmp_path, effective_contract, argv, forged))
    assert backend.spawned is False


@pytest.mark.asyncio
async def test_stdio_literal_environment_value_is_rejected_before_spawn(
    tmp_path: Path,
) -> None:
    argv = (sys.executable, "-c", "pass")
    backend = _NoSpawnBackend()
    gateway = ExecutionGateway(
        backend=backend,
        mcp_stdio_commands={"fixture": argv},
    )
    cfg = MCPServerConfig(
        name="fixture",
        transport="stdio",
        command=argv[0],
        args=list(argv[1:]),
        env={"TOKEN": "literal-secret-value"},
    )

    with pytest.raises(ValueError, match="must be one environment reference"):
        async with open_session(
            cfg,
            execution_gateway=gateway,
            workspace_root=tmp_path,
        ):
            pytest.fail("literal MCP environment value reached a session")
    assert backend.spawned is False


@pytest.mark.asyncio
async def test_secure_remote_mcp_requires_available_sandbox_before_spawn(
    tmp_path: Path,
    effective_contract,
) -> None:
    argv = (sys.executable, "-c", "pass")
    backend = _NoSpawnBackend()
    gateway = ExecutionGateway(
        backend=backend,
        mcp_stdio_commands={"fixture": argv},
    )
    with bind_execution_context(_context(effective_contract)):
        grant = gateway.issue_mcp_stdio_command_grant("fixture", argv)

    with pytest.raises(ExecutionDenied, match="secure_remote_unavailable"):
        await gateway.start(
            _request(
                tmp_path,
                effective_contract,
                argv,
                grant,
                sandbox=SandboxRequirement(
                    trust_level="secure-remote",
                    mode="required",
                    allow_host=False,
                ),
            )
        )
    assert backend.spawned is False


def test_mcp_transport_does_not_call_sdk_or_process_launchers() -> None:
    transport_path = (
        Path(__file__).parents[2] / "src" / "tianshu" / "tools" / "mcp" / "transport.py"
    )
    tree = ast.parse(transport_path.read_text(encoding="utf-8"))
    forbidden = {
        "create_subprocess_exec",
        "create_subprocess_shell",
        "Popen",
        "run",
        "stdio_client",
    }
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
    }

    assert called.isdisjoint(forbidden), called & forbidden
