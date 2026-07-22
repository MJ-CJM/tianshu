"""Workspace-bound tool, policy, skill, and MCP runtime contracts."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import ExecutionContext, bind_execution_context
from tianshu.executor.execution_gateway import grants as gateway_grants
from tianshu.executor.managed_tools import bind_managed_attempt_authority
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


class _ManagedEffectPassthrough:
    async def execute(self, *, invoke, invocation_id, **_kwargs):
        assert invocation_id is not None
        return await invoke(invocation_id)


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


def _write_skill(path: Path) -> Path:
    path.mkdir(parents=True)
    skill_file = path / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {path.name}\ndescription: fixture\n---\nbody\n",
        encoding="utf-8",
    )
    return path


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for entry in sorted(root.rglob("*")):
        relative = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            entries.append((relative, "symlink", str(entry.readlink())))
        elif entry.is_dir():
            entries.append((relative, "dir", ""))
        else:
            entries.append((relative, "file", entry.read_bytes().hex()))
    return tuple(entries)


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
    registry.set_managed_effect_executor(_ManagedEffectPassthrough())
    monkeypatch.setattr(
        "tianshu.tools.grep.process_boundary.resolve_system_adapter_executable",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr("tianshu.lsp.diagnostics.is_enabled", lambda: False)

    authority = AttemptAuthority(
        attempt_id="attempt-workspace",
        memorial_id="memorial-workspace",
        owner_id="worker-workspace",
        fencing_token=1,
    )
    with (
        bind_execution_context(_execution_context(bound)),
        bind_workspace(bound),
    ):
        read = await registry.execute("read_file", {"path": "marker.txt"})
        with bind_managed_attempt_authority(authority):
            written = await registry.execute(
                "write_file",
                {"path": "created.txt", "content": "created in staging"},
                invocation_id="workspace-write-1",
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
        gateway_grants,
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
async def test_skill_mutations_without_governed_service_leave_workspaces_unchanged(
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

    assert edited.is_error and edited.content == "governed_skill_service_required"
    assert created.is_error and created.content == "governed_skill_service_required"
    assert resource.is_error and resource.content == "governed_skill_service_required"
    assert viewed.is_error and "not found" in viewed.content.lower()
    assert not listed.is_error and "new-skill" not in listed.content
    assert "source body" in existing.read_text(encoding="utf-8")
    assert not (staging / "skills/existing/SKILL.md").exists()
    assert not (staging / "skills/new-skill/SKILL.md").exists()
    assert not (staging / "skills/new-skill/scripts/run.py").exists()
    assert not (source / "skills/new-skill").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "symlink_kind",
    ("skills-root", "skill-dir", "skill-file"),
)
async def test_skill_overlay_rejects_symlinked_write_paths(
    tmp_path: Path,
    symlink_kind: str,
) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    builtin = tmp_path / "builtin"
    outside = tmp_path / "outside"
    for path in (source, staging, builtin, outside):
        path.mkdir()

    if symlink_kind == "skills-root":
        (staging / "skills").symlink_to(outside, target_is_directory=True)
        arguments = {
            "action": "create",
            "name": "escaped",
            "content": "---\nname: escaped\ndescription: escaped\n---\nbody\n",
        }
        outside_target = outside / "escaped" / "SKILL.md"
        original_content = None
    else:
        skills_root = staging / "skills"
        skills_root.mkdir()
        outside_skill = outside / "existing"
        outside_skill.mkdir()
        outside_target = outside_skill / "SKILL.md"
        outside_target.write_text(
            "---\nname: existing\ndescription: outside\n---\noutside body\n",
            encoding="utf-8",
        )
        original_content = outside_target.read_text(encoding="utf-8")
        if symlink_kind == "skill-dir":
            (skills_root / "existing").symlink_to(outside_skill, target_is_directory=True)
        else:
            staged_skill = skills_root / "existing"
            staged_skill.mkdir()
            (staged_skill / "SKILL.md").symlink_to(outside_target)
        arguments = {
            "action": "edit",
            "name": "existing",
            "content": "attempted escape",
        }

    bound = _bound(staging, source)
    registry = ToolRegistry()
    register_skill_tools(
        registry,
        SkillsLoader(builtin_dir=builtin, workspace_dir=source),
        guard_agent_created=False,
    )

    with bind_execution_context(_execution_context(bound)), bind_workspace(bound):
        result = await registry.execute("skill_manage", arguments)

    assert result.is_error
    if original_content is None:
        assert not outside_target.exists()
    else:
        assert outside_target.read_text(encoding="utf-8") == original_content


@pytest.mark.asyncio
async def test_skill_overlay_delete_rejects_symlinked_writable_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    staging = tmp_path / "staging"
    builtin = tmp_path / "builtin"
    outside = tmp_path / "outside"
    for path in (source, staging, builtin, outside):
        path.mkdir()
    outside_skill = _write_skill(outside / "existing")
    before = _tree_snapshot(outside)
    (staging / "skills").symlink_to(outside, target_is_directory=True)
    bound = _bound(staging, source)
    registry = ToolRegistry()
    register_skill_tools(
        registry,
        SkillsLoader(builtin_dir=builtin, workspace_dir=source),
        guard_agent_created=False,
    )

    with bind_execution_context(_execution_context(bound)), bind_workspace(bound):
        result = await registry.execute(
            "skill_manage",
            {"action": "delete", "name": "existing"},
        )

    assert result.is_error
    assert outside_skill.is_dir()
    assert _tree_snapshot(outside) == before


@pytest.mark.parametrize("mutation", ("delete", "archive", "restore"))
def test_workspace_overlay_mutations_reject_symlinked_writable_root(
    tmp_path: Path,
    mutation: str,
) -> None:
    staging = tmp_path / "staging"
    builtin = tmp_path / "builtin"
    outside = tmp_path / "outside"
    for path in (staging, builtin, outside):
        path.mkdir()
    if mutation == "restore":
        _write_skill(outside / ".archive" / "existing")
    else:
        _write_skill(outside / "existing")
    before = _tree_snapshot(outside)
    (staging / "skills").symlink_to(outside, target_is_directory=True)
    overlay = SkillsLoader(builtin_dir=builtin).for_workspace_overlay(staging)

    operation = {
        "delete": overlay.delete_skill,
        "archive": overlay.archive_skill,
        "restore": overlay.restore_skill,
    }[mutation]
    with pytest.raises(ValueError, match="symlink"):
        operation("existing")

    assert _tree_snapshot(outside) == before


@pytest.mark.parametrize(
    "mutation_path",
    (
        "delete-source",
        "archive-source",
        "archive-root",
        "archive-target",
        "restore-source",
        "restore-root",
        "restore-target",
    ),
)
def test_workspace_overlay_mutations_reject_symlinked_source_or_target(
    tmp_path: Path,
    mutation_path: str,
) -> None:
    staging = tmp_path / "staging"
    skills = staging / "skills"
    builtin = tmp_path / "builtin"
    outside = tmp_path / "outside"
    for path in (skills, builtin, outside):
        path.mkdir(parents=True)
    name = "existing"

    if mutation_path in {"delete-source", "archive-source"}:
        outside_skill = _write_skill(outside / "source")
        (skills / name).symlink_to(outside_skill, target_is_directory=True)
    elif mutation_path == "archive-root":
        _write_skill(skills / name)
        outside_archive = outside / "archive"
        outside_archive.mkdir()
        (skills / ".archive").symlink_to(outside_archive, target_is_directory=True)
    elif mutation_path == "archive-target":
        _write_skill(skills / name)
        archive = skills / ".archive"
        archive.mkdir()
        outside_target = _write_skill(outside / "target")
        (archive / name).symlink_to(outside_target, target_is_directory=True)
    elif mutation_path == "restore-source":
        archive = skills / ".archive"
        archive.mkdir()
        outside_skill = _write_skill(outside / "source")
        (archive / name).symlink_to(outside_skill, target_is_directory=True)
    elif mutation_path == "restore-root":
        outside_archive = outside / "archive"
        _write_skill(outside_archive / name)
        (skills / ".archive").symlink_to(outside_archive, target_is_directory=True)
    else:
        _write_skill(skills / ".archive" / name)
        outside_target = _write_skill(outside / "target")
        (skills / name).symlink_to(outside_target, target_is_directory=True)

    before_outside = _tree_snapshot(outside)
    before_staging = _tree_snapshot(staging)
    overlay = SkillsLoader(builtin_dir=builtin).for_workspace_overlay(staging)
    mutation = mutation_path.split("-", 1)[0]
    operation = {
        "delete": overlay.delete_skill,
        "archive": overlay.archive_skill,
        "restore": overlay.restore_skill,
    }[mutation]

    with pytest.raises(ValueError, match="symlink"):
        operation(name)

    assert _tree_snapshot(outside) == before_outside
    assert _tree_snapshot(staging) == before_staging
