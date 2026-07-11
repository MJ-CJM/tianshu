"""Single governed boundary for external processes and arbitrary commands."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import shutil
import signal
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
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
    scope: Literal[
        "shell_exec",
        "tool-argv",
        "acceptance",
        "grep",
        "lsp",
        "lark-cli",
        "keqing",
        "mcp_stdio",
        "universe_gate",
        "universe_sandbox",
    ] = "tool-argv"
    argv_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    shell_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    authority_ref: str = Field(min_length=1)
    server_identity: str | None = None
    actor_id: str = Field(min_length=1)
    principal_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    universe_stage: str | None = None
    cwd: str | None = None
    environment_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    workspace_lease_id: str | None = None
    workspace_root_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    resolved_cwd_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
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
        if (self.scope == "mcp_stdio") != (self.server_identity is not None):
            raise ValueError("server identity is required only for MCP stdio grants")
        universe_scope = self.scope in {"universe_gate", "universe_sandbox"}
        workspace_capable_scope = universe_scope or self.scope in {
            "shell_exec",
            "grep",
            "lsp",
            "lark-cli",
            "keqing",
        }
        workspace_bindings = (
            self.cwd,
            self.environment_digest,
            self.workspace_lease_id,
            self.workspace_root_digest,
            self.resolved_cwd_digest,
        )
        has_workspace_binding = any(value is not None for value in workspace_bindings)
        if (
            (universe_scope and not all(value is not None for value in workspace_bindings))
            or (
                has_workspace_binding and not all(value is not None for value in workspace_bindings)
            )
            or (not workspace_capable_scope and has_workspace_binding)
        ):
            raise ValueError("workspace-bound grants require cwd, environment, and lease binding")
        if universe_scope != (self.universe_stage is not None):
            raise ValueError("Universe stage binding is valid only for Universe grants")
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
    ref: str = Field(pattern=r"^(?:[A-Za-z_][A-Za-z0-9_]*|settings:[a-z_][a-z0-9_]*)$")


class EnvironmentValue(_StrictModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str

    @field_validator("name")
    @classmethod
    def reject_secret_like_literal(cls, value: str) -> str:
        parts = set(value.upper().split("_"))
        if parts.intersection({"SECRET", "TOKEN", "PASSWORD", "KEY", "CREDENTIAL"}):
            raise ValueError("secret-like environment names require a secret reference")
        return value


class EnvironmentPolicy(_StrictModel):
    allow_names: tuple[str, ...] = SAFE_ENV_VARS
    values: tuple[EnvironmentValue, ...] = ()
    secret_refs: tuple[EnvironmentSecretRef, ...] = ()

    @field_validator("allow_names", mode="before")
    @classmethod
    def normalize_names(cls, values: Any) -> tuple[str, ...]:
        return tuple(dict.fromkeys(values or ()))

    @model_validator(mode="after")
    def validate_unique_names(self) -> Self:
        explicit_names = [item.name for item in self.values]
        secret_names = [item.env_name for item in self.secret_refs]
        all_names = (*self.allow_names, *explicit_names, *secret_names)
        if len(all_names) != len(set(all_names)):
            raise ValueError("environment names must be unique across all sources")
        return self


def _environment_digest(environment: EnvironmentPolicy) -> str:
    payload = environment.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _principal_digest(principal: Principal) -> str:
    payload = {
        "id": principal.id,
        "kind": str(principal.kind),
        "display_name": principal.display_name,
        "scopes": sorted(principal.scopes),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _resolved_path_digest(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()


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
    workspace_lease_id: str | None = Field(default=None, min_length=1)


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
    scope: Literal[
        "shell_exec",
        "tool-argv",
        "acceptance",
        "grep",
        "lsp",
        "lark-cli",
        "keqing",
        "mcp_stdio",
        "universe_gate",
        "universe_sandbox",
    ],
    authority_ref: str,
    argv: Sequence[str] | None = None,
    script: str | None = None,
    server_identity: str | None = None,
    universe_stage: str | None = None,
    cwd: str | None = None,
    environment: EnvironmentPolicy | None = None,
    workspace_root: Path | None = None,
    expires_at: datetime | None = None,
) -> CommandGrant:
    context = _require_execution_context()
    issued_at = datetime.now(UTC)
    system_workspace_scope = scope in {"mcp_stdio", "universe_gate", "universe_sandbox"}
    if system_workspace_scope:
        bound_root = workspace_root if context.workspace_lease_id is not None else None
    else:
        from tianshu.executor.workspace_context import (
            WorkspaceBindingError,
            validate_current_workspace_binding,
        )

        try:
            bound_workspace = validate_current_workspace_binding()
        except WorkspaceBindingError as exc:
            raise ExecutionDenied(
                "identity_contract",
                "workspace_binding_mismatch",
                str(exc),
            ) from None
        bound_root = workspace_root if bound_workspace is not None else None
    unsigned = CommandGrant(
        source=source,
        scope=scope,
        argv_digest=_command_digest(argv) if argv is not None else None,
        shell_digest=hashlib.sha256(script.encode()).hexdigest() if script is not None else None,
        authority_ref=authority_ref,
        server_identity=server_identity,
        actor_id=context.actor.id,
        principal_digest=_principal_digest(context.actor),
        universe_stage=universe_stage,
        cwd=cwd if bound_root is not None else None,
        environment_digest=(
            _environment_digest(environment)
            if environment is not None and bound_root is not None
            else None
        ),
        workspace_lease_id=(context.workspace_lease_id if bound_root is not None else None),
        workspace_root_digest=(
            _resolved_path_digest(bound_root) if bound_root is not None else None
        ),
        resolved_cwd_digest=(
            _resolved_path_digest(bound_root.resolve() / (cwd or "."))
            if bound_root is not None
            else None
        ),
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


def issue_shell_command_grant(
    command: str,
    *,
    cwd: str | None = None,
    workspace_root: Path | None = None,
    environment: EnvironmentPolicy | None = None,
) -> CommandGrant:
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
            cwd=cwd,
            environment=environment,
            workspace_root=workspace_root,
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
        cwd=cwd,
        environment=environment,
        workspace_root=workspace_root,
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


def issue_lark_cli_command_grant(
    argv: Sequence[str],
    *,
    workspace_root: Path | None = None,
    environment: EnvironmentPolicy | None = None,
) -> CommandGrant:
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
        cwd="." if workspace_root is not None else None,
        environment=environment,
        workspace_root=workspace_root,
    )


def _is_workspace_path(path_value: str, workspace_root: Path, *, suffix: str | None = None) -> bool:
    try:
        path = Path(path_value)
        root = workspace_root.resolve()
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return (
        path.is_absolute()
        and resolved.is_relative_to(root)
        and resolved.exists()
        and (suffix is None or resolved.suffix == suffix)
    )


_SYSTEM_ADAPTER_EXECUTABLES: dict[Literal["grep", "lsp"], tuple[str, frozenset[str]]] = {
    "grep": ("rg", frozenset({"rg", "rg.exe"})),
    "lsp": ("basedpyright", frozenset({"basedpyright", "basedpyright.exe"})),
}


def _trusted_adapter_locations(workspace_root: Path) -> tuple[tuple[Path, Path], ...]:
    workspace = workspace_root.resolve()
    try:
        active_runtime_directory = Path(sys.executable).parent.resolve(strict=True)
        active_runtime_prefix = Path(sys.prefix).resolve(strict=True)
    except OSError:
        active_runtime_directory = None
        active_runtime_prefix = None
    raw_locations: list[tuple[Path, Path]] = []
    for raw_directory in os.defpath.split(os.pathsep):
        if raw_directory:
            directory = Path(raw_directory)
            raw_locations.append((directory, directory))
    raw_locations.extend(
        (
            (Path("/usr/local/bin"), Path("/usr/local")),
            (Path("/opt/homebrew/bin"), Path("/opt/homebrew")),
        )
    )
    for raw_prefix in (Path(sys.base_prefix), Path(sys.prefix)):
        raw_locations.extend(
            (
                (raw_prefix / "bin", raw_prefix),
                (raw_prefix / "Scripts", raw_prefix),
            )
        )

    locations: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for raw_directory, raw_trust_root in raw_locations:
        try:
            directory = raw_directory.resolve(strict=True)
            trust_root = raw_trust_root.resolve(strict=True)
        except OSError:
            continue
        active_runtime_location = (
            directory == active_runtime_directory and trust_root == active_runtime_prefix
        )
        if (
            directory in seen
            or not directory.is_dir()
            or (
                (directory.is_relative_to(workspace) or trust_root.is_relative_to(workspace))
                and not active_runtime_location
            )
        ):
            continue
        seen.add(directory)
        locations.append((directory, trust_root))
    return tuple(locations)


def _resolve_trusted_adapter_executable(
    adapter: Literal["grep", "lsp"],
    workspace_root: Path,
) -> Path | None:
    executable_name, allowed_names = _SYSTEM_ADAPTER_EXECUTABLES[adapter]
    locations = _trusted_adapter_locations(workspace_root)
    search_path = os.pathsep.join(str(directory) for directory, _root in locations)
    candidate_value = shutil.which(executable_name, path=search_path)
    if candidate_value is None:
        return None
    try:
        candidate = Path(candidate_value)
        candidate_parent = candidate.parent.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        workspace = workspace_root.resolve()
    except OSError:
        return None
    trust_root = next(
        (root for directory, root in locations if directory == candidate_parent),
        None,
    )
    try:
        active_runtime_candidate = (
            candidate_parent == Path(sys.executable).parent.resolve(strict=True)
            and trust_root == Path(sys.prefix).resolve(strict=True)
            and resolved.is_relative_to(trust_root)
        )
    except OSError:
        active_runtime_candidate = False
    if (
        trust_root is None
        or resolved.name.casefold() not in allowed_names
        or not resolved.is_file()
        or not os.access(resolved, os.X_OK)
        or (resolved.is_relative_to(workspace) and not active_runtime_candidate)
        or not resolved.is_relative_to(trust_root)
    ):
        return None
    return resolved


def resolve_system_adapter_executable(
    adapter: Literal["grep", "lsp"],
    *,
    workspace_root: Path,
) -> str | None:
    """Resolve an adapter from controlled install roots, never the mutable PATH."""
    executable = _resolve_trusted_adapter_executable(adapter, workspace_root)
    return str(executable) if executable is not None else None


def _is_named_executable(
    path_value: str,
    adapter: Literal["grep", "lsp"],
    workspace_root: Path,
) -> bool:
    try:
        path = Path(path_value)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    trusted = _resolve_trusted_adapter_executable(adapter, workspace_root)
    _name, allowed_names = _SYSTEM_ADAPTER_EXECUTABLES[adapter]
    return (
        path.is_absolute()
        and resolved.name.casefold() in allowed_names
        and resolved.is_file()
        and os.access(resolved, os.X_OK)
        and trusted is not None
        and resolved == trusted
    )


def _is_canonical_grep_command(argv: Sequence[str], workspace_root: Path) -> bool:
    values = tuple(argv)
    if (
        len(values) < 9
        or any(not isinstance(value, str) or not value or "\x00" in value for value in values)
        or not _is_named_executable(values[0], "grep", workspace_root)
        or values[1:5] != ("--json", "--line-number", "--color=never", "--hidden")
        or not values[5].startswith("--max-count=")
    ):
        return False
    try:
        limit = int(values[5].partition("=")[2])
    except ValueError:
        return False
    if not 1 <= limit <= 1000:
        return False

    index = 6
    if index < len(values) and values[index] == "-i":
        index += 1
    if index < len(values) and values[index] == "-F":
        index += 1
    if index < len(values) and values[index] == "-C":
        if index + 1 >= len(values):
            return False
        try:
            context = int(values[index + 1])
        except ValueError:
            return False
        if not 1 <= context <= 20:
            return False
        index += 2
    if index < len(values) and values[index] == "--glob":
        if index + 1 >= len(values):
            return False
        index += 2
    if len(values) != index + 3 or values[index] != "--":
        return False
    return _is_workspace_path(values[index + 2], workspace_root)


def issue_grep_command_grant(
    argv: Sequence[str],
    *,
    workspace_root: Path,
    environment: EnvironmentPolicy,
) -> CommandGrant:
    if not _is_canonical_grep_command(argv, workspace_root):
        raise ExecutionDenied(
            "command_grant",
            "grep_command_not_canonical",
            "the grep authority only grants the canonical workspace ripgrep adapter",
        )
    return _mint_command_grant(
        source="system-adapter",
        scope="grep",
        authority_ref="grep:rg-json",
        argv=argv,
        cwd=".",
        environment=environment,
        workspace_root=workspace_root,
    )


def _is_canonical_lsp_command(argv: Sequence[str], workspace_root: Path) -> bool:
    values = tuple(argv)
    return (
        len(values) == 3
        and all(isinstance(value, str) and value and "\x00" not in value for value in values)
        and _is_named_executable(values[0], "lsp", workspace_root)
        and values[1] == "--outputjson"
        and _is_workspace_path(values[2], workspace_root, suffix=".py")
    )


def issue_lsp_command_grant(
    argv: Sequence[str],
    *,
    workspace_root: Path,
    environment: EnvironmentPolicy,
) -> CommandGrant:
    if not _is_canonical_lsp_command(argv, workspace_root):
        raise ExecutionDenied(
            "command_grant",
            "lsp_command_not_canonical",
            "the LSP authority only grants basedpyright JSON diagnostics inside the workspace",
        )
    return _mint_command_grant(
        source="system-adapter",
        scope="lsp",
        authority_ref="lsp:basedpyright-json",
        argv=argv,
        cwd=".",
        environment=environment,
        workspace_root=workspace_root,
    )


def issue_keqing_command_grant(
    argv: Sequence[str],
    *,
    backend: str,
    workspace_root: Path | None = None,
    environment: EnvironmentPolicy | None = None,
) -> CommandGrant:
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
        cwd="." if workspace_root is not None else None,
        environment=environment,
        workspace_root=workspace_root,
    )


_UNIVERSE_STAGES = frozenset({"gate:static", "gate:import", "gate:test", "sandbox:serve"})
_UNIVERSE_LITERAL_ENV_NAMES = frozenset(
    {
        "PYTHONPATH",
        "TIANSHU_DB_PATH",
        "TIANSHU_PORT",
        "TIANSHU_HOST",
        "TIANSHU_EVAL_MODE",
        "TIANSHU_RUNTIME_PERSONAS_DIR",
        "TIANSHU_RUNTIME_SKILLS_DIR",
        "TIANSHU_LLM_API_BASE",
        "TIANSHU_LLM_MODEL",
    }
)


def _is_canonical_universe_command(
    stage: str,
    argv: Sequence[str],
    cwd: str,
    environment: EnvironmentPolicy,
) -> bool:
    if cwd != "." or not argv:
        return False
    values = {item.name: item.value for item in environment.values}
    if stage == "gate:static":
        return (
            len(argv) == 5
            and tuple(argv[1:4]) == ("-m", "compileall", "-q")
            and argv[4] == values.get("PYTHONPATH")
        )
    if stage == "gate:import":
        return tuple(argv[1:]) == ("-c", "import tianshu")
    if stage == "gate:test":
        return tuple(argv[1:]) == ("-m", "pytest", "-q")
    if stage != "sandbox:serve":
        return False
    required_values = {
        "PYTHONPATH",
        "TIANSHU_DB_PATH",
        "TIANSHU_PORT",
        "TIANSHU_HOST",
        "TIANSHU_EVAL_MODE",
    }
    return (
        required_values.issubset(values)
        and values["TIANSHU_HOST"] == "127.0.0.1"
        and values["TIANSHU_EVAL_MODE"] == "1"
        and tuple(argv[1:7])
        == ("-m", "uvicorn", "tianshu.app:create_app", "--factory", "--host", "127.0.0.1")
        and len(argv) == 9
        and argv[7] == "--port"
        and argv[8] == values["TIANSHU_PORT"]
        and argv[8].isdigit()
    )


def issue_universe_command_grant(
    *,
    stage: str,
    argv: Sequence[str],
    workspace_root: Path,
    cwd: str,
    environment: EnvironmentPolicy,
) -> CommandGrant:
    """Admit one exact, stage-scoped Universe command for the bound run."""

    if stage not in _UNIVERSE_STAGES:
        raise ExecutionDenied(
            "command_grant",
            "universe_stage_unknown",
            "Universe command stage is not admitted",
        )
    if not _is_canonical_universe_command(stage, argv, cwd, environment):
        raise ExecutionDenied(
            "command_grant",
            "universe_command_not_canonical",
            "Universe stage command does not match its canonical adapter shape",
        )
    scope: Literal["universe_gate", "universe_sandbox"] = (
        "universe_gate" if stage.startswith("gate:") else "universe_sandbox"
    )
    return _mint_command_grant(
        source="system-adapter",
        scope=scope,
        authority_ref=f"universe:{stage}",
        argv=argv,
        universe_stage=stage,
        cwd=cwd,
        environment=environment,
        workspace_root=workspace_root,
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


class ExecutionReceipt(_StrictModel):
    schema_version: Literal["1"] = "1"
    execution_id: str
    correlation_id: str
    actor_id: str
    purpose: str
    mcp_server_name: str | None = None
    universe_stage: str | None = None
    command_admission: Literal[
        "standard",
        "transitional_mcp_config_g1_6_pending",
    ] = "standard"
    effective_contract_hash: str
    workspace_lease_id: str | None
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
    stdout_incomplete: bool = False
    stderr_incomplete: bool = False
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
    def __init__(
        self,
        guard: str,
        code: str,
        detail: str,
        *,
        receipt: ExecutionReceipt | None = None,
    ) -> None:
        self.guard = guard
        self.code = code
        self.detail = redact_text(detail)
        self.receipt = receipt
        super().__init__(f"{guard}: {code}: {self.detail}")


class ExecutionStartError(RuntimeError):
    def __init__(self, detail: str, receipt: ExecutionReceipt) -> None:
        self.receipt = receipt
        super().__init__(detail)


class ExecutionStartCancelled(asyncio.CancelledError):
    """Cancellation after the gateway entered the structured spawn boundary."""

    def __init__(self, receipt: ExecutionReceipt) -> None:
        self.receipt = receipt
        super().__init__("execution start cancelled")


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


class ExecutionGateway:
    def __init__(
        self,
        *,
        backend: ProcessBackend | None = None,
        mandatory_guards: Sequence[ExecutionGuard] = (),
        advisory_guards: Sequence[ExecutionGuard] = (),
        guard_timeout_seconds: float = 1.0,
        termination_grace_seconds: float = 0.5,
        mcp_stdio_commands: Mapping[str, Sequence[str]] | None = None,
        secret_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._backend = backend or AsyncioProcessBackend()
        self._mandatory_guards = tuple(mandatory_guards)
        self._advisory_guards = tuple(advisory_guards)
        self._guard_timeout_seconds = guard_timeout_seconds
        self._termination_grace_seconds = termination_grace_seconds
        self._secret_resolver = secret_resolver or os.environ.get
        self.configure_mcp_stdio_commands(mcp_stdio_commands or {})

    def configure_mcp_stdio_commands(
        self,
        commands: Mapping[str, Sequence[str]],
    ) -> None:
        self._mcp_stdio_commands = {
            name: ArgvCommand(argv=tuple(argv)).argv for name, argv in commands.items()
        }

    def issue_mcp_stdio_command_grant(
        self,
        server_name: str,
        argv: Sequence[str],
    ) -> CommandGrant:
        exact_argv = ArgvCommand(argv=tuple(argv)).argv
        if self._mcp_stdio_commands.get(server_name) != exact_argv:
            self._deny(
                "command_grant",
                "mcp_command_not_configured",
                "MCP server command is not in the current admitted configuration",
            )
        return _mint_command_grant(
            source="system-adapter",
            scope="mcp_stdio",
            authority_ref=f"mcp-config:{server_name}",
            argv=exact_argv,
            server_identity=server_name,
        )

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        return await (await self.start(request)).wait()

    async def start(self, request: ExecutionRequest) -> ExecutionHandle:
        started_at = datetime.now(UTC)
        started_monotonic = time.monotonic()
        try:
            cwd, built_in_gaps = self._validate_built_in_guards(request)
            env, secret_refs, secret_values = self._build_environment(request)
            custom_gaps = await self._evaluate_custom_guards(request, secret_values)
        except asyncio.CancelledError:
            raise
        except ExecutionDenied as exc:
            if exc.receipt is not None:
                raise
            raise ExecutionDenied(
                exc.guard,
                exc.code,
                exc.detail,
                receipt=self._start_failure_receipt(
                    request=request,
                    env_keys=self._declared_environment_keys(request.environment),
                    secret_refs=tuple(ref.ref for ref in request.environment.secret_refs),
                    advisory_gaps=(),
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                ),
            ) from None
        ownership = _SpawnOwnership()
        spawn_task = asyncio.create_task(
            self._backend.spawn(
                argv=request.command_argv,
                cwd=cwd,
                env=env,
                network=request.network,
                sandbox=request.sandbox,
                stdin_mode=request.stdin_mode,
                on_spawned=ownership.publish,
            ),
            name=f"execution-spawn-{request.execution_id}",
        )
        try:
            spawned = await asyncio.shield(spawn_task)
            ownership.accept_return(spawned)
        except asyncio.CancelledError:
            await self._cancel_spawn_acquisition(spawn_task, ownership)
            acquired = ownership.primary
            receipt = self._start_failure_receipt(
                request=request,
                env_keys=tuple(sorted(env)),
                secret_refs=secret_refs,
                advisory_gaps=(*built_in_gaps, *custom_gaps),
                started_at=started_at,
                started_monotonic=started_monotonic,
                backend_id=(acquired.backend_id if acquired is not None else None),
                sandbox_enforced=(acquired.sandbox_enforced if acquired is not None else False),
                network_enforced=(acquired.network_enforced if acquired is not None else False),
                status="cancelled",
            )
            raise ExecutionStartCancelled(receipt) from None
        except Exception as exc:
            await self._terminate_owned_processes(ownership)
            detail = self._redact_exception(str(exc), secret_values)
            raise ExecutionStartError(
                detail,
                self._start_failure_receipt(
                    request=request,
                    env_keys=tuple(sorted(env)),
                    secret_refs=secret_refs,
                    advisory_gaps=(*built_in_gaps, *custom_gaps),
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                ),
            ) from None
        sandbox_enforced = bool(getattr(spawned, "sandbox_enforced", False))
        if request.sandbox.mode == "required" and not sandbox_enforced:
            await _terminate_process_tree(
                spawned.process,
                process_group_id=spawned.process.pid,
                grace_seconds=self._termination_grace_seconds,
            )
            raise ExecutionDenied(
                "sandbox",
                "backend_enforcement_unproven",
                "backend did not prove required per-process sandbox enforcement",
                receipt=self._start_failure_receipt(
                    request=request,
                    env_keys=tuple(sorted(env)),
                    secret_refs=secret_refs,
                    advisory_gaps=(*built_in_gaps, *custom_gaps),
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    backend_id=spawned.backend_id,
                    sandbox_enforced=False,
                    network_enforced=spawned.network_enforced,
                ),
            )
        runtime_gaps: tuple[GuardGap, ...] = ()
        if request.sandbox.mode == "preferred" and not sandbox_enforced:
            if not request.sandbox.allow_host:
                await _terminate_process_tree(
                    spawned.process,
                    process_group_id=spawned.process.pid,
                    grace_seconds=self._termination_grace_seconds,
                )
                self._deny(
                    "sandbox",
                    "host_not_explicit",
                    "sandbox fallback was not explicitly admitted",
                )
            if not any(gap.code == "sandbox_unavailable_host_fallback" for gap in built_in_gaps):
                runtime_gaps = (
                    GuardGap(
                        guard="sandbox",
                        code="sandbox_unavailable_host_fallback",
                        detail=(
                            "preferred sandbox backend did not prove isolation; "
                            "trusted-local host fallback is active"
                        ),
                    ),
                )
        if request.network.mode != "unrestricted" and not spawned.network_enforced:
            await _terminate_process_tree(
                spawned.process,
                process_group_id=spawned.process.pid,
                grace_seconds=self._termination_grace_seconds,
            )
            raise ExecutionDenied(
                "network",
                "backend_enforcement_unproven",
                "backend did not prove restrictive network enforcement",
                receipt=self._start_failure_receipt(
                    request=request,
                    env_keys=tuple(sorted(env)),
                    secret_refs=secret_refs,
                    advisory_gaps=(*built_in_gaps, *custom_gaps, *runtime_gaps),
                    started_at=started_at,
                    started_monotonic=started_monotonic,
                    backend_id=spawned.backend_id,
                    sandbox_enforced=sandbox_enforced,
                    network_enforced=False,
                ),
            )
        return ExecutionHandle(
            request=request,
            process=spawned.process,
            env_keys=tuple(sorted(env)),
            secret_refs=secret_refs,
            secret_values=secret_values,
            advisory_gaps=(*built_in_gaps, *custom_gaps, *runtime_gaps),
            sandbox_enforced=sandbox_enforced,
            network_enforced=spawned.network_enforced,
            backend_id=spawned.backend_id,
            started_at=started_at,
            started_monotonic=started_monotonic,
            termination_grace_seconds=self._termination_grace_seconds,
        )

    def _start_failure_receipt(
        self,
        *,
        request: ExecutionRequest,
        env_keys: tuple[str, ...],
        secret_refs: tuple[str, ...],
        advisory_gaps: tuple[GuardGap, ...],
        started_at: datetime,
        started_monotonic: float,
        backend_id: str | None = None,
        sandbox_enforced: bool = False,
        network_enforced: bool = False,
        status: Literal["failed", "cancelled"] = "failed",
    ) -> ExecutionReceipt:
        finished_at = datetime.now(UTC)
        return ExecutionReceipt(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            actor_id=request.actor.id,
            purpose=request.purpose,
            mcp_server_name=request.mcp_server_name,
            universe_stage=request.universe_stage,
            command_admission=(
                "transitional_mcp_config_g1_6_pending"
                if request.purpose == "mcp_stdio"
                else "standard"
            ),
            effective_contract_hash=request.effective_contract_hash,
            workspace_lease_id=request.workspace_lease_id,
            cwd=request.cwd,
            command_kind="argv" if request.argv_command is not None else "shell",
            executable=request.command_argv[0],
            env_keys=env_keys,
            secret_refs=secret_refs,
            network_mode=request.network.mode,
            sandbox_mode=request.sandbox.mode,
            sandbox_enforced=sandbox_enforced,
            backend_id=backend_id or str(getattr(self._backend, "backend_id", "unknown")),
            network_enforced=network_enforced,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((time.monotonic() - started_monotonic) * 1000),
            exit_code=None,
            terminating_signal=None,
            stdout_bytes=0,
            stderr_bytes=0,
            stdout_truncated=False,
            stderr_truncated=False,
            advisory_gaps=advisory_gaps,
        )

    async def _cancel_spawn_acquisition(
        self,
        spawn_task: asyncio.Task[SpawnedProcess],
        ownership: _SpawnOwnership,
    ) -> None:
        if not spawn_task.done():
            spawn_task.cancel()
        while not spawn_task.done():
            try:
                await asyncio.shield(spawn_task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if spawn_task.done() and not spawn_task.cancelled() and spawn_task.exception() is None:
            with suppress(TypeError, RuntimeError):
                ownership.accept_return(spawn_task.result())
        await self._terminate_owned_processes(ownership)

    async def _terminate_owned_processes(self, ownership: _SpawnOwnership) -> None:
        terminated: set[int] = set()
        for spawned in ownership.processes:
            process_group_id = spawned.process.pid
            if process_group_id in terminated:
                continue
            terminated.add(process_group_id)
            await _terminate_process_tree(
                spawned.process,
                process_group_id=process_group_id,
                grace_seconds=self._termination_grace_seconds,
            )

    def _validate_built_in_guards(
        self,
        request: ExecutionRequest,
    ) -> tuple[Path, tuple[GuardGap, ...]]:
        if request.timeout_seconds > request.effective_contract.budget.wall_clock_seconds:
            self._deny("identity_contract", "timeout_exceeds_contract", "timeout exceeds contract")

        try:
            _validate_current_workspace_binding(
                correlation_id=request.correlation_id,
                effective_contract=request.effective_contract,
                workspace_lease_id=request.workspace_lease_id,
                workspace_root=request.workspace_root,
            )
        except ExecutionDenied as exc:
            self._deny(exc.guard, exc.code, exc.detail)
        current_context = get_execution_context()
        if current_context is not None and (
            current_context.correlation_id != request.correlation_id
            or current_context.effective_contract.content_hash
            != request.effective_contract.content_hash
            or current_context.workspace_lease_id != request.workspace_lease_id
        ):
            self._deny(
                "identity_contract",
                "current_execution_mismatch",
                "execution request does not match the current run context",
            )

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
            or grant.actor_id != request.actor.id
            or grant.principal_digest != _principal_digest(request.actor)
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
        workspace_binding_matches = (
            grant.cwd == request.cwd
            and grant.environment_digest == _environment_digest(request.environment)
            and grant.workspace_lease_id == request.workspace_lease_id
            and grant.workspace_root_digest == _resolved_path_digest(root)
            and grant.resolved_cwd_digest == _resolved_path_digest(cwd)
        )
        from tianshu.executor.workspace_context import requires_workspace_binding

        contract_requires_workspace = requires_workspace_binding(request.effective_contract)
        unbound_legacy_grant = (
            not contract_requires_workspace
            and grant.workspace_lease_id is None
            and grant.workspace_root_digest is None
        )
        workspace_bound_scopes = {"shell_exec", "grep", "lsp", "lark-cli", "keqing"}
        if (
            contract_requires_workspace
            and grant.scope in workspace_bound_scopes
            and (grant.workspace_root_digest is None or not workspace_binding_matches)
        ):
            self._deny(
                "command_grant",
                "workspace_authority_mismatch",
                "governed command grant is not bound to the active staging workspace",
            )
        if (
            grant.source != "system-adapter"
            and grant.workspace_root_digest is not None
            and not workspace_binding_matches
        ):
            self._deny(
                "command_grant",
                "workspace_authority_mismatch",
                "command grant workspace authority does not match the request",
            )
        allowed_scopes = {
            "tool": {"shell_exec", "tool-argv"},
            "acceptance": {"acceptance"},
            "grep": {"grep"},
            "lsp": {"lsp"},
            "lark-cli": {"lark-cli"},
            "keqing": {"keqing"},
            "mcp_stdio": {"mcp_stdio"},
            "universe_gate": {"universe_gate"},
            "universe_sandbox": {"universe_sandbox"},
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
                valid_system_scope = (
                    grant.authority_ref == "lark-cli"
                    and executable in {"lark-cli", "lark-cli.exe"}
                    and (grant.workspace_root_digest is None or workspace_binding_matches)
                )
            elif grant.scope == "grep":
                valid_system_scope = (
                    grant.authority_ref == "grep:rg-json"
                    and (workspace_binding_matches or unbound_legacy_grant)
                    and _is_canonical_grep_command(request.command_argv, root)
                )
            elif grant.scope == "lsp":
                valid_system_scope = (
                    grant.authority_ref == "lsp:basedpyright-json"
                    and (workspace_binding_matches or unbound_legacy_grant)
                    and _is_canonical_lsp_command(request.command_argv, root)
                )
            elif grant.scope == "keqing":
                from tianshu.executor.keqing.adapter import is_canonical_adapter_argv

                backend = request.effective_contract.executor.adapter_id.removeprefix("keqing:")
                valid_system_scope = (
                    grant.authority_ref == f"keqing:{backend}"
                    and request.effective_contract.executor.adapter_id.startswith("keqing:")
                    and is_canonical_adapter_argv(backend, request.command_argv)
                    and (grant.workspace_root_digest is None or workspace_binding_matches)
                )
            elif grant.scope == "mcp_stdio":
                server_name = request.mcp_server_name
                valid_system_scope = (
                    server_name is not None
                    and grant.server_identity == server_name
                    and grant.authority_ref == f"mcp-config:{server_name}"
                    and self._mcp_stdio_commands.get(server_name) == request.command_argv
                )
            elif grant.scope in {"universe_gate", "universe_sandbox"}:
                valid_system_scope = (
                    request.universe_stage in _UNIVERSE_STAGES
                    and grant.authority_ref == f"universe:{request.universe_stage}"
                    and grant.universe_stage == request.universe_stage
                    and workspace_binding_matches
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
        if request.sandbox.trust_level == "secure-remote" and (
            request.sandbox.mode != "required" or request.sandbox.allow_host
        ):
            self._deny(
                "sandbox",
                "secure_remote_policy_invalid",
                "secure-remote requires a required sandbox without host fallback",
            )
        if request.sandbox.trust_level == "secure-remote" and not supports_sandbox:
            self._deny("sandbox", "secure_remote_unavailable", "required sandbox is unavailable")
        if request.sandbox.mode == "required" and not supports_sandbox:
            self._deny("sandbox", "required_unavailable", "required sandbox is unavailable")
        host_fallback = request.sandbox.mode == "host" or (
            request.sandbox.mode == "preferred" and not supports_sandbox
        )
        if host_fallback and not request.sandbox.allow_host:
            self._deny(
                "sandbox", "host_not_explicit", "trusted-local host execution is not explicit"
            )

        gaps: list[GuardGap] = []
        if host_fallback and request.purpose in {"universe_gate", "universe_sandbox"}:
            gaps.append(
                GuardGap(
                    guard="sandbox",
                    code="sandbox_unavailable_host_fallback",
                    detail="trusted-local execution is running without enforced sandbox isolation",
                )
            )
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

        requested_refs = {secret.ref for secret in request.environment.secret_refs}
        if request.purpose not in {"universe_gate", "universe_sandbox"} and any(
            ref.startswith("settings:") for ref in requested_refs
        ):
            self._deny(
                "environment",
                "reserved_secret_namespace",
                "settings secret references are reserved for governed Universe execution",
            )
        declared_refs = set(request.effective_contract.permissions.secret_refs)
        if not requested_refs.issubset(declared_refs):
            self._deny("environment", "secret_not_granted", "secret reference is not in contract")
        explicit_names = {item.name for item in request.environment.values}
        if explicit_names and request.purpose not in {"universe_gate", "universe_sandbox"}:
            self._deny(
                "environment",
                "literal_values_not_allowed",
                "explicit environment values are limited to governed Universe execution",
            )
        if not explicit_names.issubset(_UNIVERSE_LITERAL_ENV_NAMES):
            self._deny(
                "environment",
                "literal_name_not_allowed",
                "Universe literal environment name is not in the fixed configuration allowlist",
            )
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
        env.update({item.name: item.value for item in request.environment.values})
        secret_refs: list[str] = []
        secret_values: list[str] = []
        for secret in request.environment.secret_refs:
            value = self._secret_resolver(secret.ref)
            if value is None:
                self._deny("environment", "secret_unavailable", "secret reference is unavailable")
            env[secret.env_name] = value
            secret_refs.append(secret.ref)
            secret_values.append(value)
        return env, tuple(secret_refs), tuple(secret_values)

    @staticmethod
    def _declared_environment_keys(environment: EnvironmentPolicy) -> tuple[str, ...]:
        passthrough = ",".join(environment.allow_names)
        keys = set(build_clean_env(passthrough))
        keys.update(item.name for item in environment.values)
        keys.update(item.env_name for item in environment.secret_refs)
        return tuple(sorted(keys))

    @staticmethod
    def _redact_exception(detail: str, secrets: tuple[str, ...]) -> str:
        for secret in secrets:
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
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
    "EnvironmentValue",
    "ExecutionContext",
    "ExecutionDenied",
    "ExecutionGateway",
    "ExecutionGuard",
    "ExecutionHandle",
    "ExecutionReceipt",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStartCancelled",
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
    "issue_grep_command_grant",
    "issue_keqing_command_grant",
    "issue_lark_cli_command_grant",
    "issue_lsp_command_grant",
    "issue_shell_command_grant",
    "issue_universe_command_grant",
    "request_for_current_execution",
    "resolve_system_adapter_executable",
]
