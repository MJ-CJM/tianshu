"""Workspace-bound tool, policy, skill, and MCP runtime contracts."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tianshu.executor import execution_gateway as gateway
from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import ExecutionContext, bind_execution_context
from tianshu.executor.policy_hook import PolicyHook
from tianshu.executor.workspace_context import BoundWorkspace, bind_workspace
from tianshu.lsp import diagnostics as lsp_module
from tianshu.models.governance_contract import (
    ObjectiveV1,
    PermissionPolicyV1,
    RequestedGovernanceContractV1,
    WorkspacePolicyV1,
)
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.models.workspace import WorkspaceLease, WorkspaceLeaseState
from tianshu.skills.loader import SkillsLoader
from tianshu.tools import lark_cli as lark_module
from tianshu.tools.builtins import register_builtins
from tianshu.tools.policy import PolicyDecision
from tianshu.tools.registry import ToolDefinition, ToolRegistry
from tianshu.tools.skill_tools import register_skill_tools
from tianshu.tools.types import ToolTier, ok_result

_SHA = "a" * 40


def _effective():
    base = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="bound tools"),
            permissions=PermissionPolicyV1(allowed_bash_prefixes=("echo ",)),
        ),
        native_manifest(),
        probe_host_capabilities(),
    )
    workspace = WorkspacePolicyV1(
        source_id="source-main",
        base_revision=_SHA,
        staging_mode="isolated",
        apply_mode="governed",
        require_clean_source=True,
    )
    return base.model_copy(
        update={
            "workspace": workspace,
            "resolved_source_id": workspace.source_id,
            "resolved_base_revision": _SHA,
        }
    )


def _bound(staging: Path, source: Path) -> BoundWorkspace:
    staging.mkdir(parents=True, exist_ok=True)
    effective = _effective()
    lease = WorkspaceLease(
        id="lease-1",
        run_id="run-1",
        lineage_root_run_id="run-1",
        attempt=0,
        source_kind="git",
        apply_mode="governed",
        source_root=str(source),
        source_repository_id="repo-identity",
        source_git_dir=str(source / ".git"),
        source_git_dir_identity="b" * 64,
        base_revision=_SHA,
        staging_root=str(staging),
        staging_git_dir=str(staging / ".git"),
        staging_git_dir_identity="c" * 64,
        state=WorkspaceLeaseState.ACTIVE,
        state_version=2,
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
    )
    return BoundWorkspace(lease=lease, effective_contract=effective)


def _execution_context(bound: BoundWorkspace) -> ExecutionContext:
    return ExecutionContext(
        correlation_id=bound.lease.run_id,
        actor=Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Test Principal",
        ),
        effective_contract=bound.effective_contract,
        workspace_lease_id=bound.lease.id,
    )


def _definition(name: str, *, tier: ToolTier = ToolTier.T0_READONLY) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        tier=tier.value,
        side_effect=tier is not ToolTier.T0_READONLY,
    )


@pytest.mark.asyncio
async def test_same_lease_serializes_tool_calls_including_t0(tmp_path: Path) -> None:
    bound = _bound(tmp_path / "staging", tmp_path / "source")
    registry = ToolRegistry()
    active = 0
    maximum = 0

    async def observe():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0)
        active -= 1
        return ok_result("ok")

    registry.register("observe", observe, _definition("observe"))
    with bind_workspace(bound):
        results = await asyncio.gather(
            registry.execute("observe", {}),
            registry.execute("observe", {}),
        )

    assert maximum == 1
    assert all(not result.is_error for result in results)


@pytest.mark.asyncio
async def test_governed_tool_without_bound_lease_fails_before_handler(tmp_path: Path) -> None:
    bound = _bound(tmp_path / "staging", tmp_path / "source")
    registry = ToolRegistry()
    called = False

    async def mutate():
        nonlocal called
        called = True
        return ok_result("mutated")

    registry.register("mutate", mutate, _definition("mutate", tier=ToolTier.T1_WORKSPACE))
    with bind_execution_context(_execution_context(bound)):
        result = await registry.execute("mutate", {})

    assert result.is_error
    assert "bound workspace" in result.content
    assert called is False

    missing_lease_context = _execution_context(bound).model_copy(
        update={"workspace_lease_id": None}
    )
    with bind_execution_context(missing_lease_context), bind_workspace(bound):
        result = await registry.execute("mutate", {})

    assert result.is_error
    assert "lease" in result.content
    assert called is False


@pytest.mark.asyncio
async def test_mcp_tool_fails_closed_inside_isolated_workspace(tmp_path: Path) -> None:
    bound = _bound(tmp_path / "staging", tmp_path / "source")
    registry = ToolRegistry()
    called = False

    async def mcp_call():
        nonlocal called
        called = True
        return ok_result("unsafe")

    registry.register("mcp_fixture_read", mcp_call, _definition("mcp_fixture_read"))
    registry.register("mcp_helper", mcp_call, _definition("mcp_helper"))
    with bind_workspace(bound):
        result = await registry.execute("mcp_fixture_read", {})
        ordinary_result = await registry.execute("mcp_helper", {})

    assert result.is_error
    assert "MCP" in result.content
    assert not ordinary_result.is_error
    assert called is True


@pytest.mark.asyncio
async def test_builtin_file_tools_resolve_staging_root_per_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    source.mkdir()
    staging.mkdir()
    (source / "marker.txt").write_text("source text", encoding="utf-8")
    (staging / "marker.txt").write_text("staging text", encoding="utf-8")
    (staging / "staging-only.txt").write_text("needle", encoding="utf-8")
    bound = _bound(staging, source)
    registry = ToolRegistry()
    register_builtins(registry, workspace_dir=str(source))
    monkeypatch.setattr(
        "tianshu.tools.grep.process_boundary.resolve_system_adapter_executable",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("tianshu.lsp.diagnostics.is_enabled", lambda: False)

    with bind_execution_context(_execution_context(bound)), bind_workspace(bound):
        read = await registry.execute("read_file", {"path": "marker.txt"})
        written = await registry.execute(
            "write_file", {"path": "created.txt", "content": "created in staging"}
        )
        edited = await registry.execute(
            "edit_file",
            {"path": "marker.txt", "old_text": "staging", "new_text": "edited"},
        )
        listed = await registry.execute("list_dir", {"path": "."})
        found = await registry.execute("find_files", {"pattern": "staging-only.txt"})
        grepped = await registry.execute("grep", {"pattern": "needle"})

    assert read.content == "staging text"
    assert not written.is_error and not edited.is_error
    assert "staging-only.txt" in listed.content
    assert "staging-only.txt" in found.content
    assert "staging-only.txt" in grepped.content
    assert (staging / "created.txt").read_text(encoding="utf-8") == "created in staging"
    assert (staging / "marker.txt").read_text(encoding="utf-8") == "edited text"
    assert (source / "marker.txt").read_text(encoding="utf-8") == "source text"
    assert not (source / "created.txt").exists()


class _RecordingPolicyEngine:
    def __init__(self) -> None:
        self.contexts = []

    async def evaluate(self, context):
        self.contexts.append(context)
        return PolicyDecision(verdict="allow", rule_id="allow", reason="test")


class _Storage:
    def append_event(self, *_args, **_kwargs) -> None:
        return None


class _ProcessCapture:
    def __init__(self, staging: Path) -> None:
        self.staging = staging
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        if request.purpose == "grep":
            stdout = json.dumps(
                {
                    "type": "match",
                    "data": {
                        "path": {"text": str(self.staging / "search.txt")},
                        "line_number": 1,
                        "lines": {"text": "needle\n"},
                    },
                }
            )
        elif request.purpose == "lsp":
            stdout = '{"generalDiagnostics":[]}'
        elif request.purpose == "lark-cli":
            stdout = '{"ok":true}'
        else:
            stdout = "ok\n"
        receipt = SimpleNamespace(
            status="succeeded",
            correlation_id=request.correlation_id,
            exit_code=0,
            terminating_signal=None,
            stdout_truncated=False,
            stderr_truncated=False,
            stdout_incomplete=False,
        )
        receipt.model_dump = lambda **_kwargs: {}
        return SimpleNamespace(
            stdout=stdout,
            stderr="",
            error=None,
            returncode=0,
            receipt=receipt,
        )


@pytest.mark.asyncio
async def test_process_tools_bind_grants_and_requests_to_staging_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    trusted = tmp_path / "trusted"
    source.mkdir()
    staging.mkdir()
    trusted.mkdir()
    (staging / "subdir").mkdir()
    (staging / "search.txt").write_text("needle\n", encoding="utf-8")
    python_file = staging / "sample.py"
    python_file.write_text("x = 1\n", encoding="utf-8")
    executables = {}
    for adapter, name in (("grep", "rg"), ("lsp", "basedpyright")):
        executable = trusted / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        executables[adapter] = executable.resolve()
    monkeypatch.setattr(
        gateway,
        "_resolve_trusted_adapter_executable",
        lambda adapter, _root: executables[adapter],
    )
    monkeypatch.setattr(lark_module, "_resolve_bin", lambda: "/opt/bin/lark-cli")
    monkeypatch.setenv("TIANSHU_LSP_ENABLED", "1")

    bound = _bound(staging, source)
    capture = _ProcessCapture(staging)
    registry = ToolRegistry()
    register_builtins(registry, workspace_dir=str(source), execution_gateway=capture)

    with bind_execution_context(_execution_context(bound)), bind_workspace(bound):
        shell = await registry.execute("shell_exec", {"command": "echo ok", "cwd": "subdir"})
        grep = await registry.execute("grep", {"pattern": "needle"})
        lark = await lark_module.lark_cli(
            ["message", "list"],
            execution_gateway=capture,
            workspace_root=source,
        )
        lsp = await lsp_module.run_diagnostics_async(
            python_file,
            execution_gateway=capture,
            workspace_root=source,
        )

    assert not shell.is_error and not grep.is_error and not lark.is_error
    assert lsp.status == "ok"
    assert [request.purpose for request in capture.requests] == [
        "tool",
        "grep",
        "lark-cli",
        "lsp",
    ]
    assert all(request.workspace_root == bound.root for request in capture.requests)
    assert all(
        request.command_grant.workspace_lease_id == bound.lease.id
        and request.command_grant.workspace_root_digest is not None
        for request in capture.requests
    )


@pytest.mark.asyncio
async def test_policy_hook_uses_staging_root_and_fails_closed_without_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    bound = _bound(tmp_path / "staging", source)
    engine = _RecordingPolicyEngine()
    registry = ToolRegistry()
    registry.register("mutate", lambda: ok_result("ok"), _definition("mutate"))
    hook = PolicyHook(engine, source, _Storage(), registry)
    call = {
        "tool_name": "mutate",
        "tool_args": {},
        "edict": SimpleNamespace(id="edict-1"),
    }

    with bind_execution_context(_execution_context(bound)):
        denied = await hook.on_before_tool_call(**call)
    with bind_execution_context(_execution_context(bound)), bind_workspace(bound):
        allowed = await hook.on_before_tool_call(**call)

    assert denied is not None and denied.block
    assert allowed is not None and not allowed.block
    assert [context.workspace_root for context in engine.contexts] == [bound.root]


@pytest.mark.asyncio
async def test_skill_mutations_use_staging_overlay_without_touching_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    builtin = tmp_path / "builtin"
    existing = source / "skills" / "existing" / "SKILL.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "---\nname: existing\ndescription: source\n---\nsource body\n",
        encoding="utf-8",
    )
    builtin.mkdir()
    bound = _bound(staging, source)
    registry = ToolRegistry()
    register_skill_tools(
        registry,
        SkillsLoader(builtin_dir=builtin, workspace_dir=source),
        guard_agent_created=False,
    )

    with bind_execution_context(_execution_context(bound)), bind_workspace(bound):
        edited = await registry.execute(
            "skill_manage",
            {"action": "edit", "name": "existing", "content": "staged body"},
        )
        created = await registry.execute(
            "skill_manage",
            {
                "action": "create",
                "name": "new-skill",
                "content": "---\nname: new-skill\ndescription: staged\n---\nnew body\n",
            },
        )
        resource = await registry.execute(
            "skill_manage",
            {
                "action": "write_file",
                "name": "new-skill",
                "file_path": "scripts/run.py",
                "file_content": "print('staged')\n",
            },
        )
        viewed = await registry.execute("skill_view", {"name": "new-skill"})
        listed = await registry.execute("skill_list", {"query": "new-skill"})

    assert not edited.is_error and not created.is_error and not resource.is_error
    assert not viewed.is_error and "new body" in viewed.content
    assert not listed.is_error and "new-skill" in listed.content
    assert "source body" in existing.read_text(encoding="utf-8")
    assert "staged body" in (staging / "skills/existing/SKILL.md").read_text(encoding="utf-8")
    assert (staging / "skills/new-skill/SKILL.md").is_file()
    assert (staging / "skills/new-skill/scripts/run.py").is_file()
    assert not (source / "skills/new-skill").exists()
