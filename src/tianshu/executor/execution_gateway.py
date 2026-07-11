"""Single governed boundary for external processes and arbitrary commands."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import signal
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, NoReturn, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ulid import ULID

from tianshu.models.governance_contract import AcceptanceCheckV1, EffectiveGovernanceContractV1
from tianshu.models.principal import Principal
from tianshu.security.bash_analysis import analyze_command
from tianshu.security.clean_env import SAFE_ENV_VARS, build_clean_env
from tianshu.security.redact import redact_text


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArgvCommand(_StrictModel):
    argv: tuple[str, ...]

    @field_validator("argv", mode="before")
    @classmethod
    def validate_argv(cls, values: Any) -> tuple[str, ...]:
        argv = tuple(values or ())
        if not argv or any(
            not isinstance(value, str) or not value or "\x00" in value for value in argv
        ):
            raise ValueError("argv must contain non-empty strings without NUL bytes")
        return argv


class ShellCommand(_StrictModel):
    script: str = Field(min_length=1)
    interpreter: tuple[str, ...] = ("bash", "--noprofile", "--norc")

    @field_validator("script")
    @classmethod
    def validate_script(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("shell script cannot contain NUL bytes")
        return value

    @field_validator("interpreter", mode="before")
    @classmethod
    def validate_interpreter(cls, values: Any) -> tuple[str, ...]:
        interpreter = tuple(values or ())
        if not interpreter or any(
            not isinstance(value, str) or not value or "\x00" in value for value in interpreter
        ):
            raise ValueError("shell interpreter must be explicit")
        return interpreter

    @property
    def argv(self) -> tuple[str, ...]:
        return (*self.interpreter, "-c", self.script)


def _command_digest(argv: Sequence[str]) -> str:
    payload = json.dumps(tuple(argv), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


class CommandGrant(_StrictModel):
    source: Literal[
        "effective-permissions",
        "policy-decision",
        "acceptance-contract",
        "system-adapter",
    ]
    scope: Literal["shell_exec", "tool-argv", "acceptance", "lark-cli", "keqing"] = "tool-argv"
    argv_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shell_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    authority_ref: str = Field(min_length=1)
    effective_contract_hash: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_digest_choice(self) -> Self:
        if (self.argv_digest is None) == (self.shell_digest is None):
            raise ValueError("exactly one command digest is required")
        if self.expires_at < self.issued_at:
            raise ValueError("grant expiry cannot precede issuance")
        return self


class ToolPolicyDecision(_StrictModel):
    source: Literal["before-tool-hook"] = "before-tool-hook"
    tool_name: str = Field(min_length=1)
    arguments_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_contract_hash: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    issued_at: datetime
    expires_at: datetime
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


_GRANT_SIGNING_KEY = secrets.token_bytes(32)


def _signature_payload(model: CommandGrant | ToolPolicyDecision) -> bytes:
    payload = model.model_dump(mode="json", exclude={"signature"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _sign(model: CommandGrant | ToolPolicyDecision) -> str:
    return hmac.new(_GRANT_SIGNING_KEY, _signature_payload(model), hashlib.sha256).hexdigest()


def _valid_signature(model: CommandGrant | ToolPolicyDecision) -> bool:
    return hmac.compare_digest(model.signature, _sign(model))


def _normalize_tool_arguments(arguments: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return arguments


def _tool_arguments_digest(
    tool_name: str,
    arguments: str | dict[str, Any],
) -> str:
    arguments = _normalize_tool_arguments(arguments)
    normalized = {key: value for key, value in arguments.items() if value is not None}
    payload = json.dumps(
        {"tool_name": tool_name, "arguments": normalized},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class EnvironmentSecretRef(_StrictModel):
    env_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    ref: str = Field(min_length=1)


class EnvironmentPolicy(_StrictModel):
    allow_names: tuple[str, ...] = SAFE_ENV_VARS
    secret_refs: tuple[EnvironmentSecretRef, ...] = ()

    @field_validator("allow_names", mode="before")
    @classmethod
    def normalize_names(cls, values: Any) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values or ()))


class NetworkPolicy(_StrictModel):
    mode: Literal["deny", "allowlist", "unrestricted"] = "deny"
    allowed_hosts: tuple[str, ...] = ()
    enforcement_required: bool = False

    @model_validator(mode="after")
    def validate_hosts(self) -> Self:
        if self.mode == "allowlist" and not self.allowed_hosts:
            raise ValueError("allowlist network mode requires hosts")
        if self.mode != "allowlist" and self.allowed_hosts:
            raise ValueError("network hosts are only valid in allowlist mode")
        return self


class SandboxRequirement(_StrictModel):
    trust_level: Literal["trusted-local", "secure-remote"]
    mode: Literal["host", "preferred", "required"]
    allow_host: bool = False
    backend: str | None = None


class GuardDecision(_StrictModel):
    outcome: Literal["allow", "deny", "abstain"]
    code: str = Field(min_length=1)
    detail: str = Field(min_length=1)

    @classmethod
    def allow(cls) -> Self:
        return cls(outcome="allow", code="allowed", detail="guard allowed execution")

    @classmethod
    def deny(cls, *, code: str, detail: str) -> Self:
        return cls(outcome="deny", code=code, detail=detail)

    @classmethod
    def abstain(cls, *, code: str, detail: str) -> Self:
        return cls(outcome="abstain", code=code, detail=detail)


class GuardGap(_StrictModel):
    guard: str
    code: str
    detail: str


class ExecutionContext(_StrictModel):
    correlation_id: str = Field(min_length=1)
    actor: Principal
    effective_contract: EffectiveGovernanceContractV1
    workspace_lease_id: str = Field(min_length=1)


_current_execution_context: ContextVar[ExecutionContext | None] = ContextVar(
    "current_execution_context",
    default=None,
)


@contextmanager
def bind_execution_context(context: ExecutionContext) -> Iterator[None]:
    token = _current_execution_context.set(context)
    try:
        yield
    finally:
        _current_execution_context.reset(token)


def get_execution_context() -> ExecutionContext | None:
    return _current_execution_context.get()


_current_tool_policy_decision: ContextVar[ToolPolicyDecision | None] = ContextVar(
    "current_tool_policy_decision",
    default=None,
)


@contextmanager
def bind_tool_policy_decision(decision: ToolPolicyDecision) -> Iterator[None]:
    token = _current_tool_policy_decision.set(decision)
    try:
        yield
    finally:
        _current_tool_policy_decision.reset(token)


def _require_execution_context() -> ExecutionContext:
    context = get_execution_context()
    if context is None:
        raise ExecutionDenied(
            "command_grant",
            "missing_authority_context",
            "command grants require a run-bound execution context",
        )
    return context


def _issue_tool_policy_decision(
    tool_name: str,
    arguments: str | dict[str, Any],
) -> ToolPolicyDecision:
    """Create the short-lived proof emitted after the before-tool hook chain allows."""

    context = _require_execution_context()
    issued_at = datetime.now(UTC)
    unsigned = ToolPolicyDecision(
        tool_name=tool_name,
        arguments_digest=_tool_arguments_digest(tool_name, arguments),
        effective_contract_hash=context.effective_contract.content_hash,
        correlation_id=context.correlation_id,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(seconds=30),
        signature="0" * 64,
    )
    return unsigned.model_copy(update={"signature": _sign(unsigned)})


def _mint_command_grant(
    *,
    source: Literal[
        "effective-permissions",
        "policy-decision",
        "acceptance-contract",
        "system-adapter",
    ],
    scope: Literal["shell_exec", "tool-argv", "acceptance", "lark-cli", "keqing"],
    authority_ref: str,
    argv: Sequence[str] | None = None,
    script: str | None = None,
    expires_at: datetime | None = None,
) -> CommandGrant:
    context = _require_execution_context()
    issued_at = datetime.now(UTC)
    unsigned = CommandGrant(
        source=source,
        scope=scope,
        argv_digest=_command_digest(argv) if argv is not None else None,
        shell_digest=hashlib.sha256(script.encode()).hexdigest() if script is not None else None,
        authority_ref=authority_ref,
        effective_contract_hash=context.effective_contract.content_hash,
        correlation_id=context.correlation_id,
        issued_at=issued_at,
        expires_at=expires_at or issued_at + timedelta(seconds=30),
        signature="0" * 64,
    )
    return unsigned.model_copy(update={"signature": _sign(unsigned)})


def _validated_bound_policy_decision(
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolPolicyDecision:
    context = _require_execution_context()
    decision = _current_tool_policy_decision.get()
    now = datetime.now(UTC)
    if (
        decision is None
        or not _valid_signature(decision)
        or decision.source != "before-tool-hook"
        or decision.tool_name != tool_name
        or decision.arguments_digest != _tool_arguments_digest(tool_name, arguments)
        or decision.effective_contract_hash != context.effective_contract.content_hash
        or decision.correlation_id != context.correlation_id
        or decision.issued_at > now + timedelta(seconds=1)
        or decision.expires_at <= now
    ):
        raise ExecutionDenied(
            "command_grant",
            "policy_decision_missing",
            "no valid independent policy decision covers this tool call",
        )
    return decision


def issue_shell_command_grant(command: str, *, cwd: str | None = None) -> CommandGrant:
    context = _require_execution_context()
    analysis = analyze_command(command)
    prefixes = context.effective_contract.permissions.allowed_bash_prefixes
    prefix_match = bool(prefixes) and all(
        any(segment.startswith(prefix) for prefix in prefixes) for segment in analysis.segments
    )
    if prefix_match:
        return _mint_command_grant(
            source="effective-permissions",
            scope="shell_exec",
            authority_ref=context.effective_contract.permissions.content_hash,
            script=command,
        )
    decision = _validated_bound_policy_decision(
        "shell_exec",
        {"command": command, "cwd": cwd},
    )
    return _mint_command_grant(
        source="policy-decision",
        scope="shell_exec",
        authority_ref=decision.signature,
        script=command,
        expires_at=decision.expires_at,
    )


def _issue_tool_argv_grant(
    tool_name: str,
    arguments: dict[str, Any],
    argv: Sequence[str],
) -> CommandGrant:
    """Issue an argv grant only from a separately bound tool-policy decision."""

    decision = _validated_bound_policy_decision(tool_name, arguments)
    return _mint_command_grant(
        source="policy-decision",
        scope="tool-argv",
        authority_ref=decision.signature,
        argv=argv,
        expires_at=decision.expires_at,
    )


def issue_acceptance_command_grant(
    check: AcceptanceCheckV1,
) -> CommandGrant:
    context = _require_execution_context()
    frozen_hashes = {frozen.content_hash for frozen in context.effective_contract.acceptance.checks}
    if (
        check.kind not in {"bash", "lint"}
        or check.command is None
        or check.content_hash not in frozen_hashes
    ):
        raise ExecutionDenied(
            "command_grant",
            "acceptance_not_frozen",
            "acceptance command is not frozen in the effective contract",
        )
    return _mint_command_grant(
        source="acceptance-contract",
        scope="acceptance",
        authority_ref=check.content_hash,
        script=check.command,
    )


def issue_lark_cli_command_grant(argv: Sequence[str]) -> CommandGrant:
    if not argv or Path(argv[0]).name not in {"lark-cli", "lark-cli.exe"}:
        raise ExecutionDenied(
            "command_grant",
            "lark_cli_executable_mismatch",
            "the lark-cli authority only grants the lark-cli executable",
        )
    return _mint_command_grant(
        source="system-adapter",
        scope="lark-cli",
        authority_ref="lark-cli",
        argv=argv,
    )


def issue_keqing_command_grant(argv: Sequence[str], *, backend: str) -> CommandGrant:
    from tianshu.executor.keqing.adapter import is_canonical_adapter_argv

    context = _require_execution_context()
    if (
        context.effective_contract.executor.adapter_id != f"keqing:{backend}"
        or not is_canonical_adapter_argv(backend, argv)
    ):
        raise ExecutionDenied(
            "command_grant",
            "keqing_adapter_mismatch",
            "the argv is not covered by the selected Keqing adapter",
        )
    return _mint_command_grant(
        source="system-adapter",
        scope="keqing",
        authority_ref=f"keqing:{backend}",
        argv=argv,
    )


class ExecutionRequest(_StrictModel):
    schema_version: Literal["1"] = "1"
    execution_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    actor: Principal
    purpose: Literal["tool", "acceptance", "lark-cli", "keqing"]
    effective_contract: EffectiveGovernanceContractV1
    argv_command: ArgvCommand | None = None
    shell_command: ShellCommand | None = None
    workspace_lease_id: str = Field(min_length=1)
    workspace_root: Path = Field(exclude=True, repr=False)
    cwd: str = "."
    environment: EnvironmentPolicy
    network: NetworkPolicy
    timeout_seconds: float = Field(gt=0)
    stdout_limit_bytes: int = Field(gt=0)
    stderr_limit_bytes: int = Field(gt=0)
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


def request_for_current_execution(
    *,
    purpose: Literal["tool", "acceptance", "lark-cli", "keqing"],
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


class ExecutionReceipt(_StrictModel):
    schema_version: Literal["1"] = "1"
    execution_id: str
    correlation_id: str
    actor_id: str
    purpose: str
    effective_contract_hash: str
    workspace_lease_id: str
    cwd: str
    command_kind: Literal["argv", "shell"]
    executable: str
    env_keys: tuple[str, ...]
    secret_refs: tuple[str, ...]
    network_mode: str
    sandbox_mode: str
    sandbox_enforced: bool
    backend_id: str = "unknown"
    network_enforced: bool = False
    status: Literal["succeeded", "failed", "timed_out", "cancelled"]
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    exit_code: int | None
    terminating_signal: int | None
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    advisory_gaps: tuple[GuardGap, ...] = ()


class ExecutionResult(_StrictModel):
    stdout: str
    stderr: str
    receipt: ExecutionReceipt
    error: str | None = None

    @property
    def returncode(self) -> int | None:
        if self.receipt.exit_code is not None:
            return self.receipt.exit_code
        if self.receipt.terminating_signal is not None:
            return -self.receipt.terminating_signal
        return None


class ExecutionDenied(RuntimeError):
    def __init__(self, guard: str, code: str, detail: str) -> None:
        self.guard = guard
        self.code = code
        self.detail = redact_text(detail)
        super().__init__(f"{guard}: {code}: {self.detail}")


class ExecutionStartError(RuntimeError):
    pass


class ExecutionGuard(Protocol):
    name: str

    async def evaluate(self, request: ExecutionRequest) -> GuardDecision: ...


class ProcessBackend(Protocol):
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
    ) -> SpawnedProcess:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        return SpawnedProcess(
            process=process,
            backend_id=self.backend_id,
            network_enforced=False,
            sandbox_enforced=False,
        )


async def _drain_stream(
    stream: asyncio.StreamReader | None,
    limit: int,
    queue: asyncio.Queue[bytes | None] | None = None,
    secret_values: tuple[str, ...] = (),
) -> tuple[bytes, int, bool]:
    if stream is None:
        if queue is not None:
            queue.put_nowait(None)
        return b"", 0, False
    kept = bytearray()
    total = 0
    stream_redactor = _SecretStreamRedactor(secret_values)
    while chunk := await stream.read(65536):
        total += len(chunk)
        remaining = limit - len(kept)
        if remaining > 0:
            bounded_chunk = chunk[:remaining]
            kept.extend(bounded_chunk)
            if queue is not None:
                redacted = stream_redactor.feed(bounded_chunk)
                if redacted:
                    queue.put_nowait(redacted)
    if queue is not None:
        final = stream_redactor.finish()
        if final:
            queue.put_nowait(final)
        queue.put_nowait(None)
    return bytes(kept), total, total > limit


class _SecretStreamRedactor:
    def __init__(self, values: tuple[str, ...]) -> None:
        self._secrets = tuple(value.encode() for value in values if value)
        self._tail = bytearray()
        self._overlap = max((len(secret) - 1 for secret in self._secrets), default=0)

    def feed(self, chunk: bytes) -> bytes:
        self._tail.extend(chunk)
        self._tail = bytearray(self._replace(bytes(self._tail)))
        emit_length = max(0, len(self._tail) - self._overlap)
        if emit_length == 0:
            return b""
        data = bytes(self._tail[:emit_length])
        del self._tail[:emit_length]
        return self._replace(data)

    def finish(self) -> bytes:
        data = bytes(self._tail)
        self._tail.clear()
        return self._replace(data)

    def _replace(self, data: bytes) -> bytes:
        for secret in self._secrets:
            data = data.replace(secret, b"[REDACTED SECRET]")
        return data


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
        self._stdout_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stdout_task = asyncio.create_task(
            _drain_stream(
                process.stdout,
                request.stdout_limit_bytes,
                self._stdout_queue,
                secret_values,
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
                _, stdout_data, stderr_data = await asyncio.shield(completion)
            except asyncio.CancelledError:
                await asyncio.shield(
                    _terminate_process_tree(
                        self._process,
                        process_group_id=self._process_group_id,
                        grace_seconds=self._termination_grace_seconds,
                    )
                )
                await asyncio.shield(completion)
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
            if self._result is not None:
                return self._result
            await _terminate_process_tree(
                self._process,
                process_group_id=self._process_group_id,
                grace_seconds=self._termination_grace_seconds,
            )
            stdout_data, stderr_data = await asyncio.gather(
                self._stdout_task,
                self._stderr_task,
            )
            self._result = self._build_result(
                stdout_data,
                stderr_data,
                timed_out=False,
                cancelled=True,
            )
            return self._result

    async def iter_stdout(self) -> AsyncIterator[str]:
        while True:
            chunk = await self._stdout_queue.get()
            if chunk is None:
                return
            yield self._redact(chunk.decode(errors="replace"))

    def _redact(self, value: str) -> str:
        for secret in self._secret_values:
            if secret:
                value = value.replace(secret, "[REDACTED SECRET]")
        return redact_text(value)

    def _build_result(
        self,
        stdout_record: tuple[bytes, int, bool],
        stderr_record: tuple[bytes, int, bool],
        *,
        timed_out: bool,
        cancelled: bool,
    ) -> ExecutionResult:
        stdout_data, stdout_bytes, stdout_truncated = stdout_record
        stderr_data, stderr_bytes, stderr_truncated = stderr_record
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
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
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


class ExecutionGateway:
    def __init__(
        self,
        *,
        backend: ProcessBackend | None = None,
        mandatory_guards: Sequence[ExecutionGuard] = (),
        advisory_guards: Sequence[ExecutionGuard] = (),
        guard_timeout_seconds: float = 1.0,
        termination_grace_seconds: float = 0.5,
    ) -> None:
        self._backend = backend or AsyncioProcessBackend()
        self._mandatory_guards = tuple(mandatory_guards)
        self._advisory_guards = tuple(advisory_guards)
        self._guard_timeout_seconds = guard_timeout_seconds
        self._termination_grace_seconds = termination_grace_seconds

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        return await (await self.start(request)).wait()

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        cwd, built_in_gaps = self._validate_built_in_guards(request)
        env, secret_refs, secret_values = self._build_environment(request)
        custom_gaps = await self._evaluate_custom_guards(request, secret_values)
        started_at = datetime.now(UTC)
        started_monotonic = time.monotonic()
        try:
            spawned = await self._backend.spawn(
                argv=request.command_argv,
                cwd=cwd,
                env=env,
                network=request.network,
                sandbox=request.sandbox,
            )
        except (OSError, ValueError) as exc:
            detail = self._redact_exception(str(exc), secret_values)
            raise ExecutionStartError(detail) from None
        sandbox_enforced = bool(getattr(spawned, "sandbox_enforced", False))
        if request.sandbox.mode == "required" and not sandbox_enforced:
            await _terminate_process_tree(
                spawned.process,
                process_group_id=spawned.process.pid,
                grace_seconds=self._termination_grace_seconds,
            )
            self._deny(
                "sandbox",
                "backend_enforcement_unproven",
                "backend did not prove required per-process sandbox enforcement",
            )
        if request.network.mode != "unrestricted" and not spawned.network_enforced:
            await _terminate_process_tree(
                spawned.process,
                process_group_id=spawned.process.pid,
                grace_seconds=self._termination_grace_seconds,
            )
            self._deny(
                "network",
                "backend_enforcement_unproven",
                "backend did not prove restrictive network enforcement",
            )
        return ExecutionHandle(
            request=request,
            process=spawned.process,
            env_keys=tuple(sorted(env)),
            secret_refs=secret_refs,
            secret_values=secret_values,
            advisory_gaps=(*built_in_gaps, *custom_gaps),
            sandbox_enforced=sandbox_enforced,
            network_enforced=spawned.network_enforced,
            backend_id=spawned.backend_id,
            started_at=started_at,
            started_monotonic=started_monotonic,
            termination_grace_seconds=self._termination_grace_seconds,
        )

    def _validate_built_in_guards(
        self,
        request: ExecutionRequest,
    ) -> tuple[Path, tuple[GuardGap, ...]]:
        if request.timeout_seconds > request.effective_contract.budget.wall_clock_seconds:
            self._deny("identity_contract", "timeout_exceeds_contract", "timeout exceeds contract")

        root = request.workspace_root.resolve()
        cwd = (root / request.cwd).resolve()
        if not cwd.is_relative_to(root) or not cwd.is_dir():
            self._deny("cwd_boundary", "cwd_escape", "cwd is outside the supplied workspace")

        grant = request.command_grant
        if grant is None:
            self._deny("command_grant", "missing_grant", "command has no bound grant")
        assert grant is not None
        now = datetime.now(UTC)
        if not _valid_signature(grant):
            self._deny(
                "command_grant",
                "invalid_signature",
                "command grant was not issued by the gateway authority",
            )
        if (
            grant.effective_contract_hash != request.effective_contract.content_hash
            or grant.correlation_id != request.correlation_id
        ):
            self._deny(
                "command_grant",
                "authority_scope_mismatch",
                "command grant belongs to a different run or effective contract",
            )
        if grant.issued_at > now + timedelta(seconds=1) or grant.expires_at <= now:
            self._deny(
                "command_grant",
                "grant_expired",
                "command grant is outside its validity window",
            )
        allowed_scopes = {
            "tool": {"shell_exec", "tool-argv"},
            "acceptance": {"acceptance"},
            "lark-cli": {"lark-cli"},
            "keqing": {"keqing"},
        }[request.purpose]
        if grant.scope not in allowed_scopes:
            self._deny(
                "command_grant",
                "purpose_scope_mismatch",
                "command grant scope does not match the execution purpose",
            )
        if request.shell_command is not None:
            analysis = analyze_command(request.shell_command.script)
            if analysis.has_structural_risk:
                self._deny(
                    "bash_analysis",
                    "structural_shell_risk",
                    ", ".join(analysis.structural_notes),
                )
            script_digest = hashlib.sha256(request.shell_command.script.encode()).hexdigest()
            if grant.shell_digest != script_digest:
                self._deny(
                    "command_grant",
                    "shell_not_granted",
                    "shell command is not covered by its grant",
                )
        elif grant.argv_digest != _command_digest(request.command_argv):
            self._deny(
                "command_grant",
                "argv_not_granted",
                "argv command is not covered by its grant",
            )

        if grant.source == "effective-permissions":
            prefixes = request.effective_contract.permissions.allowed_bash_prefixes
            if grant.scope != "shell_exec" or request.shell_command is None or not prefixes:
                self._deny(
                    "command_grant",
                    "permission_source_mismatch",
                    "effective permissions do not authorize this grant scope",
                )
            analysis = analyze_command(request.shell_command.script)
            if not all(
                any(segment.startswith(prefix) for prefix in prefixes)
                for segment in analysis.segments
            ):
                self._deny(
                    "command_grant",
                    "permission_source_mismatch",
                    "shell command is outside effective allowed prefixes",
                )
        elif grant.source == "policy-decision":
            if grant.scope not in {"shell_exec", "tool-argv"} or len(grant.authority_ref) != 64:
                self._deny(
                    "command_grant",
                    "policy_source_mismatch",
                    "policy-derived grant has invalid authority metadata",
                )
        elif grant.source == "acceptance-contract":
            matching_check = any(
                check.kind in {"bash", "lint"}
                and check.command
                == (request.shell_command.script if request.shell_command else None)
                and check.content_hash == grant.authority_ref
                and request.timeout_seconds
                == min(
                    check.timeout_seconds,
                    request.effective_contract.budget.wall_clock_seconds,
                )
                for check in request.effective_contract.acceptance.checks
            )
            if grant.scope != "acceptance" or not matching_check:
                self._deny(
                    "command_grant",
                    "acceptance_source_mismatch",
                    "grant is not backed by a frozen acceptance check",
                )
        elif grant.source == "system-adapter":
            executable = Path(request.command_argv[0]).name
            if grant.scope == "lark-cli":
                valid_system_scope = grant.authority_ref == "lark-cli" and executable in {
                    "lark-cli",
                    "lark-cli.exe",
                }
            elif grant.scope == "keqing":
                from tianshu.executor.keqing.adapter import is_canonical_adapter_argv

                backend = request.effective_contract.executor.adapter_id.removeprefix("keqing:")
                valid_system_scope = (
                    grant.authority_ref == f"keqing:{backend}"
                    and request.effective_contract.executor.adapter_id.startswith("keqing:")
                    and is_canonical_adapter_argv(backend, request.command_argv)
                )
            else:
                valid_system_scope = False
            if not valid_system_scope:
                self._deny(
                    "command_grant",
                    "system_source_mismatch",
                    "system-adapter grant is outside its purpose-limited scope",
                )

        supports_sandbox = bool(getattr(self._backend, "supports_sandbox", False))
        if request.sandbox.trust_level == "secure-remote" and not supports_sandbox:
            self._deny("sandbox", "secure_remote_unavailable", "required sandbox is unavailable")
        if request.sandbox.mode == "required" and not supports_sandbox:
            self._deny("sandbox", "required_unavailable", "required sandbox is unavailable")
        if request.sandbox.mode == "host" and not request.sandbox.allow_host:
            self._deny(
                "sandbox", "host_not_explicit", "trusted-local host execution is not explicit"
            )

        gaps: list[GuardGap] = []
        effective_network = request.effective_contract.network
        effective_network_mode = (
            "unrestricted"
            if effective_network.mode == "unrestricted_requested"
            else effective_network.mode
        )
        if (
            request.network.mode != effective_network_mode
            or request.network.allowed_hosts != effective_network.allowed_hosts
            or (
                request.effective_contract.state("network_control") == "enforced"
                and not request.network.enforcement_required
            )
        ):
            self._deny(
                "network",
                "policy_downgrade",
                "request network policy does not match the effective contract",
            )
        network_supported = bool(getattr(self._backend, "supports_network_enforcement", False))
        if request.network.mode != "unrestricted" and not network_supported:
            self._deny(
                "network",
                "enforcement_unavailable",
                "network policy cannot be enforced by the selected backend",
            )

        declared_refs = set(request.effective_contract.permissions.secret_refs)
        requested_refs = {secret.ref for secret in request.environment.secret_refs}
        if not requested_refs.issubset(declared_refs):
            self._deny("environment", "secret_not_granted", "secret reference is not in contract")
        secret_env_names = {secret.env_name for secret in request.environment.secret_refs}
        for name in request.environment.allow_names:
            parts = set(name.upper().split("_"))
            if (
                name.startswith("TIANSHU_")
                or parts.intersection({"SECRET", "TOKEN", "PASSWORD", "KEY", "CREDENTIAL"})
            ) and name not in secret_env_names:
                self._deny(
                    "environment",
                    "secret_requires_reference",
                    "secret-like environment names must use a secret reference",
                )
        return cwd, tuple(gaps)

    async def _evaluate_custom_guards(
        self,
        request: ExecutionRequest,
        secret_values: tuple[str, ...],
    ) -> tuple[GuardGap, ...]:
        for guard in self._mandatory_guards:
            decision = await self._guard_decision(
                guard,
                request,
                mandatory=True,
                secret_values=secret_values,
            )
            if decision.outcome != "allow":
                self._deny(
                    guard.name,
                    decision.code,
                    self._redact_exception(decision.detail, secret_values),
                )

        gaps: list[GuardGap] = []
        for guard in self._advisory_guards:
            decision = await self._guard_decision(
                guard,
                request,
                mandatory=False,
                secret_values=secret_values,
            )
            if decision.outcome != "allow":
                gaps.append(
                    GuardGap(
                        guard=guard.name,
                        code=decision.code,
                        detail=self._redact_exception(decision.detail, secret_values),
                    )
                )
        return tuple(gaps)

    async def _guard_decision(
        self,
        guard: ExecutionGuard,
        request: ExecutionRequest,
        *,
        mandatory: bool,
        secret_values: tuple[str, ...],
    ) -> GuardDecision:
        try:
            decision = await asyncio.wait_for(
                guard.evaluate(request),
                timeout=self._guard_timeout_seconds,
            )
        except TimeoutError:
            return GuardDecision.abstain(
                code="guard_timeout",
                detail="mandatory guard timed out" if mandatory else "advisory guard timed out",
            )
        except Exception as exc:
            return GuardDecision.abstain(
                code="guard_error",
                detail=self._redact_exception(str(exc), secret_values)
                or "guard raised an exception",
            )
        if not isinstance(decision, GuardDecision):
            return GuardDecision.abstain(
                code="invalid_guard_result",
                detail="guard did not return a structured decision",
            )
        return decision

    def _build_environment(
        self,
        request: ExecutionRequest,
    ) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
        passthrough = ",".join(request.environment.allow_names)
        env = build_clean_env(passthrough)
        secret_refs: list[str] = []
        secret_values: list[str] = []
        for secret in request.environment.secret_refs:
            value = os.environ.get(secret.ref)
            if value is None:
                self._deny("environment", "secret_unavailable", "secret reference is unavailable")
            env[secret.env_name] = value
            secret_refs.append(secret.ref)
            secret_values.append(value)
        return env, tuple(secret_refs), tuple(secret_values)

    @staticmethod
    def _redact_exception(detail: str, secrets: tuple[str, ...]) -> str:
        for secret in secrets:
            if secret:
                detail = detail.replace(secret, "[REDACTED SECRET]")
        return redact_text(detail)

    @staticmethod
    def _deny(guard: str, code: str, detail: str) -> NoReturn:
        raise ExecutionDenied(guard, code, detail)


__all__ = [
    "ArgvCommand",
    "AsyncioProcessBackend",
    "CommandGrant",
    "EnvironmentPolicy",
    "EnvironmentSecretRef",
    "ExecutionContext",
    "ExecutionDenied",
    "ExecutionGateway",
    "ExecutionGuard",
    "ExecutionHandle",
    "ExecutionReceipt",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStartError",
    "GuardDecision",
    "GuardGap",
    "NetworkPolicy",
    "ProcessBackend",
    "SandboxRequirement",
    "ShellCommand",
    "SpawnedProcess",
    "ToolPolicyDecision",
    "bind_execution_context",
    "bind_tool_policy_decision",
    "get_execution_context",
    "issue_acceptance_command_grant",
    "issue_keqing_command_grant",
    "issue_lark_cli_command_grant",
    "issue_shell_command_grant",
    "request_for_current_execution",
]
