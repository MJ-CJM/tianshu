"""Grant/policy 与执行回执值对象 —— 从 execution_gateway 单文件拆出，符号原文零改动。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tianshu.models.principal import Principal
from tianshu.security.clean_env import SAFE_ENV_VARS
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
