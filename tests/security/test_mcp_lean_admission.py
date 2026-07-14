"""Lean MCP admission must fail closed before any runtime capability is exposed."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tianshu.tools.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    MCPServerOverride,
    ToolFilter,
    merge_overrides,
)
from tianshu.tools.mcp.manager import MCPManager
from tianshu.tools.registry import ToolRegistry


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
