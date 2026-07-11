"""MCP transport factory with governed stdio process ownership."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import anyio
from ulid import ULID

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import (
    ArgvCommand,
    CommandGrant,
    EnvironmentPolicy,
    EnvironmentSecretRef,
    ExecutionContext,
    ExecutionGateway,
    ExecutionReceipt,
    ExecutionRequest,
    ExecutionStartError,
    NetworkPolicy,
    SandboxRequirement,
    bind_execution_context,
)
from tianshu.models.governance_contract import (
    BudgetPolicyV1,
    NetworkPolicyV1,
    ObjectiveV1,
    PermissionPolicyV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.tools.mcp.config import MCPServerConfig

if TYPE_CHECKING:
    from mcp import ClientSession

logger = logging.getLogger(__name__)

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MCP_STDIO_LIMIT_BYTES = 1024 * 1024


@asynccontextmanager
async def open_session(
    config: MCPServerConfig,
    *,
    execution_gateway: ExecutionGateway | None = None,
    workspace_root: Path | None = None,
    security_mode: Literal["trusted-local", "secure-remote"] = "trusted-local",
    receipt_callback: Callable[[ExecutionReceipt], None] | None = None,
    closed_event: asyncio.Event | None = None,
) -> AsyncIterator[ClientSession]:
    """Open one MCP client session while keeping HTTP behavior unchanged."""
    if config.transport == "stdio":
        if execution_gateway is None or workspace_root is None:
            raise TypeError("stdio MCP requires the bootstrapped ExecutionGateway and workspace")
        async with _open_stdio(
            config,
            execution_gateway=execution_gateway,
            workspace_root=workspace_root,
            security_mode=security_mode,
            receipt_callback=receipt_callback,
            closed_event=closed_event,
        ) as session:
            yield session
    elif config.transport == "streamable_http":
        async with _open_streamable_http(config.with_env_interpolated()) as session:
            yield session
    else:  # pragma: no cover - Pydantic restricts the transport literal
        raise ValueError(f"unknown transport: {config.transport!r}")


@asynccontextmanager
async def _open_stdio(
    cfg: MCPServerConfig,
    *,
    execution_gateway: ExecutionGateway,
    workspace_root: Path,
    security_mode: Literal["trusted-local", "secure-remote"],
    receipt_callback: Callable[[ExecutionReceipt], None] | None,
    closed_event: asyncio.Event | None,
) -> AsyncIterator[ClientSession]:
    from mcp import ClientSession, types
    from mcp.shared.message import SessionMessage

    assert cfg.command is not None
    argv = (cfg.command, *cfg.args)
    environment = _stdio_environment_policy(cfg)
    context = _stdio_execution_context(cfg, environment, security_mode)
    with bind_execution_context(context):
        grant = execution_gateway.issue_mcp_stdio_command_grant(cfg.name, argv)
    request = _stdio_execution_request(
        cfg,
        argv=argv,
        environment=environment,
        context=context,
        grant=grant,
        workspace_root=workspace_root,
        security_mode=security_mode,
    )
    try:
        handle = await execution_gateway.start(request)
    except ExecutionStartError as exc:
        if receipt_callback is not None:
            receipt_callback(exc.receipt)
        raise

    read_sender, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_receiver = anyio.create_memory_object_stream[SessionMessage](0)

    async def stdout_reader() -> None:
        buffer = b""
        try:
            async with read_sender:
                async for chunk in handle.iter_stdout_bytes():
                    lines = (buffer + chunk).split(b"\n")
                    buffer = lines.pop()
                    for line in lines:
                        if not line:
                            continue
                        try:
                            message = types.JSONRPCMessage.model_validate_json(line)
                        except Exception as exc:
                            await read_sender.send(exc)
                            return
                        else:
                            await read_sender.send(SessionMessage(message))
                if buffer.strip():
                    try:
                        message = types.JSONRPCMessage.model_validate_json(buffer)
                    except Exception as exc:
                        await read_sender.send(exc)
                        return
                    else:
                        await read_sender.send(SessionMessage(message))
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def stdin_writer() -> None:
        try:
            async with write_receiver:
                async for session_message in write_receiver:
                    payload = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    ).encode()
                    await handle.write_stdin(payload + b"\n")
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    stdout_task = asyncio.create_task(stdout_reader(), name=f"mcp-stdout-{cfg.name}")
    stdin_task = asyncio.create_task(stdin_writer(), name=f"mcp-stdin-{cfg.name}")
    wait_task = asyncio.create_task(handle.wait(), name=f"mcp-process-{cfg.name}")
    if closed_event is not None:
        wait_task.add_done_callback(lambda _task: closed_event.set())

    async def cleanup() -> None:
        with suppress(Exception):
            await write_stream.aclose()
        stdin_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await stdin_task
        if not wait_task.done():
            wait_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await wait_task
        result = await handle.terminate()
        with suppress(asyncio.TimeoutError, Exception):
            await asyncio.wait_for(stdout_task, timeout=1)
        if not stdout_task.done():
            stdout_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await stdout_task
        for stream in (read_stream, read_sender, write_stream, write_receiver):
            with suppress(Exception):
                await stream.aclose()
        if receipt_callback is not None:
            receipt_callback(result.receipt)

    try:
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=cfg.timeout),
        ) as session:
            await session.initialize()
            yield session
    finally:
        cleanup_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await asyncio.shield(cleanup_task)
            raise


def _stdio_environment_policy(cfg: MCPServerConfig) -> EnvironmentPolicy:
    refs: list[EnvironmentSecretRef] = []
    for env_name, configured_value in cfg.env.items():
        match = _ENV_REF.fullmatch(configured_value)
        if match is None:
            raise ValueError(f"MCP stdio env {env_name!r} must be one environment reference")
        refs.append(EnvironmentSecretRef(env_name=env_name, ref=match.group(1)))
    return EnvironmentPolicy(secret_refs=tuple(refs))


def _stdio_execution_context(
    cfg: MCPServerConfig,
    environment: EnvironmentPolicy,
    security_mode: Literal["trusted-local", "secure-remote"],
) -> ExecutionContext:
    secret_refs = tuple(ref.ref for ref in environment.secret_refs)
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal=f"Run configured MCP stdio server {cfg.name}"),
        permissions=PermissionPolicyV1(secret_refs=secret_refs),
        network=NetworkPolicyV1(
            mode=("unrestricted_requested" if security_mode == "trusted-local" else "deny")
        ),
        budget=BudgetPolicyV1(wall_clock_seconds=cfg.timeout),
    )
    effective = resolve_governance_contract(
        requested,
        native_manifest(),
        probe_host_capabilities(),
    )
    correlation_id = f"mcp:{cfg.name}:{ULID()}"
    return ExecutionContext(
        correlation_id=correlation_id,
        actor=Principal(
            id="system:mcp",
            kind=PrincipalKind.SERVICE,
            display_name="MCP Manager",
        ),
        effective_contract=effective,
        workspace_lease_id=f"system:mcp:{cfg.name}",
    )


def _stdio_execution_request(
    cfg: MCPServerConfig,
    *,
    argv: tuple[str, ...],
    environment: EnvironmentPolicy,
    context: ExecutionContext,
    grant: CommandGrant,
    workspace_root: Path,
    security_mode: Literal["trusted-local", "secure-remote"],
) -> ExecutionRequest:
    restrictive = security_mode == "secure-remote"
    return ExecutionRequest(
        execution_id=str(ULID()),
        correlation_id=context.correlation_id,
        actor=context.actor,
        purpose="mcp_stdio",
        mcp_server_name=cfg.name,
        effective_contract=context.effective_contract,
        argv_command=ArgvCommand(argv=argv),
        workspace_lease_id=context.workspace_lease_id,
        workspace_root=workspace_root.resolve(),
        cwd=".",
        environment=environment,
        network=NetworkPolicy(mode="deny" if restrictive else "unrestricted"),
        timeout_seconds=cfg.timeout,
        stdout_limit_bytes=_MCP_STDIO_LIMIT_BYTES,
        stderr_limit_bytes=_MCP_STDIO_LIMIT_BYTES,
        stdin_mode="pipe",
        stdin_write_limit_bytes=_MCP_STDIO_LIMIT_BYTES,
        sandbox=SandboxRequirement(
            trust_level=security_mode,
            mode="required" if restrictive else "host",
            allow_host=not restrictive,
        ),
        command_grant=grant,
    )


@asynccontextmanager
async def _open_streamable_http(
    cfg: MCPServerConfig,
) -> AsyncIterator[ClientSession]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    assert cfg.url is not None
    headers = dict(cfg.headers) if cfg.headers else None
    async with (
        streamablehttp_client(
            cfg.url,
            headers=headers,
            timeout=float(cfg.connect_timeout),
            sse_read_timeout=float(cfg.timeout),
        ) as (read_stream, write_stream, _get_session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session
