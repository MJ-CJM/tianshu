"""Lean MCP admission must fail closed before any runtime capability is exposed."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import (
    ArgvCommand,
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
from tianshu.tools.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    MCPServerOverride,
    ToolFilter,
    merge_overrides,
)
from tianshu.tools.mcp.manager import MCPManager
from tianshu.tools.registry import ToolRegistry


class _NoSpawnBackend:
    backend_id = "no-spawn"
    supports_sandbox = False
    supports_network_enforcement = False

    def __init__(self) -> None:
        self.spawned = False

    async def spawn(self, **_: object) -> None:
        self.spawned = True
        raise AssertionError("revoked MCP command must not spawn")


class _RecordingExecutionGateway(ExecutionGateway):
    def __init__(self, backend: _NoSpawnBackend) -> None:
        self.configurations: list[dict[str, tuple[str, ...]]] = []
        super().__init__(backend=backend)

    def configure_mcp_stdio_commands(
        self,
        commands: Mapping[str, Sequence[str]],
    ) -> None:
        self.configurations.append({name: tuple(argv) for name, argv in commands.items()})
        super().configure_mcp_stdio_commands(commands)


def _execution_context():
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="test stale MCP command revocation"),
        network=NetworkPolicyV1(mode="unrestricted_requested"),
    )
    contract = resolve_governance_contract(
        requested,
        native_manifest(),
        probe_host_capabilities(),
    )
    return ExecutionContext(
        correlation_id="mcp-stale-command",
        actor=Principal(
            id="system:mcp",
            kind=PrincipalKind.SERVICE,
            display_name="MCP Manager",
        ),
        effective_contract=contract,
        workspace_lease_id="system:mcp",
    )


def _execution_request(tmp_path, context: ExecutionContext, grant, argv):
    return ExecutionRequest(
        execution_id="mcp-stale-execution",
        correlation_id=context.correlation_id,
        actor=context.actor,
        purpose="mcp_stdio",
        mcp_server_name="demo",
        effective_contract=context.effective_contract,
        argv_command=ArgvCommand(argv=argv),
        workspace_lease_id=context.workspace_lease_id,
        workspace_root=tmp_path,
        cwd=".",
        environment=EnvironmentPolicy(),
        network=NetworkPolicy(mode="unrestricted"),
        timeout_seconds=3,
        stdout_limit_bytes=4096,
        stderr_limit_bytes=4096,
        stdin_mode="pipe",
        stdin_write_limit_bytes=4096,
        sandbox=SandboxRequirement(
            trust_level="trusted-local",
            mode="host",
            allow_host=True,
        ),
        command_grant=grant,
    )


def _stdio(
    name: str,
    *,
    enabled: bool = True,
    include: list[str] | None = None,
) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command="SENSITIVE_COMMAND_MUST_NOT_BE_AUDITED",
        enabled=enabled,
        tools=ToolFilter(include=include or []),
    )


def _remote(name: str, *, enabled: bool = True) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="streamable_http",
        url="https://SECRET_REMOTE_URL_MUST_NOT_BE_AUDITED.invalid/mcp",
        enabled=enabled,
    )


def _manager(
    *,
    security_mode: str = "trusted-local",
    allowlist: str | None = None,
    storage: object | None = None,
) -> tuple[MCPManager, ToolRegistry, MagicMock]:
    registry = ToolRegistry()
    gateway = MagicMock()
    manager = MCPManager(
        registry,
        gateway,
        security_mode=security_mode,  # type: ignore[arg-type]
        allowlist=allowlist,
        storage=storage,
    )
    return manager, registry, gateway


def test_db_only_override_without_enabled_is_disabled() -> None:
    merged = merge_overrides(
        MCPConfig(),
        [
            MCPServerOverride(
                name="db-only",
                transport="stdio",
                command="python",
                tools_include=["approved"],
            )
        ],
    )

    assert merged.mcp_servers["db-only"].enabled is False


@pytest.mark.parametrize(
    ("config", "security_mode", "allowlist", "reason_code"),
    [
        (
            _stdio("disabled", enabled=False, include=["approved"]),
            "trusted-local",
            None,
            "disabled",
        ),
        (_remote("remote"), "secure-remote", None, "trusted_egress_unavailable"),
        (_stdio("unapproved"), "trusted-local", None, "approved_tools_required"),
        (
            _stdio("not-listed", include=["approved"]),
            "trusted-local",
            "another-server",
            "server_not_allowlisted",
        ),
        (_stdio("admitted", include=["approved"]), "trusted-local", None, "admitted"),
    ],
)
def test_admission_decision_has_one_exact_reason_code(
    config: MCPServerConfig,
    security_mode: str,
    allowlist: str | None,
    reason_code: str,
) -> None:
    manager, _, _ = _manager(security_mode=security_mode, allowlist=allowlist)

    decision = manager._admission_for(config)

    assert decision.allowed is (reason_code == "admitted")
    assert decision.reason_code == reason_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "security_mode", "allowlist", "reason_code"),
    [
        (_remote("blocked-remote"), "secure-remote", None, "trusted_egress_unavailable"),
        (_stdio("empty-include"), "trusted-local", None, "approved_tools_required"),
        (
            _stdio("not-allowlisted", include=["approved"]),
            "trusted-local",
            "different-server",
            "server_not_allowlisted",
        ),
    ],
)
async def test_denied_server_never_reaches_session_gateway_readiness_or_registry(
    monkeypatch: pytest.MonkeyPatch,
    storage,
    config: MCPServerConfig,
    security_mode: str,
    allowlist: str | None,
    reason_code: str,
) -> None:
    constructed: list[str] = []

    class _SessionThatWouldExposeATool:
        def __init__(self, *, config: MCPServerConfig, **_: object) -> None:
            constructed.append(config.name)
            self.config = config
            self.tools = [
                SimpleNamespace(
                    name="dangerous",
                    description="must stay hidden",
                    input_schema={"type": "object", "properties": {}},
                )
            ]
            self.terminal_receipt = None

        async def start(self) -> bool:
            return True

        async def shutdown(self) -> None:
            return None

    monkeypatch.setattr(
        "tianshu.tools.mcp.manager.MCPServerSession",
        _SessionThatWouldExposeATool,
    )
    manager, registry, gateway = _manager(
        security_mode=security_mode,
        allowlist=allowlist,
        storage=storage,
    )
    manager._config = MCPConfig(mcp_servers={config.name: config})

    await manager.start()

    assert manager.admitted_enabled_names == ()
    assert manager.sessions == {}
    assert constructed == []
    gateway.configure_mcp_stdio_commands.assert_not_called()
    assert not any(
        definition.name.startswith(f"mcp_{config.name}_")
        for definition in registry.list_definitions()
    )

    denied = [
        event for event in storage.list_system_audit() if event.action == "mcp.admission.denied"
    ]
    assert len(denied) == 1
    event = denied[0]
    assert event.outcome == "denied"
    assert event.reason_code == reason_code
    assert event.subject_digest == hashlib.sha256(config.name.encode()).hexdigest()
    assert event.metadata == {}
    serialized = repr(event.model_dump(mode="json"))
    for forbidden in (config.name, config.command, config.url):
        if forbidden:
            assert forbidden not in serialized


def test_registration_rechecks_the_same_admission_decision() -> None:
    manager, registry, _ = _manager(security_mode="secure-remote")
    config = _remote("late-remote")
    session = SimpleNamespace(
        config=config,
        tools=[
            SimpleNamespace(
                name="late-tool",
                description="must stay hidden",
                input_schema={"type": "object", "properties": {}},
            )
        ],
    )

    count = manager._register_session_tools(session)

    assert count == 0
    assert registry.get_definition("mcp_late-remote_late-tool") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denied_config",
    [
        _stdio("demo", enabled=False, include=["approved"]),
        _stdio("demo"),
        _stdio("not-allowlisted", include=["approved"]),
    ],
    ids=["disabled", "empty-include", "allowlist-denied"],
)
async def test_hot_reload_revokes_previous_stdio_gateway_command_and_grant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    denied_config: MCPServerConfig,
) -> None:
    class _ConnectedSession:
        def __init__(self, *, config: MCPServerConfig, **_: object) -> None:
            self.config = config
            self.tools = [
                SimpleNamespace(
                    name="approved",
                    description="approved fixture tool",
                    input_schema={"type": "object", "properties": {}},
                )
            ]
            self.terminal_receipt = None

        async def start(self) -> bool:
            return True

        async def shutdown(self) -> None:
            return None

    monkeypatch.setattr("tianshu.tools.mcp.manager.MCPServerSession", _ConnectedSession)
    backend = _NoSpawnBackend()
    gateway = ExecutionGateway(backend=backend)
    registry = ToolRegistry()
    manager = MCPManager(
        registry,
        gateway,
        allowlist="demo",
    )
    argv = (sys.executable, "-c", "pass")
    manager._config = MCPConfig(
        mcp_servers={
            "demo": MCPServerConfig(
                name="demo",
                transport="stdio",
                command=argv[0],
                args=list(argv[1:]),
                tools=ToolFilter(include=["approved"]),
            )
        }
    )
    await manager.start()
    context = _execution_context()
    with bind_execution_context(context):
        old_grant = gateway.issue_mcp_stdio_command_grant("demo", argv)
    assert manager.sessions.keys() == {"demo"}
    assert registry.get_definition("mcp_demo_approved") is not None

    manager._config = MCPConfig(mcp_servers={denied_config.name: denied_config})
    from tianshu.gateway.mcp_api import _restart_mcp_sessions

    await _restart_mcp_sessions(manager, registry)

    assert manager.sessions == {}
    assert not any(definition.name.startswith("mcp_") for definition in registry.list_definitions())
    with (
        bind_execution_context(context),
        pytest.raises(ExecutionDenied, match="mcp_command_not_configured"),
    ):
        gateway.issue_mcp_stdio_command_grant("demo", argv)
    with pytest.raises(ExecutionDenied):
        await gateway.start(_execution_request(tmp_path, context, old_grant, argv))
    assert backend.spawned is False


@pytest.mark.asyncio
async def test_shutdown_revokes_stdio_command_before_waiting_for_live_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    shutdown_entered = asyncio.Event()
    release_shutdown = asyncio.Event()

    class _BlockingSession:
        def __init__(self, *, config: MCPServerConfig, **_: object) -> None:
            self.config = config
            self.tools = []
            self.terminal_receipt = None

        async def start(self) -> bool:
            return True

        async def shutdown(self) -> None:
            shutdown_entered.set()
            await release_shutdown.wait()

    monkeypatch.setattr("tianshu.tools.mcp.manager.MCPServerSession", _BlockingSession)
    backend = _NoSpawnBackend()
    gateway = _RecordingExecutionGateway(backend)
    manager = MCPManager(
        ToolRegistry(),
        gateway,
        allowlist="demo,next",
    )
    argv = (sys.executable, "-c", "pass")
    manager._config = MCPConfig(
        mcp_servers={
            "demo": MCPServerConfig(
                name="demo",
                transport="stdio",
                command=argv[0],
                args=list(argv[1:]),
                tools=ToolFilter(include=["approved"]),
            )
        }
    )
    await manager.start()
    context = _execution_context()
    with bind_execution_context(context):
        old_grant = gateway.issue_mcp_stdio_command_grant("demo", argv)
    empty_configurations_before = gateway.configurations.count({})

    second_shutdown_started = asyncio.Event()
    shutdown_calls = 0
    original_shutdown = manager.shutdown

    async def tracked_shutdown() -> None:
        nonlocal shutdown_calls
        shutdown_calls += 1
        if shutdown_calls == 2:
            second_shutdown_started.set()
        await original_shutdown()

    monkeypatch.setattr(manager, "shutdown", tracked_shutdown)

    first_shutdown = asyncio.create_task(manager.shutdown())
    await shutdown_entered.wait()
    second_shutdown = asyncio.create_task(manager.shutdown())
    await second_shutdown_started.wait()
    assert first_shutdown.done() is False
    assert second_shutdown.done() is False
    try:
        with pytest.raises(ExecutionDenied):
            await gateway.start(_execution_request(tmp_path, context, old_grant, argv))
        assert backend.spawned is False
    finally:
        release_shutdown.set()
        await asyncio.gather(first_shutdown, second_shutdown)

    assert gateway.configurations.count({}) == empty_configurations_before + 1

    next_argv = (sys.executable, "-c", "pass # next")
    manager._config = MCPConfig(
        mcp_servers={
            "next": MCPServerConfig(
                name="next",
                transport="stdio",
                command=next_argv[0],
                args=list(next_argv[1:]),
                tools=ToolFilter(include=["approved"]),
            )
        }
    )
    await manager.start()
    with bind_execution_context(context):
        gateway.issue_mcp_stdio_command_grant("next", next_argv)
        with pytest.raises(ExecutionDenied, match="mcp_command_not_configured"):
            gateway.issue_mcp_stdio_command_grant("demo", argv)
    await manager.shutdown()
