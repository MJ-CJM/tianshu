"""Governed warm probe for the Pi RPC transport contract."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path
from typing import Any

from ulid import ULID

from tianshu.executor.capabilities import (
    pi_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import (
    ArgvCommand,
    EnvironmentPolicy,
    ExecutionContext,
    ExecutionDenied,
    ExecutionGateway,
    ExecutionStartCancelled,
    ExecutionStartError,
    SandboxRequirement,
    bind_execution_context,
    issue_keqing_command_grant,
    request_for_current_execution,
)
from tianshu.executor.keqing.pi_adapter import PiSessionAdapter
from tianshu.executor.keqing.pi_wire import VERIFIED_SESSION_VERSION
from tianshu.executor.keqing.versions import (
    ExecutableInspectionError,
    resolve_execution_executable,
)
from tianshu.models.governance_contract import (
    BudgetPolicyV1,
    ExecutorSelectionV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind


async def verify_pi_rpc_contract(
    execution_gateway: ExecutionGateway,
    *,
    workspace_root: Path,
    binary_path: str | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[bool, str | None]:
    """Verify Pi header and a side-effect-free management response via the gateway.

    A persisted ``binary_path`` is resolved directly and never looked up through
    ``PATH``. Passing ``None`` explicitly requests discovery of the installed Pi.
    """

    if timeout_seconds <= 0:
        return False, "invalid_timeout"
    try:
        root = workspace_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return False, "workspace_missing"
    if not root.is_dir():
        return False, "workspace_missing"

    try:
        executable = resolve_execution_executable(
            "pi" if binary_path is None else binary_path,
            backend="pi",
        )
    except ExecutableInspectionError:
        return False, "executable_invalid"

    adapter = PiSessionAdapter(binary_path=executable.binary_path)
    argv = adapter.build_session_argv()
    environment = EnvironmentPolicy()
    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="verify Pi RPC runtime contract"),
        executor=ExecutorSelectionV1(adapter_id="keqing:pi"),
        budget=BudgetPolicyV1(wall_clock_seconds=max(1, math.ceil(timeout_seconds))),
    )
    effective = resolve_governance_contract(
        requested,
        pi_manifest(),
        probe_host_capabilities(),
    )
    context = ExecutionContext(
        correlation_id=f"pi-warm-probe-{ULID()}",
        actor=Principal(
            id="system:pi-generation-probe",
            kind=PrincipalKind.SERVICE,
            display_name="Pi Generation Probe",
        ),
        effective_contract=effective,
    )

    handle: Any | None = None
    outcome: tuple[bool, str | None]
    cleanup_ok = True
    try:
        with bind_execution_context(context):
            request = request_for_current_execution(
                purpose="keqing",
                workspace_root=root,
                cwd=".",
                argv_command=ArgvCommand(
                    argv=tuple(argv),
                    executable_version=executable.version,
                    executable_version_source=executable.version_source,
                ),
                environment=environment,
                timeout_seconds=timeout_seconds,
                stdout_limit_bytes=1024 * 1024,
                stderr_limit_bytes=256 * 1024,
                sandbox=SandboxRequirement(
                    trust_level="trusted-local",
                    mode="host",
                    allow_host=True,
                ),
                command_grant=issue_keqing_command_grant(
                    argv,
                    backend="pi",
                    workspace_root=root,
                    environment=environment,
                ),
            ).model_copy(update={"stdin_mode": "pipe", "stdin_write_limit_bytes": 64 * 1024})
            async with asyncio.timeout(timeout_seconds):
                handle = await execution_gateway.start(request)
                outcome = await _probe_handle(handle, adapter)
    except TimeoutError:
        outcome = (False, "timeout")
    except ExecutionDenied:
        outcome = (False, "gateway_denied")
    except (ExecutionStartCancelled, ExecutionStartError):
        outcome = (False, "gateway_start_failed")
    except (OSError, RuntimeError, ValueError):
        outcome = (False, "probe_failed")
    finally:
        if handle is not None:
            cleanup_ok = await _cleanup_handle(handle)

    if not cleanup_ok:
        return False, "cleanup_failed"
    return outcome


async def _probe_handle(handle: Any, adapter: PiSessionAdapter) -> tuple[bool, str | None]:
    command_id = f"tianshu-probe-{ULID()}"
    await handle.write_stdin(adapter.encode_command("get_session_stats", cmd_id=command_id))

    header_seen = False
    response_seen = False
    buffer = b""
    async for chunk in handle.iter_stdout_bytes():
        buffer += chunk
        parts = buffer.split(b"\n")
        buffer = parts.pop()
        for raw in parts:
            result = _absorb_probe_frame(
                raw,
                adapter=adapter,
                command_id=command_id,
                header_seen=header_seen,
                response_seen=response_seen,
            )
            if isinstance(result, str):
                return False, result
            header_seen, response_seen = result
            if header_seen and response_seen:
                return True, None
    if buffer.strip():
        result = _absorb_probe_frame(
            buffer,
            adapter=adapter,
            command_id=command_id,
            header_seen=header_seen,
            response_seen=response_seen,
        )
        if isinstance(result, str):
            return False, result
        header_seen, response_seen = result
    if not header_seen:
        return False, "header_missing"
    if not response_seen:
        return False, "stats_response_missing"
    return True, None


def _absorb_probe_frame(
    raw: bytes,
    *,
    adapter: PiSessionAdapter,
    command_id: str,
    header_seen: bool,
    response_seen: bool,
) -> tuple[bool, bool] | str:
    frame_bytes = raw.rstrip(b"\r").strip()
    if not frame_bytes:
        return header_seen, response_seen
    try:
        frame = json.loads(frame_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "invalid_json_frame"
    if not isinstance(frame, dict):
        return "invalid_json_frame"
    if not header_seen:
        if frame.get("type") != "session":
            return "invalid_header"
        if frame.get("version") != VERIFIED_SESSION_VERSION:
            return "unsupported_wire_version"
        header_seen = True
    elif frame.get("type") == "session":
        return "duplicate_header"

    if adapter.is_response(frame) and frame.get("id") == command_id:
        if frame.get("success") is not True:
            return "stats_rejected"
        if not isinstance(frame.get("data"), dict):
            return "invalid_stats_response"
        response_seen = True
    return header_seen, response_seen


async def _cleanup_handle(handle: Any) -> bool:
    cleanup_ok = True
    try:
        await handle.close_stdin()
    except Exception:
        cleanup_ok = False
    try:
        await handle.terminate()
    except Exception:
        cleanup_ok = False
    return cleanup_ok
