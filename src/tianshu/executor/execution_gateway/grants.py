"""Execution context 绑定与八类 command grant 签发 —— 从 execution_gateway 单文件拆出，符号原文零改动。"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from tianshu.models.governance_contract import AcceptanceCheckV1, EffectiveGovernanceContractV1
from tianshu.models.principal import Principal
from tianshu.security.bash_analysis import analyze_command

from .policy_models import (
    CommandGrant,
    EnvironmentPolicy,
    ExecutionDenied,
    ToolPolicyDecision,
    _command_digest,
    _environment_digest,
    _principal_digest,
    _resolved_path_digest,
    _sign,
    _StrictModel,
    _tool_arguments_digest,
    _valid_signature,
)


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
