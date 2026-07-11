"""grep and LSP system adapters must execute through canonical grants."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tianshu.executor import execution_gateway as gateway
from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.lsp import diagnostics as lsp_module
from tianshu.models.governance_contract import (
    NetworkPolicyV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.tools import grep as grep_module
from tianshu.tools.edit_file import register_edit_file
from tianshu.tools.registry import ToolRegistry


def _make_executable(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _trust_adapter_executables(
    monkeypatch: pytest.MonkeyPatch,
    *,
    grep: str | None = None,
    lsp: str | None = None,
) -> None:
    executables = {"grep": grep, "lsp": lsp}
    monkeypatch.setattr(
        gateway,
        "_resolve_trusted_adapter_executable",
        lambda adapter, _workspace_root: (
            Path(executables[adapter]).resolve() if executables[adapter] is not None else None
        ),
    )


def _context(
    correlation_id: str = "memorial-system-adapters",
    *,
    unrestricted_network: bool = False,
) -> gateway.ExecutionContext:
    effective = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="inspect workspace"),
            network=NetworkPolicyV1(
                mode="unrestricted_requested" if unrestricted_network else "deny"
            ),
        ),
        native_manifest(),
        probe_host_capabilities(),
    )
    return gateway.ExecutionContext(
        correlation_id=correlation_id,
        actor=Principal(
            id="principal-system-adapters",
            kind=PrincipalKind.SERVICE,
            display_name="System adapters",
        ),
        effective_contract=effective,
        workspace_lease_id="workspace-system-adapters",
    )


def _result(
    request: gateway.ExecutionRequest,
    *,
    stdout: str = "",
    stderr: str = "",
    status: str = "succeeded",
    exit_code: int | None = 0,
    stdout_truncated: bool = False,
    stdout_incomplete: bool = False,
) -> gateway.ExecutionResult:
    now = datetime.now(UTC)
    return gateway.ExecutionResult(
        stdout=stdout,
        stderr=stderr,
        receipt=gateway.ExecutionReceipt(
            execution_id=request.execution_id,
            correlation_id=request.correlation_id,
            actor_id=request.actor.id,
            purpose=request.purpose,
            effective_contract_hash=request.effective_contract_hash,
            workspace_lease_id=request.workspace_lease_id,
            cwd=request.cwd,
            command_kind="argv",
            executable=request.command_argv[0],
            env_keys=(),
            secret_refs=(),
            network_mode=request.network.mode,
            sandbox_mode=request.sandbox.mode,
            sandbox_enforced=False,
            status=status,
            started_at=now,
            finished_at=now,
            duration_ms=1,
            exit_code=exit_code,
            terminating_signal=None,
            stdout_bytes=len(stdout.encode()),
            stderr_bytes=len(stderr.encode()),
            stdout_truncated=stdout_truncated,
            stderr_truncated=False,
            stdout_incomplete=stdout_incomplete,
        ),
    )


class _RecordingGateway:
    def __init__(
        self,
        *,
        stdout: str = "",
        status: str = "succeeded",
        stdout_truncated: bool = False,
        stdout_incomplete: bool = False,
    ) -> None:
        self.stdout = stdout
        self.status = status
        self.stdout_truncated = stdout_truncated
        self.stdout_incomplete = stdout_incomplete
        self.requests: list[gateway.ExecutionRequest] = []

    async def run(self, request: gateway.ExecutionRequest) -> gateway.ExecutionResult:
        self.requests.append(request)
        return _result(
            request,
            stdout=self.stdout,
            status=self.status,
            exit_code=None if self.status == "timed_out" else 0,
            stdout_truncated=self.stdout_truncated,
            stdout_incomplete=self.stdout_incomplete,
        )


class _ForbiddenBackend:
    backend_id = "forbidden"
    supports_sandbox = False
    supports_network_enforcement = False

    async def spawn(self, **_kwargs: object) -> None:
        raise AssertionError("replayed adapter grant reached process spawn")


async def test_grep_uses_canonical_gateway_grant_and_option_terminator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("-needle\n", encoding="utf-8")
    rg_message = json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": str(target)},
                "line_number": 1,
                "lines": {"text": "-needle\n"},
            },
        }
    )
    recording = _RecordingGateway(stdout=f"{rg_message}\n")
    registry = ToolRegistry()
    rg_executable = _make_executable(tmp_path / "rg")
    _trust_adapter_executables(monkeypatch, grep=rg_executable)

    async def direct_spawn_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("grep bypassed ExecutionGateway")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", direct_spawn_forbidden)
    grep_module.register_grep(registry, tmp_path, execution_gateway=recording)

    with gateway.bind_execution_context(_context()):
        result = await registry.execute("grep", {"pattern": "-needle"})

    assert result.is_error is False
    assert "sample.txt:1: -needle" in result.content
    assert len(recording.requests) == 1
    request = recording.requests[0]
    assert request.purpose == "grep"
    assert request.command_grant is not None
    assert request.command_grant.source == "system-adapter"
    assert request.command_grant.scope == "grep"
    separator = request.command_argv.index("--")
    assert request.command_argv[separator + 1] == "-needle"
    assert request.workspace_root == tmp_path.resolve()


async def test_grep_rejects_truncated_gateway_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("needle\n", encoding="utf-8")
    recording = _RecordingGateway(stdout='{"type":"match"', stdout_truncated=True)
    registry = ToolRegistry()
    rg_executable = _make_executable(tmp_path / "rg")
    _trust_adapter_executables(monkeypatch, grep=rg_executable)
    grep_module.register_grep(registry, tmp_path, execution_gateway=recording)

    with gateway.bind_execution_context(_context("memorial-grep-truncated")):
        result = await registry.execute("grep", {"pattern": "needle"})

    assert result.is_error is True
    assert "incomplete" in result.content


async def test_grep_enforces_global_match_limit_across_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = []
    for index in range(2):
        target = tmp_path / f"sample-{index}.txt"
        target.write_text("needle\n", encoding="utf-8")
        messages.append(
            json.dumps(
                {
                    "type": "match",
                    "data": {
                        "path": {"text": str(target)},
                        "line_number": 1,
                        "lines": {"text": "needle\n"},
                    },
                }
            )
        )
    recording = _RecordingGateway(stdout="\n".join(messages))
    registry = ToolRegistry()
    rg_executable = _make_executable(tmp_path / "rg")
    _trust_adapter_executables(monkeypatch, grep=rg_executable)
    grep_module.register_grep(registry, tmp_path, execution_gateway=recording)

    with gateway.bind_execution_context(_context("memorial-grep-global-limit")):
        result = await registry.execute("grep", {"pattern": "needle", "limit": 1})

    assert result.is_error is False
    assert result.details == {"match_count": 1, "limit_reached": True}
    assert "sample-0.txt" in result.content
    assert "sample-1.txt" not in result.content


async def test_grep_true_gateway_falls_back_only_for_unenforceable_network_deny(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("needle\n", encoding="utf-8")
    rg_executable = _make_executable(tmp_path / "rg")
    _trust_adapter_executables(monkeypatch, grep=rg_executable)
    registry = ToolRegistry()
    grep_module.register_grep(
        registry,
        tmp_path,
        execution_gateway=gateway.ExecutionGateway(),
    )

    async def spawn_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network-denied grep reached process spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_forbidden)
    with gateway.bind_execution_context(_context("memorial-grep-network-deny")):
        result = await registry.execute("grep", {"pattern": "needle"})

    assert result.is_error is False
    assert "sample.txt:1: needle" in result.content
    advisory = result.details["execution_advisory"]
    assert advisory["code"] == "enforcement_unavailable"
    assert advisory["correlation_id"] == "memorial-grep-network-deny"
    assert advisory["receipt"]["status"] == "failed"


async def test_grep_true_gateway_runs_when_contract_explicitly_allows_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rg_executable = _make_executable(tmp_path / "rg")
    _trust_adapter_executables(monkeypatch, grep=rg_executable)
    registry = ToolRegistry()
    grep_module.register_grep(
        registry,
        tmp_path,
        execution_gateway=gateway.ExecutionGateway(),
    )

    with gateway.bind_execution_context(
        _context("memorial-grep-network-open", unrestricted_network=True)
    ):
        result = await registry.execute("grep", {"pattern": "needle"})

    assert result.is_error is False
    assert result.content == "No matches found."
    assert "execution_advisory" not in (result.details or {})


async def test_grep_python_fallback_does_not_follow_file_symlinks_outside_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside-secret-value\n", encoding="utf-8")
    (workspace / "leak.txt").symlink_to(outside / "secret.txt")
    rg_executable = _make_executable(workspace / "rg")
    _trust_adapter_executables(monkeypatch, grep=rg_executable)
    registry = ToolRegistry()
    grep_module.register_grep(
        registry,
        workspace,
        execution_gateway=gateway.ExecutionGateway(),
    )

    with gateway.bind_execution_context(_context("memorial-grep-symlink")):
        result = await registry.execute("grep", {"pattern": "outside-secret-value"})

    assert result.is_error is False
    assert result.content == "No matches found."


def test_grep_and_lsp_grants_reject_noncanonical_or_escaped_argv(tmp_path: Path) -> None:
    context = _context("memorial-system-grants")
    environment = gateway.EnvironmentPolicy()
    with gateway.bind_execution_context(context):
        with pytest.raises(gateway.ExecutionDenied, match="grep_command_not_canonical"):
            gateway.issue_grep_command_grant(
                ("rg", "--json", "-needle", str(tmp_path)),
                workspace_root=tmp_path,
                environment=environment,
            )
        with pytest.raises(gateway.ExecutionDenied, match="lsp_command_not_canonical"):
            gateway.issue_lsp_command_grant(
                ("basedpyright", "--outputjson", str(tmp_path.parent / "escaped.py")),
                workspace_root=tmp_path,
                environment=environment,
            )


async def test_grep_grant_cannot_replay_across_workspace_or_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _make_executable(tmp_path / "rg")
    _trust_adapter_executables(monkeypatch, grep=executable)
    search_root = tmp_path / "search"
    search_root.mkdir()
    argv = (
        executable,
        "--json",
        "--line-number",
        "--color=never",
        "--hidden",
        "--max-count=10",
        "--",
        "needle",
        str(search_root),
    )
    environment = gateway.EnvironmentPolicy()
    context = _context("memorial-grep-replay")
    with gateway.bind_execution_context(context):
        grant = gateway.issue_grep_command_grant(
            argv,
            workspace_root=tmp_path,
            environment=environment,
        )
        request = gateway.request_for_current_execution(
            purpose="grep",
            workspace_root=tmp_path,
            cwd=".",
            argv_command=gateway.ArgvCommand(argv=argv),
            environment=environment,
            timeout_seconds=30,
            stdout_limit_bytes=1000,
            stderr_limit_bytes=1000,
            sandbox=gateway.SandboxRequirement(
                trust_level="trusted-local",
                mode="host",
                allow_host=True,
            ),
            command_grant=grant,
        )

    assert grant.workspace_lease_id == context.workspace_lease_id
    assert grant.workspace_root_digest is not None
    assert grant.environment_digest is not None
    other_root = tmp_path / "other"
    other_root.mkdir()
    process_gateway = gateway.ExecutionGateway(backend=_ForbiddenBackend())
    with pytest.raises(gateway.ExecutionDenied, match="system_source_mismatch"):
        await process_gateway.run(request.model_copy(update={"workspace_root": other_root}))
    changed_environment = gateway.EnvironmentPolicy(
        values=(gateway.EnvironmentValue(name="SAFE_FLAG", value="changed"),)
    )
    with pytest.raises(gateway.ExecutionDenied, match="system_source_mismatch"):
        await process_gateway.run(request.model_copy(update={"environment": changed_environment}))


def test_grep_and_lsp_grants_reject_workspace_executable_lookalikes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trusted_rg = _make_executable(trusted_dir / "rg")
    trusted_lsp = _make_executable(trusted_dir / "basedpyright")
    lookalike_rg = _make_executable(workspace / "rg")
    lookalike_lsp = _make_executable(workspace / "basedpyright")
    source = workspace / "sample.py"
    source.write_text("x = 1\n", encoding="utf-8")
    _trust_adapter_executables(monkeypatch, grep=trusted_rg, lsp=trusted_lsp)
    environment = gateway.EnvironmentPolicy()

    with gateway.bind_execution_context(_context("memorial-adapter-lookalikes")):
        with pytest.raises(gateway.ExecutionDenied, match="grep_command_not_canonical"):
            gateway.issue_grep_command_grant(
                (
                    lookalike_rg,
                    "--json",
                    "--line-number",
                    "--color=never",
                    "--hidden",
                    "--max-count=10",
                    "--",
                    "needle",
                    str(workspace),
                ),
                workspace_root=workspace,
                environment=environment,
            )
        with pytest.raises(gateway.ExecutionDenied, match="lsp_command_not_canonical"):
            gateway.issue_lsp_command_grant(
                (lookalike_lsp, "--outputjson", str(source)),
                workspace_root=workspace,
                environment=environment,
            )


def test_adapter_resolution_ignores_mutable_process_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_rg = _make_executable(tmp_path / "rg")
    monkeypatch.setenv("PATH", str(tmp_path))

    resolved = gateway.resolve_system_adapter_executable(
        "grep",
        workspace_root=tmp_path,
    )

    assert resolved is None or Path(resolved).resolve() != Path(workspace_rg).resolve()


def test_adapter_resolution_trusts_the_active_runtime_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    runtime_bin = workspace / ".venv" / "bin"
    runtime_bin.mkdir(parents=True)
    runtime_python = _make_executable(runtime_bin / "python")
    basedpyright = _make_executable(runtime_bin / "basedpyright")
    monkeypatch.setattr(gateway.sys, "executable", runtime_python)
    monkeypatch.setattr(gateway.sys, "prefix", str(runtime_bin.parent))

    resolved = gateway.resolve_system_adapter_executable(
        "lsp",
        workspace_root=workspace,
    )

    assert resolved == str(Path(basedpyright).resolve())


async def test_adapter_grant_rechecks_trusted_executable_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trusted_dir = tmp_path / "trusted-rg"
    trusted_dir.mkdir()
    replacement_dir = tmp_path / "replacement-rg"
    replacement_dir.mkdir()
    trusted = _make_executable(trusted_dir / "rg")
    replacement = _make_executable(replacement_dir / "rg")
    _trust_adapter_executables(monkeypatch, grep=trusted)
    argv = (
        trusted,
        "--json",
        "--line-number",
        "--color=never",
        "--hidden",
        "--max-count=10",
        "--",
        "needle",
        str(workspace),
    )
    environment = gateway.EnvironmentPolicy()
    with gateway.bind_execution_context(_context("memorial-adapter-recheck")):
        grant = gateway.issue_grep_command_grant(
            argv,
            workspace_root=workspace,
            environment=environment,
        )
        request = gateway.request_for_current_execution(
            purpose="grep",
            workspace_root=workspace,
            cwd=".",
            argv_command=gateway.ArgvCommand(argv=argv),
            environment=environment,
            timeout_seconds=30,
            stdout_limit_bytes=1_000,
            stderr_limit_bytes=1_000,
            sandbox=gateway.SandboxRequirement(
                trust_level="trusted-local",
                mode="host",
                allow_host=True,
            ),
            command_grant=grant,
        )

    _trust_adapter_executables(monkeypatch, grep=replacement)
    process_gateway = gateway.ExecutionGateway(backend=_ForbiddenBackend())
    with pytest.raises(gateway.ExecutionDenied, match="system_source_mismatch"):
        await process_gateway.run(request)


async def test_lsp_async_core_uses_gateway_and_returns_correlated_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("missing_name\n", encoding="utf-8")
    payload = json.dumps(
        {
            "generalDiagnostics": [
                {
                    "severity": "error",
                    "message": '"missing_name" is not defined',
                    "rule": "reportUndefinedVariable",
                    "range": {"start": {"line": 0, "character": 0}},
                }
            ]
        }
    )
    recording = _RecordingGateway(stdout=payload)
    monkeypatch.setenv("TIANSHU_LSP_ENABLED", "1")
    lsp_executable = _make_executable(tmp_path / "basedpyright")
    _trust_adapter_executables(monkeypatch, lsp=lsp_executable)

    def direct_spawn_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("LSP bypassed ExecutionGateway")

    monkeypatch.setattr(subprocess, "run", direct_spawn_forbidden)

    with gateway.bind_execution_context(_context("memorial-lsp")):
        outcome = await lsp_module.run_diagnostics_async(
            source,
            execution_gateway=recording,
            workspace_root=tmp_path,
        )

    assert outcome.status == "ok"
    assert outcome.correlation_id == "memorial-lsp"
    assert outcome.advisory is None
    assert outcome.diagnostics[0]["line"] == 1
    request = recording.requests[0]
    assert request.purpose == "lsp"
    assert request.command_grant is not None
    assert request.command_grant.scope == "lsp"
    assert request.command_argv == (lsp_executable, "--outputjson", str(source))


async def test_lsp_true_gateway_reports_network_enforcement_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("TIANSHU_LSP_ENABLED", "1")
    lsp_executable = _make_executable(tmp_path / "basedpyright")
    _trust_adapter_executables(monkeypatch, lsp=lsp_executable)

    async def spawn_forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network-denied LSP reached process spawn")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn_forbidden)
    with gateway.bind_execution_context(_context("memorial-lsp-network-deny")):
        outcome = await lsp_module.run_diagnostics_async(
            source,
            execution_gateway=gateway.ExecutionGateway(),
            workspace_root=tmp_path,
        )

    assert outcome.status == "denied"
    assert outcome.correlation_id == "memorial-lsp-network-deny"
    assert "enforcement_unavailable" in (outcome.advisory or "")
    assert outcome.receipt is not None
    assert outcome.receipt["status"] == "failed"


async def test_lsp_invalid_json_shape_returns_failed_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("TIANSHU_LSP_ENABLED", "1")
    lsp_executable = _make_executable(tmp_path / "basedpyright")
    _trust_adapter_executables(monkeypatch, lsp=lsp_executable)
    recording = _RecordingGateway(stdout='{"generalDiagnostics":[null]}')

    with gateway.bind_execution_context(_context("memorial-lsp-invalid-shape")):
        outcome = await lsp_module.run_diagnostics_async(
            source,
            execution_gateway=recording,
            workspace_root=tmp_path,
        )

    assert outcome.status == "failed"
    assert outcome.correlation_id == "memorial-lsp-invalid-shape"
    assert "schema" in (outcome.advisory or "")


@pytest.mark.parametrize(
    ("status", "expected"),
    [("timed_out", "timed_out"), ("succeeded", "unavailable")],
)
async def test_lsp_failure_modes_are_structured_advisories_with_correlation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected: str,
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("TIANSHU_LSP_ENABLED", "1")
    if expected == "unavailable":
        _trust_adapter_executables(monkeypatch)
    else:
        lsp_executable = _make_executable(tmp_path / "basedpyright")
        _trust_adapter_executables(monkeypatch, lsp=lsp_executable)
    recording = _RecordingGateway(status=status)

    with gateway.bind_execution_context(_context("memorial-lsp-advisory")):
        outcome = await lsp_module.run_diagnostics_async(
            source,
            execution_gateway=recording,
            workspace_root=tmp_path,
        )

    assert outcome.status == expected
    assert outcome.correlation_id == "memorial-lsp-advisory"
    assert outcome.advisory
    assert outcome.diagnostics == ()


async def test_lsp_sync_wrapper_rejects_active_event_loop(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="await run_diagnostics_async"):
        lsp_module.run_diagnostics(source, workspace_root=tmp_path)


async def test_edit_file_awaits_lsp_and_surfaces_advisory_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "sample.py"
    source.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setenv("TIANSHU_LSP_ENABLED", "1")
    _trust_adapter_executables(monkeypatch)
    registry = ToolRegistry()
    recording = _RecordingGateway()
    register_edit_file(registry, tmp_path, execution_gateway=recording)

    with gateway.bind_execution_context(_context("memorial-edit-lsp")):
        result = await registry.execute(
            "edit_file",
            {"path": "sample.py", "old_text": "x = 1", "new_text": "x = 2"},
        )

    assert result.is_error is False
    advisory = result.details["diagnostics_advisory"]
    assert advisory["status"] == "unavailable"
    assert advisory["correlation_id"] == "memorial-edit-lsp"
