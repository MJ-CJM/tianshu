"""ExecutionRequest 构造、进程后端、流处理与执行句柄 —— 从 execution_gateway 单文件拆出，符号原文零改动。"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, Self

from pydantic import Field, field_validator, model_validator
from ulid import ULID

from tianshu.models.governance_contract import EffectiveGovernanceContractV1
from tianshu.models.principal import Principal
from tianshu.security.redact import redact_text

from .grants import get_execution_context
from .policy_models import (
    ArgvCommand,
    CommandGrant,
    EnvironmentPolicy,
    ExecutionDenied,
    ExecutionReceipt,
    ExecutionResult,
    GuardDecision,
    GuardGap,
    NetworkPolicy,
    SandboxRequirement,
    ShellCommand,
    _StrictModel,
)


class ExecutionRequest(_StrictModel):
    schema_version: Literal["1"] = "1"
    execution_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    actor: Principal
    purpose: Literal[
        "tool",
        "acceptance",
        "grep",
        "lsp",
        "lark-cli",
        "keqing",
        "mcp_stdio",
        "universe_gate",
        "universe_sandbox",
    ]
    mcp_server_name: str | None = None
    universe_stage: str | None = None
    effective_contract: EffectiveGovernanceContractV1
    argv_command: ArgvCommand | None = None
    shell_command: ShellCommand | None = None
    workspace_lease_id: str | None = Field(default=None, min_length=1)
    workspace_root: Path = Field(exclude=True, repr=False)
    cwd: str = "."
    environment: EnvironmentPolicy
    network: NetworkPolicy
    timeout_seconds: float = Field(gt=0)
    stdout_limit_bytes: int = Field(gt=0)
    stderr_limit_bytes: int = Field(gt=0)
    stdin_mode: Literal["null", "pipe"] = "null"
    stdin_write_limit_bytes: int = Field(default=64 * 1024, gt=0)
    sandbox: SandboxRequirement
    command_grant: CommandGrant | None

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("relative cwd must remain under the workspace root")
        return value or "."

    @model_validator(mode="after")
    def validate_command_choice(self) -> Self:
        if (self.argv_command is None) == (self.shell_command is None):
            raise ValueError("exactly one of argv_command and shell_command is required")
        if (self.purpose == "mcp_stdio") != (self.mcp_server_name is not None):
            raise ValueError("MCP server name is required only for MCP stdio execution")
        universe_purpose = self.purpose in {"universe_gate", "universe_sandbox"}
        if universe_purpose != (self.universe_stage is not None):
            raise ValueError("Universe stage is required only for Universe execution")
        if self.purpose == "universe_gate" and not str(self.universe_stage).startswith("gate:"):
            raise ValueError("Universe gate purpose requires a gate stage")
        if self.purpose == "universe_sandbox" and self.universe_stage != "sandbox:serve":
            raise ValueError("Universe sandbox purpose requires sandbox:serve stage")
        return self

    @property
    def effective_contract_hash(self) -> str:
        return self.effective_contract.content_hash

    @property
    def command_argv(self) -> tuple[str, ...]:
        if self.argv_command is not None:
            return self.argv_command.argv
        assert self.shell_command is not None
        return self.shell_command.argv


def _validate_current_workspace_binding(
    *,
    correlation_id: str,
    effective_contract: EffectiveGovernanceContractV1,
    workspace_lease_id: str | None,
    workspace_root: Path,
) -> None:
    from tianshu.executor.workspace_context import (
        WorkspaceBindingError,
        get_bound_workspace,
        require_bound_workspace,
        requires_workspace_binding,
    )

    workspace = get_bound_workspace()
    if workspace is None and not requires_workspace_binding(effective_contract):
        return
    if workspace_lease_id is None:
        raise ExecutionDenied(
            "identity_contract",
            "workspace_binding_mismatch",
            "bound workspace lease id is missing from execution context",
        )
    try:
        require_bound_workspace(
            run_id=correlation_id,
            lease_id=workspace_lease_id,
            effective_contract_hash=effective_contract.content_hash,
            root=workspace_root,
        )
    except WorkspaceBindingError as exc:
        raise ExecutionDenied(
            "identity_contract",
            "workspace_binding_mismatch",
            str(exc),
        ) from None


def request_for_current_execution(
    *,
    purpose: Literal["tool", "acceptance", "grep", "lsp", "lark-cli", "keqing"],
    workspace_root: Path,
    cwd: str,
    argv_command: ArgvCommand | None = None,
    shell_command: ShellCommand | None = None,
    environment: EnvironmentPolicy,
    timeout_seconds: float,
    stdout_limit_bytes: int,
    stderr_limit_bytes: int,
    sandbox: SandboxRequirement,
    command_grant: CommandGrant,
) -> ExecutionRequest:
    context = get_execution_context()
    if context is None:
        raise ExecutionDenied(
            "identity_contract",
            "missing_execution_context",
            "no run-bound actor and effective contract are available",
        )
    _validate_current_workspace_binding(
        correlation_id=context.correlation_id,
        effective_contract=context.effective_contract,
        workspace_lease_id=context.workspace_lease_id,
        workspace_root=workspace_root,
    )
    effective_network = context.effective_contract.network
    network_mode = (
        "unrestricted"
        if effective_network.mode == "unrestricted_requested"
        else effective_network.mode
    )
    return ExecutionRequest(
        execution_id=str(ULID()),
        correlation_id=context.correlation_id,
        actor=context.actor,
        purpose=purpose,
        effective_contract=context.effective_contract,
        argv_command=argv_command,
        shell_command=shell_command,
        workspace_lease_id=context.workspace_lease_id,
        workspace_root=workspace_root.resolve(),
        cwd=cwd,
        environment=environment,
        network=NetworkPolicy(
            mode=network_mode,
            allowed_hosts=effective_network.allowed_hosts,
            enforcement_required=(
                context.effective_contract.state("network_control") == "enforced"
            ),
        ),
        timeout_seconds=min(
            timeout_seconds,
            context.effective_contract.budget.wall_clock_seconds,
        ),
        stdout_limit_bytes=stdout_limit_bytes,
        stderr_limit_bytes=stderr_limit_bytes,
        sandbox=sandbox,
        command_grant=command_grant,
    )


class ExecutionGuard(Protocol):
    name: str

    async def evaluate(self, request: ExecutionRequest) -> GuardDecision: ...


class ProcessBackend(Protocol):
    """A process backend with an explicit ownership handoff.

    Backends must call ``on_spawned`` exactly once, immediately after a process
    exists and before any further await. If cancellation arrives before that
    handoff, the backend remains responsible for proving that no process exists.
    """

    backend_id: str
    supports_sandbox: bool
    supports_network_enforcement: bool

    async def spawn(self, **kwargs: Any) -> SpawnedProcess: ...


@dataclass(frozen=True)
class SpawnedProcess:
    process: asyncio.subprocess.Process
    backend_id: str
    network_enforced: bool
    sandbox_enforced: bool


class _SpawnOwnership:
    def __init__(self) -> None:
        self._published: list[SpawnedProcess] = []

    @property
    def processes(self) -> tuple[SpawnedProcess, ...]:
        return tuple(self._published)

    @property
    def primary(self) -> SpawnedProcess | None:
        return self._published[0] if self._published else None

    def publish(self, spawned: SpawnedProcess) -> None:
        if not isinstance(spawned, SpawnedProcess):
            raise TypeError("process backend published an invalid ownership record")
        self._published.append(spawned)
        if len(self._published) != 1:
            raise RuntimeError("process backend published ownership more than once")

    def accept_return(self, spawned: SpawnedProcess) -> None:
        if not isinstance(spawned, SpawnedProcess):
            raise TypeError("process backend returned an invalid ownership record")
        if not self._published:
            self._published.append(spawned)
            return
        published = self._published[0]
        if published is spawned:
            return
        if published.process is spawned.process:
            self._published[0] = spawned
            return
        self._published.append(spawned)
        raise RuntimeError("process backend returned a different process than it published")


class AsyncioProcessBackend:
    """The only low-level process launcher used by the gateway."""

    backend_id = "asyncio-host"
    supports_sandbox = False
    supports_network_enforcement = False

    async def spawn(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        env: dict[str, str],
        network: NetworkPolicy,
        sandbox: SandboxRequirement,
        stdin_mode: Literal["null", "pipe"],
        on_spawned: Callable[[SpawnedProcess], None],
    ) -> SpawnedProcess:
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=env,
                stdin=(
                    asyncio.subprocess.PIPE if stdin_mode == "pipe" else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name != "nt",
            ),
            name="execution-process-acquisition",
        )
        cancellation: asyncio.CancelledError | None = None
        while True:
            try:
                process = await asyncio.shield(creation)
                break
            except asyncio.CancelledError as exc:
                cancellation = exc
                continue
        spawned = SpawnedProcess(
            process=process,
            backend_id=self.backend_id,
            network_enforced=False,
            sandbox_enforced=False,
        )
        on_spawned(spawned)
        if cancellation is not None:
            raise cancellation
        return spawned


@dataclass(frozen=True)
class _StreamRecord:
    data: bytes
    total_bytes: int
    truncated: bool
    incomplete: bool = False


async def _drain_stream(
    stream: asyncio.StreamReader | None,
    limit: int,
    queue: asyncio.Queue[bytes | None] | None = None,
    secret_values: tuple[str, ...] = (),
    stream_all: bool = False,
) -> _StreamRecord:
    if stream is None:
        if queue is not None:
            await queue.put(None)
        return _StreamRecord(data=b"", total_bytes=0, truncated=False)
    kept = bytearray()
    total = 0
    incomplete = False
    stream_redactor = _SecretStreamRedactor(secret_values)
    try:
        while chunk := await stream.read(65536):
            total += len(chunk)
            remaining = limit - len(kept)
            if remaining > 0:
                bounded_chunk = chunk[:remaining]
                kept.extend(bounded_chunk)
            if queue is not None:
                stream_chunk = chunk if stream_all else chunk[: max(0, remaining)]
                if stream_chunk:
                    redacted = stream_redactor.feed(stream_chunk)
                    if redacted:
                        await queue.put(redacted)
        if queue is not None:
            final = stream_redactor.finish()
            if final:
                await queue.put(final)
            await queue.put(None)
    except asyncio.CancelledError:
        incomplete = True
        if queue is not None:
            while not queue.empty():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(None)
    return _StreamRecord(
        data=bytes(kept),
        total_bytes=total,
        truncated=total > limit or incomplete,
        incomplete=incomplete,
    )


class _SecretStreamRedactor:
    def __init__(self, values: tuple[str, ...]) -> None:
        self._secrets = tuple(value.encode() for value in values if value)
        self._tail = bytearray()

    def feed(self, chunk: bytes) -> bytes:
        self._tail.extend(chunk)
        self._tail = bytearray(self._replace(bytes(self._tail)))
        emit_length = len(self._tail) - self._pending_prefix_length(bytes(self._tail))
        if emit_length == 0:
            return b""
        data = bytes(self._tail[:emit_length])
        del self._tail[:emit_length]
        return data

    def finish(self) -> bytes:
        data = bytes(self._tail)
        self._tail.clear()
        return self._replace(data)

    def _replace(self, data: bytes) -> bytes:
        for secret in self._secrets:
            data = data.replace(secret, b"[REDACTED]")
        return data

    def _pending_prefix_length(self, data: bytes) -> int:
        pending = 0
        for secret in self._secrets:
            max_length = min(len(data), len(secret) - 1)
            for length in range(max_length, pending, -1):
                if data.endswith(secret[:length]):
                    pending = length
                    break
        return pending


async def _terminate_process_tree(
    process: asyncio.subprocess.Process,
    *,
    process_group_id: int,
    grace_seconds: float,
) -> None:
    if os.name != "nt":
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            try:
                os.killpg(process_group_id, 0)
            except (PermissionError, ProcessLookupError):
                break
            await asyncio.sleep(0.01)
        else:
            with suppress(ProcessLookupError):
                os.killpg(process_group_id, signal.SIGKILL)
        if process.returncode is None:
            await process.wait()
        return

    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        return
    except TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()


class ExecutionHandle:
    def __init__(
        self,
        *,
        request: ExecutionRequest,
        process: asyncio.subprocess.Process,
        env_keys: tuple[str, ...],
        secret_refs: tuple[str, ...],
        secret_values: tuple[str, ...],
        advisory_gaps: tuple[GuardGap, ...],
        sandbox_enforced: bool,
        network_enforced: bool,
        backend_id: str,
        started_at: datetime,
        started_monotonic: float,
        termination_grace_seconds: float,
    ) -> None:
        self.request = request
        self._process = process
        self._env_keys = env_keys
        self._secret_refs = secret_refs
        self._secret_values = secret_values
        self._advisory_gaps = advisory_gaps
        self._sandbox_enforced = sandbox_enforced
        self._network_enforced = network_enforced
        self._backend_id = backend_id
        self._process_group_id = process.pid
        self._started_at = started_at
        self._started_monotonic = started_monotonic
        self._termination_grace_seconds = termination_grace_seconds
        self._stdout_queue: asyncio.Queue[bytes | None] = asyncio.Queue(
            maxsize=8 if request.stdin_mode == "pipe" else 0
        )
        self._stdout_task = asyncio.create_task(
            _drain_stream(
                process.stdout,
                request.stdout_limit_bytes,
                self._stdout_queue,
                secret_values,
                request.stdin_mode == "pipe",
            )
        )
        self._stderr_task = asyncio.create_task(
            _drain_stream(process.stderr, request.stderr_limit_bytes)
        )
        self._result: ExecutionResult | None = None
        self._wait_lock = asyncio.Lock()

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    async def write_stdin(self, data: bytes) -> None:
        if self.request.stdin_mode != "pipe" or self._process.stdin is None:
            raise RuntimeError("execution stdin is not configured as a pipe")
        if len(data) > self.request.stdin_write_limit_bytes:
            raise ValueError(
                f"stdin write exceeds {self.request.stdin_write_limit_bytes} byte limit"
            )
        self._process.stdin.write(data)
        remaining = self.request.timeout_seconds - (time.monotonic() - self._started_monotonic)
        if remaining <= 0:
            raise TimeoutError("execution stdin deadline exceeded")
        await asyncio.wait_for(self._process.stdin.drain(), timeout=remaining)

    async def close_stdin(self) -> None:
        stdin = self._process.stdin
        if stdin is None or stdin.is_closing():
            return
        stdin.close()
        with suppress(BrokenPipeError, ConnectionResetError):
            await stdin.wait_closed()

    async def wait(self) -> ExecutionResult:
        async with self._wait_lock:
            if self._result is not None:
                return self._result
            timed_out = False
            completion = asyncio.gather(
                self._process.wait(),
                self._stdout_task,
                self._stderr_task,
            )
            remaining = max(
                0.0,
                self.request.timeout_seconds - (time.monotonic() - self._started_monotonic),
            )
            try:
                _, stdout_data, stderr_data = await asyncio.wait_for(
                    asyncio.shield(completion),
                    timeout=remaining,
                )
            except TimeoutError:
                timed_out = True
                await _terminate_process_tree(
                    self._process,
                    process_group_id=self._process_group_id,
                    grace_seconds=self._termination_grace_seconds,
                )
                stdout_data, stderr_data = await self._finish_stream_drains()
            except asyncio.CancelledError:
                await asyncio.shield(
                    _terminate_process_tree(
                        self._process,
                        process_group_id=self._process_group_id,
                        grace_seconds=self._termination_grace_seconds,
                    )
                )
                await asyncio.shield(self._finish_stream_drains())
                raise
            self._result = self._build_result(
                stdout_data,
                stderr_data,
                timed_out=timed_out,
                cancelled=False,
            )
            return self._result

    async def terminate(self) -> ExecutionResult:
        async with self._wait_lock:
            await _terminate_process_tree(
                self._process,
                process_group_id=self._process_group_id,
                grace_seconds=self._termination_grace_seconds,
            )
            if self._result is not None:
                return self._result
            stdout_data, stderr_data = await self._finish_stream_drains()
            self._result = self._build_result(
                stdout_data,
                stderr_data,
                timed_out=False,
                cancelled=True,
            )
            return self._result

    async def _finish_stream_drains(self) -> tuple[_StreamRecord, _StreamRecord]:
        tasks = (self._stdout_task, self._stderr_task)
        _, pending = await asyncio.wait(
            tasks,
            timeout=max(self._termination_grace_seconds, 0.05),
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        def record(task: asyncio.Task[_StreamRecord]) -> _StreamRecord:
            if task.cancelled():
                return _StreamRecord(
                    data=b"",
                    total_bytes=0,
                    truncated=True,
                    incomplete=True,
                )
            result = task.exception()
            if result is not None:
                return _StreamRecord(
                    data=b"",
                    total_bytes=0,
                    truncated=True,
                    incomplete=True,
                )
            return task.result()

        return record(self._stdout_task), record(self._stderr_task)

    async def iter_stdout(self) -> AsyncIterator[str]:
        async for chunk in self.iter_stdout_bytes():
            yield self._redact(chunk.decode(errors="replace"))

    async def iter_stdout_bytes(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._stdout_queue.get()
            if chunk is None:
                return
            yield chunk

    def _redact(self, value: str) -> str:
        for secret in self._secret_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return redact_text(value)

    def _build_result(
        self,
        stdout_record: _StreamRecord,
        stderr_record: _StreamRecord,
        *,
        timed_out: bool,
        cancelled: bool,
    ) -> ExecutionResult:
        stdout_data = stdout_record.data
        stderr_data = stderr_record.data
        returncode = self._process.returncode
        terminating_signal = -returncode if returncode is not None and returncode < 0 else None
        exit_code = returncode if returncode is not None and returncode >= 0 else None
        status: Literal["succeeded", "failed", "timed_out", "cancelled"]
        if cancelled:
            status = "cancelled"
        elif timed_out:
            status = "timed_out"
        elif returncode == 0:
            status = "succeeded"
        else:
            status = "failed"
        finished_at = datetime.now(UTC)
        command_kind: Literal["argv", "shell"] = (
            "argv" if self.request.argv_command is not None else "shell"
        )
        receipt = ExecutionReceipt(
            execution_id=self.request.execution_id,
            correlation_id=self.request.correlation_id,
            actor_id=self.request.actor.id,
            purpose=self.request.purpose,
            mcp_server_name=self.request.mcp_server_name,
            universe_stage=self.request.universe_stage,
            command_admission=(
                "transitional_mcp_config_g1_6_pending"
                if self.request.purpose == "mcp_stdio"
                else "standard"
            ),
            effective_contract_hash=self.request.effective_contract_hash,
            workspace_lease_id=self.request.workspace_lease_id,
            cwd=self.request.cwd,
            command_kind=command_kind,
            executable=self.request.command_argv[0],
            env_keys=self._env_keys,
            secret_refs=self._secret_refs,
            network_mode=self.request.network.mode,
            sandbox_mode=self.request.sandbox.mode,
            sandbox_enforced=self._sandbox_enforced,
            backend_id=self._backend_id,
            network_enforced=self._network_enforced,
            status=status,
            started_at=self._started_at,
            finished_at=finished_at,
            duration_ms=int((time.monotonic() - self._started_monotonic) * 1000),
            exit_code=exit_code,
            terminating_signal=terminating_signal,
            stdout_bytes=stdout_record.total_bytes,
            stderr_bytes=stderr_record.total_bytes,
            stdout_truncated=stdout_record.truncated,
            stderr_truncated=stderr_record.truncated,
            stdout_incomplete=stdout_record.incomplete,
            stderr_incomplete=stderr_record.incomplete,
            advisory_gaps=self._advisory_gaps,
        )
        error = None
        if timed_out:
            error = f"execution timed out after {self.request.timeout_seconds:g}s"
        elif cancelled:
            error = "execution cancelled"
        return ExecutionResult(
            stdout=self._redact(stdout_data.decode(errors="replace")),
            stderr=self._redact(stderr_data.decode(errors="replace")),
            receipt=receipt,
            error=error,
        )
