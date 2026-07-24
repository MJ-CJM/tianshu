"""ExecutionGateway 本体 —— 从 execution_gateway 单文件拆出，符号原文零改动。"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, NoReturn

from tianshu.security.bash_analysis import analyze_command
from tianshu.security.clean_env import build_clean_env
from tianshu.security.redact import redact_text

from .grants import (
    _UNIVERSE_LITERAL_ENV_NAMES,
    _UNIVERSE_STAGES,
    _is_canonical_grep_command,
    _is_canonical_lsp_command,
    _mint_command_grant,
    get_execution_context,
)
from .policy_models import (
    ArgvCommand,
    CommandGrant,
    EnvironmentPolicy,
    ExecutionDenied,
    ExecutionReceipt,
    ExecutionResult,
    ExecutionStartCancelled,
    ExecutionStartError,
    GuardDecision,
    GuardGap,
    _command_digest,
    _environment_digest,
    _principal_digest,
    _resolved_path_digest,
    _valid_signature,
)
from .process_backend import (
    AsyncioProcessBackend,
    ExecutionGuard,
    ExecutionHandle,
    ExecutionRequest,
    ProcessBackend,
    SpawnedProcess,
    _SpawnOwnership,
    _terminate_process_tree,
    _validate_current_workspace_binding,
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
        # keqing-run: 是天枢逐 run 铸造的 scoped token 命名空间(客卿 LLM 出口凭证)。
        # 仅 keqing purpose 可用;且由天枢自身铸造(非用户/repo 供给),故豁免 contract
        # 声明的 subset 检查——但其余所有 ref 仍须在 contract 授予集内。
        keqing_run_refs = {ref for ref in requested_refs if ref.startswith("keqing-run:")}
        if keqing_run_refs and request.purpose != "keqing":
            self._deny(
                "environment",
                "reserved_secret_namespace",
                "keqing-run secret references are reserved for keqing execution",
            )
        declared_refs = set(request.effective_contract.permissions.secret_refs)
        if not (requested_refs - keqing_run_refs).issubset(declared_refs):
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
