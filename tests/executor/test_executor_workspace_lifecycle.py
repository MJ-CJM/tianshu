from __future__ import annotations

import asyncio
import contextlib
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.adapters import DelegatingExecutorAdapter
from tianshu.executor.agent import AgentResult
from tianshu.executor.capabilities import (
    CapabilityState,
    HostCapabilityProbeV1,
    claude_code_manifest,
    codex_manifest,
    native_manifest,
)
from tianshu.executor.executor import Executor
from tianshu.executor.git_backend import GitBackend
from tianshu.executor.workspace_context import (
    get_bound_workspace,
    require_bound_workspace,
    resolve_workspace_root,
)
from tianshu.executor.workspace_runtime import WorkspaceContractError
from tianshu.executor.workspace_service import WorkspaceApplyError, WorkspaceService
from tianshu.kernel.hooks import HookRegistry, HookType
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.governance_contract import (
    LegacyEdictGovernanceMapper,
    RecoveryPolicyV1,
    WorkspacePolicyV1,
)
from tianshu.models.plan import Plan, PlanTask
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.models.workspace import WorkspaceLeaseState

_GIT = shutil.which("git") or "git"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        [_GIT, *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Tianshu Test")
    _git(path, "config", "user.email", "tianshu@example.test")
    (path / "tracked.txt").write_text("base\n")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-qm", "base")
    return path


def _probe() -> HostCapabilityProbeV1:
    return HostCapabilityProbeV1(
        probe_id="workspace-runtime-test",
        os_name="test",
        architecture="test",
        git_available=True,
        process_groups_available=True,
        sandbox_backend=None,
    )


def _promoted_manifest(manifest):
    promoted = {"pre_run_restore_point"}
    return manifest.model_copy(
        update={
            "capabilities": tuple(
                declaration.model_copy(update={"state": CapabilityState.ENFORCED})
                if declaration.capability in promoted
                else declaration
                for declaration in manifest.capabilities
            )
        }
    )


def _manifest():
    return _promoted_manifest(native_manifest())


def _reviewer() -> Principal:
    return Principal(
        id="workspace-reviewer",
        kind=PrincipalKind.HUMAN,
        display_name="Workspace Reviewer",
        scopes=frozenset({"workspace:apply"}),
    )


def _governed_edict(
    *,
    retry_limit: int = 0,
    timeout_seconds: int = 30,
    outer_loop: bool = False,
) -> Edict:
    base = Edict(
        goal="change tracked file",
        submitter="workspace-test",
        runtime={"retry_limit": retry_limit, "timeout_seconds": timeout_seconds},
        acceptance=AcceptanceCriteria() if outer_loop else None,
    )
    requested = LegacyEdictGovernanceMapper.from_edict(
        base,
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "workspace": WorkspacePolicyV1(
                source_id="workspace-main",
                base_revision="HEAD",
                staging_mode="isolated",
                apply_mode="governed",
                require_clean_source=True,
            ),
            "recovery": RecoveryPolicyV1(require_restore_point=True),
        }
    )
    return base.model_copy(update={"governance_contract": requested})


def _executor(
    *,
    storage,
    config_manager,
    hooks: HookRegistry,
    service: WorkspaceService,
    source: Path,
    agent,
) -> Executor:
    executor = Executor(
        event_bus=EventBus(),
        storage=storage,
        config_manager=config_manager,
        hook_registry=hooks,
        workspace_service=service,
        workspace_sources={"workspace-main": source},
    )
    executor.set_agent(agent)
    executor._adapter_registry.replace(  # noqa: SLF001 - exact adapter E2E seam
        DelegatingExecutorAdapter(
            adapter_id="native",
            manifest=_manifest(),
            delegate=agent,
            probe_factory=_probe,
        )
    )
    return executor


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_single_run_restores_before_hooks_and_captures_staging_changes(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    hooks = HookRegistry()
    observed: list[str] = []

    original_create = service.create_lease

    async def create_lease(request):
        current = storage.get_memorial(request.run_id)
        assert current is not None and current.status is TaskStatus.SUBMITTED
        lease = await original_create(request)
        assert storage.get_restore_point_for_lease(lease.id) is not None
        observed.append("lease")
        return lease

    service.create_lease = create_lease

    async def before_agent(*, memorial: Memorial, **_kwargs):
        lease = storage.get_workspace_lease_by_run(memorial.id)
        assert lease is not None and lease.state is WorkspaceLeaseState.ACTIVE
        assert storage.get_restore_point_for_lease(lease.id) is not None
        observed.append("hook")

    hooks.register(HookType.BEFORE_AGENT_START, before_agent)

    async def after_agent(*, memorial: Memorial, **_kwargs):
        bound = require_bound_workspace(run_id=memorial.id)
        (bound.root / "end-hook.txt").write_text("written by end hook\n")
        observed.append("end-hook")

    hooks.register(HookType.AGENT_END, after_agent)

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        bound = require_bound_workspace(run_id=memorial.id)
        assert bound.lease.base_revision == _git(source, "rev-parse", "HEAD")
        (bound.root / "tracked.txt").write_text("changed in staging\n")
        observed.append("agent")
        return AgentResult(
            status=TaskStatus.COMPLETED,
            result="done",
            usage=UsageSummary(total_tokens=1),
        )

    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=hooks,
        service=service,
        source=source,
        agent=agent,
    )
    edict = _governed_edict()
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

    assert observed == ["lease", "hook", "agent", "end-hook"]
    assert get_bound_workspace() is None
    assert (source / "tracked.txt").read_text() == "base\n"
    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.ACTIVE
    changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
    assert changes is not None
    assert [change.new_path for change in changes.changes] == ["end-hook.txt", "tracked.txt"]
    persisted = storage.get_effective_governance_contract(memorial.id)
    assert persisted is not None
    assert persisted.resolved_base_revision == _git(source, "rev-parse", "HEAD")
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_terminal_memorial_is_visible_only_after_changes_are_readable(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    """终态对外可见的那一刻，变更集必须已经可读。

    客户端 `GET /api/edicts/{id}/memorial` 看到 completed 就会立刻去取
    `GET /api/workspace-runs/{run_id}/changes`。若终态先落库、变更集在其后才捕获，
    这个再正常不过的调用序列会撞上 `changes_unavailable`——一个产品可见的竞态。
    这里在终态写库的**那一刻**同步去读变更集，不轮询（轮询会把竞态盖回去）。
    """
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        bound = require_bound_workspace(run_id=memorial.id)
        (bound.root / "tracked.txt").write_text("raced\n")
        return AgentResult(status=TaskStatus.COMPLETED, result="done")

    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    edict = _governed_edict()
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)

    # 在终态落库的**那一刻**同步查库：这正是一个真实客户端看到 completed 之后
    # 立刻取 changes 时，服务端会读到的东西。不用 create_task/轮询——那会把竞态盖回去。
    changes_at_terminal: list[object] = []
    real_update = storage.update_memorial

    def update_memorial_spy(updated: Memorial):
        result = real_update(updated)
        if updated.status == TaskStatus.COMPLETED:
            lease = storage.get_workspace_lease_by_run(updated.id)
            changes_at_terminal.append(
                storage.get_latest_canonical_change_set_for_lease(lease.id) if lease else None
            )
        return result

    storage.update_memorial = update_memorial_spy  # type: ignore[method-assign]
    try:
        await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)
    finally:
        storage.update_memorial = real_update  # type: ignore[method-assign]

    assert changes_at_terminal, "终态从未落库"
    change_set = changes_at_terminal[0]
    assert change_set is not None, (
        "memorial 已 completed，变更集却还没落库——客户端此刻取 changes 会拿到 changes_unavailable"
    )
    assert {change.new_path for change in change_set.changes} == {"tracked.txt"}

    # REST 读路径同样可用（同一事实的另一面）。
    served = await service.get_run_changes(memorial.id)
    assert served.id == change_set.id
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.parametrize(
    "apply_case", ["success", "rollback"], ids=["native-success", "native-rollback"]
)
async def test_native_run_to_governed_apply_uses_production_manifest(
    storage,
    config_manager,
    tmp_path: Path,
    apply_case: str,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        bound = require_bound_workspace(run_id=memorial.id)
        (bound.root / "tracked.txt").write_text("native applied\n")
        return AgentResult(status=TaskStatus.COMPLETED, result="done")

    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    executor._adapter_registry.replace(  # noqa: SLF001 - production manifest E2E seam
        DelegatingExecutorAdapter(
            adapter_id="native",
            manifest=native_manifest(),
            delegate=agent,
            probe_factory=_probe,
        )
    )
    edict = _governed_edict()
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)
    decision, token = await service.issue_apply_decision(
        memorial.id,
        _reviewer(),
        "native E2E reviewed",
        timedelta(minutes=5),
    )
    if apply_case == "success":
        receipt = await service.apply(memorial.id, decision.id, token, _reviewer())
        assert receipt.outcome == "succeeded"
        assert (source / "tracked.txt").read_text() == "native applied\n"
    else:
        service._apply_failure_injector = (  # noqa: SLF001 - exact rollback E2E seam
            lambda stage: (
                (_ for _ in ()).throw(RuntimeError("native rollback E2E"))
                if stage == "after_materialize"
                else None
            )
        )
        with pytest.raises(WorkspaceApplyError) as caught:
            await service.apply(memorial.id, decision.id, token, _reviewer())
        assert caught.value.code == "materialization_failed"
        receipt = storage.get_apply_receipt_for_decision(decision.id)
        assert receipt is not None and receipt.outcome == "failed"
        assert receipt.rollback_status == "succeeded"
        assert (source / "tracked.txt").read_text() == "base\n"
    assert storage.get_workspace_lease_by_run(memorial.id).state is WorkspaceLeaseState.CLOSED


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_terminal_event_observes_captured_workspace_after_context_reset(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    hooks = HookRegistry()

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        bound = require_bound_workspace(run_id=memorial.id)
        (bound.root / "tracked.txt").write_text("terminal evidence\n")
        return AgentResult(status=TaskStatus.COMPLETED, result="done")

    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=hooks,
        service=service,
        source=source,
        agent=agent,
    )
    edict = _governed_edict()
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)
    observed: list[object] = []
    started_observed: list[object] = []

    async def on_started(_event):
        started_observed.append(get_bound_workspace())

        async def inherited_context() -> None:
            started_observed.append(get_bound_workspace())

        await asyncio.create_task(inherited_context())

    async def on_completed(_event):
        observed.append(get_bound_workspace())
        lease = storage.get_workspace_lease_by_run(memorial.id)
        assert lease is not None
        changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
        assert changes is not None and len(changes.changes) == 1

    executor._bus.on(  # noqa: SLF001
        "execution.started",
        on_started,
        consumer_name="test.execution_started.v1",
    )
    executor._bus.on(  # noqa: SLF001
        "execution.completed",
        on_completed,
        consumer_name="test.execution_completed.v1",
    )

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

    assert observed == [None]
    assert started_observed == [None, None]
    await service.shutdown()


@pytest.mark.parametrize(
    ("result_status", "write_change", "expected_change_count"),
    [
        (TaskStatus.COMPLETED, False, 0),
        (TaskStatus.FAILED, True, 1),
        (TaskStatus.CANCELLED, True, 1),
    ],
)
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_non_preview_terminal_outcomes_capture_then_close(
    storage,
    config_manager,
    tmp_path: Path,
    result_status: TaskStatus,
    write_change: bool,
    expected_change_count: int,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    capture_states: list[WorkspaceLeaseState] = []
    original_capture = service.capture_change_set

    async def capture_change_set(lease_id: str, *, run_id: str):
        current = storage.get_workspace_lease(lease_id)
        assert current is not None
        capture_states.append(current.state)
        return await original_capture(lease_id, run_id=run_id)

    service.capture_change_set = capture_change_set

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        bound = require_bound_workspace(run_id=memorial.id)
        if write_change:
            (bound.root / "tracked.txt").write_text("terminal change\n")
        return AgentResult(
            status=result_status,
            result="original result",
            error="agent failed" if result_status is TaskStatus.FAILED else None,
        )

    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    edict = _governed_edict()
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
    assert changes is not None and len(changes.changes) == expected_change_count
    assert capture_states == [WorkspaceLeaseState.ACTIVE]
    assert storage.get_memorial(memorial.id).result == "original result"
    assert (source / "tracked.txt").read_text() == "base\n"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_hook_denial_and_timeout_close_empty_workspace(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    from tianshu.kernel.hooks import HookResult

    for case in ("denied", "timeout"):
        source = _repository(tmp_path / f"source-{case}")
        service = WorkspaceService(storage, GitBackend(), tmp_path / f"leases-{case}")
        hooks = HookRegistry()
        agent = AsyncMock()
        if case == "denied":

            async def deny(**_kwargs):
                return HookResult(block=True, reason="policy denied")

            hooks.register(HookType.BEFORE_AGENT_START, deny)
        else:

            async def wait_forever(*_args, **_kwargs):
                await asyncio.Event().wait()

            agent.execute.side_effect = wait_forever
        executor = _executor(
            storage=storage,
            config_manager=config_manager,
            hooks=hooks,
            service=service,
            source=source,
            agent=agent,
        )
        edict = _governed_edict(timeout_seconds=1)
        storage.save_edict(edict)
        memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
        storage.save_memorial(memorial)

        await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

        loaded = storage.get_memorial(memorial.id)
        assert loaded.status is TaskStatus.FAILED
        assert "denied" in loaded.error if case == "denied" else "timed out" in loaded.error
        lease = storage.get_workspace_lease_by_run(memorial.id)
        assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
        changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
        assert changes is not None and changes.changes == ()
        if case == "denied":
            agent.execute.assert_not_awaited()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_task_cancellation_cleans_workspace_and_propagates(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    started = asyncio.Event()
    end_started = asyncio.Event()
    release_end = asyncio.Event()
    end_finished = asyncio.Event()
    hooks = HookRegistry()

    async def finish_end_hook(*, memorial: Memorial, **_kwargs):
        end_started.set()
        await release_end.wait()
        bound = require_bound_workspace(run_id=memorial.id)
        (bound.root / "end-hook.txt").write_text("finished under shield\n")
        end_finished.set()

    hooks.register(HookType.AGENT_END, finish_end_hook)
    cancelled_events: list[object] = []

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        require_bound_workspace(run_id=memorial.id)
        started.set()
        await asyncio.Event().wait()

    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=hooks,
        service=service,
        source=source,
        agent=agent,
    )

    async def on_cancelled(event):
        cancelled_events.append(event)

    executor._bus.on(  # noqa: SLF001
        "execution.cancelled",
        on_cancelled,
        consumer_name="test.execution_cancelled.v1",
    )
    edict = _governed_edict()
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)

    task = asyncio.create_task(
        executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)
    )
    await started.wait()
    task.cancel()
    await end_started.wait()
    task.cancel()
    release_end.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert end_finished.is_set()
    assert get_bound_workspace() is None
    assert storage.get_memorial(memorial.id).status is TaskStatus.CANCELLED
    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
    assert changes is not None
    assert [change.new_path for change in changes.changes] == ["end-hook.txt"]
    assert len(cancelled_events) == 1
    assert cancelled_events[0].memorial_id == memorial.id
    assert cancelled_events[0].payload["workspace_change_count"] == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_workspace_finalization_errors_are_evidence_not_result_overrides(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        require_bound_workspace(run_id=memorial.id)
        return AgentResult(status=TaskStatus.COMPLETED, result="keep this result")

    async def fail_capture(_lease_id: str, *, run_id: str):
        raise OSError(f"capture failed for {run_id}")

    service.capture_change_set = fail_capture
    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    edict = _governed_edict()
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)
    payloads: list[dict] = []

    async def completed(event):
        payloads.append(event.payload)

    executor._bus.on(  # noqa: SLF001
        "execution.completed",
        completed,
        consumer_name="test.execution_completed.v1",
    )

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

    loaded = storage.get_memorial(memorial.id)
    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.result == "keep this result"
    assert payloads[0]["workspace_capture_error"].startswith("capture failed")
    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.ACTIVE
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_outer_loop_binds_staging_before_orchestrator_context(
    storage,
    config_manager,
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    agent = AsyncMock()
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    executor.set_orchestrator_context(
        SimpleNamespace(agent=object(), workspace_root=source, execution_context=None)
    )
    edict = _governed_edict(outer_loop=True)
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)

    async def run(_edict, active_memorial: Memorial, context):
        bound = require_bound_workspace(run_id=active_memorial.id)
        assert active_memorial.status is TaskStatus.RUNNING
        assert context.workspace_root == bound.root
        assert context.execution_context.workspace_lease_id == bound.lease.id
        (bound.root / "tracked.txt").write_text("outer staging\n")
        return SimpleNamespace(
            status=TaskStatus.COMPLETED,
            final_output="outer done",
            error=None,
        )

    monkeypatch.setattr("tianshu.executor.orchestrator.run", run)

    await executor._execute_outer_loop(  # noqa: SLF001 - exact outer lifecycle E2E
        storage.get_edict(edict.id),
        memorial,
    )

    assert get_bound_workspace() is None
    assert (source / "tracked.txt").read_text() == "base\n"
    loaded = storage.get_memorial(memorial.id)
    assert loaded.status is TaskStatus.COMPLETED
    assert loaded.final_output == "outer done"
    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.ACTIVE
    changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
    assert changes is not None and len(changes.changes) == 1
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_auto_retry_starts_after_reset_with_distinct_contiguous_lease(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    calls = 0

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        nonlocal calls
        calls += 1
        bound = require_bound_workspace(run_id=memorial.id)
        (bound.root / "tracked.txt").write_text(f"attempt {calls}\n")
        if calls == 1:
            return AgentResult(status=TaskStatus.FAILED, error="retry me")
        return AgentResult(status=TaskStatus.COMPLETED, result="retried")

    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    prepare_contexts: list[object] = []
    original_prepare = executor._workspace_runtime.prepare  # noqa: SLF001

    async def prepare(effective, memorial):
        prepare_contexts.append(get_bound_workspace())
        return await original_prepare(effective, memorial)

    executor._workspace_runtime.prepare = prepare  # noqa: SLF001
    edict = _governed_edict(retry_limit=2)
    storage.save_edict(edict)
    first = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(first)

    await executor.execute_edict(storage.get_edict(edict.id), memorial=first)
    while executor.running_tasks:
        await asyncio.gather(*tuple(executor.running_tasks))

    memorials = sorted(storage.list_memorials_by_edict(edict.id), key=lambda item: item.attempt)
    assert [item.status for item in memorials] == [TaskStatus.FAILED, TaskStatus.COMPLETED]
    assert memorials[1].parent_memorial_id == memorials[0].id
    first_lease = storage.get_workspace_lease_by_run(memorials[0].id)
    second_lease = storage.get_workspace_lease_by_run(memorials[1].id)
    assert first_lease is not None and first_lease.state is WorkspaceLeaseState.CLOSED
    assert second_lease is not None and second_lease.state is WorkspaceLeaseState.ACTIVE
    assert second_lease.id != first_lease.id
    assert second_lease.lineage_root_run_id == first_lease.run_id
    assert second_lease.parent_run_id == first_lease.run_id
    assert second_lease.attempt == first_lease.attempt + 1
    assert prepare_contexts == [None, None]
    assert (source / "tracked.txt").read_text() == "base\n"
    await service.shutdown()


@pytest.mark.parametrize("failure", ["bind", "persist"])
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_post_lease_startup_failure_cleans_before_running(
    storage,
    config_manager,
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    agent = AsyncMock()
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    if failure == "bind":

        def fail_bind(*_args, **_kwargs):
            raise RuntimeError("final bind failed")

        monkeypatch.setattr(executor._adapter_registry, "bind_effective", fail_bind)  # noqa: SLF001
    else:

        def fail_persist(*_args, **_kwargs):
            raise RuntimeError("effective persist failed")

        monkeypatch.setattr(storage, "save_effective_governance_contract", fail_persist)

    edict = _governed_edict()
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

    loaded = storage.get_memorial(memorial.id)
    assert loaded.status is TaskStatus.FAILED
    assert loaded.started_at is None
    assert failure in loaded.error
    agent.execute.assert_not_awaited()
    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED


@pytest.mark.parametrize("failure", ["empty_base", "service_value_error"])
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_workspace_materialization_failure_rejects_before_running(
    storage,
    config_manager,
    tmp_path: Path,
    failure: str,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    agent = AsyncMock()
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    edict = _governed_edict()
    if failure == "empty_base":
        workspace = edict.governance_contract.workspace.model_copy(update={"base_revision": ""})
        edict = edict.model_copy(
            update={
                "governance_contract": edict.governance_contract.model_copy(
                    update={"workspace": workspace}
                )
            }
        )
    else:
        service.create_lease = AsyncMock(side_effect=ValueError("invalid Git ref"))
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)
    rejected: list[dict] = []

    async def on_rejected(event):
        rejected.append(event.payload)

    executor._bus.on(  # noqa: SLF001
        "execution.rejected",
        on_rejected,
        consumer_name="test.execution_rejected.v1",
    )

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

    loaded = storage.get_memorial(memorial.id)
    assert loaded.status is TaskStatus.FAILED
    assert loaded.started_at is None
    assert rejected == [
        {
            "code": "workspace_contract_rejected",
            "error": loaded.error,
        }
    ]
    if failure == "empty_base":
        assert "explicit non-empty base" in loaded.error
    else:
        assert "workspace lease creation failed" in loaded.error
    agent.execute.assert_not_awaited()
    assert storage.get_workspace_lease_by_run(memorial.id) is None
    await service.shutdown()


@pytest.mark.parametrize("execution_path", ["single", "outer", "dag", "retry"])
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_pre_running_cancellation_persists_terminal_and_closes_lease(
    storage,
    config_manager,
    tmp_path: Path,
    execution_path: str,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=AsyncMock(),
    )
    edict = _governed_edict(outer_loop=execution_path == "outer")
    storage.save_edict(edict)
    root = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(root)
    plan = Plan(tasks=[PlanTask(task_id="one", description="one")])

    if execution_path == "outer":
        executor.set_orchestrator_context(
            SimpleNamespace(agent=object(), workspace_root=source, execution_context=None)
        )
    elif execution_path in {"dag", "retry"}:

        class Scheduler:
            async def run(
                self, _edict, execution, *, prepared_executor, persist_root_terminal=True
            ):
                require_bound_workspace(run_id=prepared_executor.prepared.run_id)
                active_root = storage.get_memorial(execution.root_memorial_id)
                active_root.status = TaskStatus.FAILED
                active_root.error = "initial failure"
                active_root.completed_at = datetime.now(UTC)
                storage.update_memorial(active_root)
                storage.update_dag_node_status(
                    execution.id,
                    "one",
                    "failed",
                    error=active_root.error,
                )
                storage.update_dag_execution_status(execution.id, "failed")

        executor.set_dag_scheduler(Scheduler())

    if execution_path == "retry":
        await executor._execute_dag(edict, plan, memorial=root)  # noqa: SLF001
        execution = storage.get_dag_by_edict(edict.id)
        assert execution is not None

    lease_created = asyncio.Event()
    original_create = service.create_lease

    async def create_then_wait(request):
        lease = await original_create(request)
        lease_created.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await service.close_lease(lease.id, run_id=lease.run_id)
            raise

    service.create_lease = create_then_wait
    terminal_ids: list[str] = []

    async def on_cancelled(event):
        terminal_ids.append(event.memorial_id)

    executor._bus.on(  # noqa: SLF001
        "execution.cancelled",
        on_cancelled,
        consumer_name="test.execution_cancelled.v1",
    )

    if execution_path == "single":
        coroutine = executor.execute_edict(edict, memorial=root)
    elif execution_path == "outer":
        coroutine = executor._execute_outer_loop(edict, root)  # noqa: SLF001
    elif execution_path == "dag":
        coroutine = executor._execute_dag(edict, plan, memorial=root)  # noqa: SLF001
    else:
        coroutine = executor.retry_dag(execution.id)
    task = asyncio.create_task(coroutine)
    await lease_created.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    memorials = storage.list_memorials_by_edict(edict.id)
    cancelled = max(memorials, key=lambda item: item.attempt)
    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.started_at is None
    assert cancelled.completed_at is not None
    assert terminal_ids == [cancelled.id]
    lease = storage.get_workspace_lease_by_run(cancelled.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    assert get_bound_workspace() is None
    if execution_path == "retry":
        unchanged = storage.get_dag_execution(execution.id)
        assert unchanged is not None and unchanged.root_memorial_id == root.id
        assert unchanged.status == "failed"
    await service.shutdown()


@pytest.mark.parametrize("outcome", ["error", "cancel"])
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_outer_loop_failure_and_cancellation_capture_then_close(
    storage,
    config_manager,
    tmp_path: Path,
    monkeypatch,
    outcome: str,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=AsyncMock(),
    )
    executor.set_orchestrator_context(
        SimpleNamespace(agent=object(), workspace_root=source, execution_context=None)
    )
    edict = _governed_edict(outer_loop=True)
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)
    started = asyncio.Event()
    capture_started = asyncio.Event()
    release_capture = asyncio.Event()
    cancelled_events: list[object] = []

    if outcome == "cancel":
        original_capture = service.capture_change_set

        async def delayed_capture(lease_id: str, *, run_id: str):
            capture_started.set()
            await release_capture.wait()
            return await original_capture(lease_id, run_id=run_id)

        service.capture_change_set = delayed_capture

    async def on_cancelled(event):
        cancelled_events.append(event)

    executor._bus.on(  # noqa: SLF001
        "execution.cancelled",
        on_cancelled,
        consumer_name="test.execution_cancelled.v1",
    )

    async def run(_edict, active_memorial: Memorial, _context):
        bound = require_bound_workspace(run_id=active_memorial.id)
        (bound.root / "tracked.txt").write_text("outer terminal\n")
        if outcome == "error":
            raise RuntimeError("outer failed")
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("tianshu.executor.orchestrator.run", run)

    if outcome == "error":
        await executor._execute_outer_loop(storage.get_edict(edict.id), memorial)  # noqa: SLF001
    else:
        task = asyncio.create_task(
            executor._execute_outer_loop(storage.get_edict(edict.id), memorial)  # noqa: SLF001
        )
        await started.wait()
        task.cancel()
        await capture_started.wait()
        task.cancel()
        release_capture.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    expected = TaskStatus.FAILED if outcome == "error" else TaskStatus.CANCELLED
    assert storage.get_memorial(memorial.id).status is expected
    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
    assert changes is not None and len(changes.changes) == 1
    assert get_bound_workspace() is None
    assert (source / "tracked.txt").read_text() == "base\n"
    if outcome == "cancel":
        assert len(cancelled_events) == 1
        assert cancelled_events[0].memorial_id == memorial.id
        assert cancelled_events[0].payload["workspace_change_count"] == 1


@pytest.mark.parametrize(
    ("adapter_id", "manifest_factory", "apply_case"),
    [
        ("keqing:claude-code", claude_code_manifest, "success"),
        ("keqing:claude-code", claude_code_manifest, "rollback"),
        ("keqing:codex", codex_manifest, "success"),
        ("keqing:codex", codex_manifest, "rollback"),
    ],
    ids=[
        "keqing:claude-code-success",
        "keqing:claude-code-rollback",
        "keqing:codex-success",
        "keqing:codex-rollback",
    ],
)
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_lease_backed_keqing_run_to_governed_apply_uses_production_manifest(
    storage,
    config_manager,
    tmp_path: Path,
    adapter_id,
    manifest_factory,
    apply_case,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=AsyncMock(),
    )
    legacy_root = tmp_path / "legacy-keqing"
    executor._keqing._root = legacy_root  # noqa: SLF001 - legacy path regression seam

    async def execute(edict, *, memorial: Memorial, **_kwargs):
        bound = require_bound_workspace(run_id=memorial.id)
        work = resolve_workspace_root(executor._keqing.work_dir(edict.id))  # noqa: SLF001
        assert work == bound.root
        (work / "tracked.txt").write_text("keqing staging\n")
        return AgentResult(status=TaskStatus.COMPLETED, result="keqing done")

    executor._keqing.execute = AsyncMock(side_effect=execute)  # noqa: SLF001
    executor._adapter_registry.replace(  # noqa: SLF001
        DelegatingExecutorAdapter(
            adapter_id=adapter_id,
            manifest=manifest_factory(),
            delegate=executor._keqing,  # noqa: SLF001
            probe_factory=_probe,
        )
    )
    base = Edict(
        goal="keqing change",
        submitter="workspace-test",
        runtime={"executor": adapter_id},
    )
    requested = LegacyEdictGovernanceMapper.from_edict(
        base,
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "workspace": WorkspacePolicyV1(
                source_id="workspace-main",
                base_revision="HEAD",
                staging_mode="isolated",
                apply_mode="governed",
                require_clean_source=True,
            ),
            "recovery": RecoveryPolicyV1(require_restore_point=True),
        }
    )
    edict = base.model_copy(update={"governance_contract": requested})
    storage.save_edict(edict)
    memorial = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(memorial)

    await executor.execute_edict(storage.get_edict(edict.id), memorial=memorial)

    assert not executor._keqing.work_dir(edict.id).exists()  # noqa: SLF001
    assert storage.list_shadow_snapshots(edict.id) == []
    assert (source / "tracked.txt").read_text() == "base\n"
    lease = storage.get_workspace_lease_by_run(memorial.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.ACTIVE
    changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
    assert changes is not None and len(changes.changes) == 1
    decision, token = await service.issue_apply_decision(
        memorial.id,
        _reviewer(),
        "keqing E2E reviewed",
        timedelta(minutes=5),
    )
    if apply_case == "success":
        receipt = await service.apply(memorial.id, decision.id, token, _reviewer())
        assert receipt.outcome == "succeeded"
        assert (source / "tracked.txt").read_text() == "keqing staging\n"
    else:
        service._apply_failure_injector = (  # noqa: SLF001 - exact rollback E2E seam
            lambda stage: (
                (_ for _ in ()).throw(RuntimeError("keqing rollback E2E"))
                if stage == "after_materialize"
                else None
            )
        )
        with pytest.raises(WorkspaceApplyError) as caught:
            await service.apply(memorial.id, decision.id, token, _reviewer())
        assert caught.value.code == "materialization_failed"
        receipt = storage.get_apply_receipt_for_decision(decision.id)
        assert receipt is not None and receipt.outcome == "failed"
        assert receipt.rollback_status == "succeeded"
        assert (source / "tracked.txt").read_text() == "base\n"
    assert storage.get_workspace_lease_by_run(memorial.id).state is WorkspaceLeaseState.CLOSED


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_dag_root_finalizes_workspace_before_single_terminal_event(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=AsyncMock(),
    )

    class Scheduler:
        async def run(self, _edict, execution, *, prepared_executor, persist_root_terminal=True):
            bound = require_bound_workspace(run_id=prepared_executor.prepared.run_id)
            assert execution.root_memorial_id == bound.lease.run_id
            (bound.root / "tracked.txt").write_text("dag staging\n")
            root = storage.get_memorial(execution.root_memorial_id)
            root.status = TaskStatus.COMPLETED
            root.result = "dag done"
            root.final_output = "dag done"
            storage.update_memorial(root)

    executor.set_dag_scheduler(Scheduler())
    lane_calls: list[tuple[str, object]] = []

    class LaneManager:
        global_lane = object()

        def get_session_lane(self, edict_id: str, max_concurrency: int):
            lane_calls.append((edict_id, max_concurrency))
            return object()

        def remove_session(self, edict_id: str) -> None:
            lane_calls.append((edict_id, "removed"))

    executor.set_lane_manager(LaneManager())
    edict = _governed_edict()
    storage.save_edict(edict)
    root = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(root)
    events: list[object] = []

    async def completed(event):
        events.append(get_bound_workspace())
        lease = storage.get_workspace_lease_by_run(root.id)
        assert lease is not None
        assert storage.get_latest_canonical_change_set_for_lease(lease.id) is not None

    executor._bus.on(  # noqa: SLF001
        "execution.completed",
        completed,
        consumer_name="test.execution_completed.v1",
    )
    plan = Plan(
        tasks=[
            PlanTask(task_id="one", description="one"),
            PlanTask(task_id="two", description="two", depends_on=["one"]),
        ]
    )

    await executor._execute_dag(edict, plan, memorial=root)  # noqa: SLF001

    assert events == [None]
    assert lane_calls == [
        (edict.id, edict.runtime.max_concurrency),
        (edict.id, "removed"),
    ]
    assert storage.get_memorial(root.id).status is TaskStatus.COMPLETED
    lease = storage.get_workspace_lease_by_run(root.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.ACTIVE
    assert (source / "tracked.txt").read_text() == "base\n"
    await service.shutdown()


@pytest.mark.parametrize("outcome", ["error", "cancel"])
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_dag_scheduler_failure_persists_matching_root_and_execution_terminal(
    storage,
    config_manager,
    tmp_path: Path,
    outcome: str,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=AsyncMock(),
    )
    started = asyncio.Event()
    capture_started = asyncio.Event()
    release_capture = asyncio.Event()
    cancelled_events: list[object] = []

    if outcome == "cancel":
        original_capture = service.capture_change_set

        async def delayed_capture(lease_id: str, *, run_id: str):
            capture_started.set()
            await release_capture.wait()
            return await original_capture(lease_id, run_id=run_id)

        service.capture_change_set = delayed_capture

    async def on_cancelled(event):
        cancelled_events.append(event)

    executor._bus.on(  # noqa: SLF001
        "execution.cancelled",
        on_cancelled,
        consumer_name="test.execution_cancelled.v1",
    )

    class Scheduler:
        async def run(self, _edict, _execution, *, prepared_executor, persist_root_terminal=True):
            bound = require_bound_workspace(run_id=prepared_executor.prepared.run_id)
            (bound.root / "tracked.txt").write_text("dag terminal\n")
            if outcome == "error":
                raise RuntimeError("scheduler failed")
            started.set()
            await asyncio.Event().wait()

    executor.set_dag_scheduler(Scheduler())
    edict = _governed_edict()
    storage.save_edict(edict)
    root = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(root)
    plan = Plan(tasks=[PlanTask(task_id="one", description="one")])

    if outcome == "error":
        await executor._execute_dag(edict, plan, memorial=root)  # noqa: SLF001
    else:
        task = asyncio.create_task(
            executor._execute_dag(edict, plan, memorial=root)  # noqa: SLF001
        )
        await started.wait()
        task.cancel()
        await capture_started.wait()
        task.cancel()
        release_capture.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    expected = "failed" if outcome == "error" else "cancelled"
    execution = storage.get_dag_by_edict(edict.id)
    assert execution is not None and execution.status == expected
    assert storage.get_memorial(root.id).status.value == expected
    lease = storage.get_workspace_lease_by_run(root.id)
    assert lease is not None and lease.state is WorkspaceLeaseState.CLOSED
    changes = storage.get_latest_canonical_change_set_for_lease(lease.id)
    assert changes is not None and len(changes.changes) == 1
    assert get_bound_workspace() is None
    assert (source / "tracked.txt").read_text() == "base\n"
    if outcome == "cancel":
        assert len(cancelled_events) == 1
        assert cancelled_events[0].memorial_id == root.id
        assert cancelled_events[0].payload["workspace_change_count"] == 1


@pytest.mark.parametrize("failure", ["prepare", "claim_lost"])
@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_dag_retry_startup_failure_persists_consistent_terminal_root(
    storage,
    config_manager,
    tmp_path: Path,
    monkeypatch,
    failure: str,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=AsyncMock(),
    )

    class FailedScheduler:
        async def run(self, _edict, execution, *, prepared_executor, persist_root_terminal=True):
            require_bound_workspace(run_id=prepared_executor.prepared.run_id)
            root = storage.get_memorial(execution.root_memorial_id)
            root.status = TaskStatus.FAILED
            root.error = "initial DAG failure"
            root.completed_at = datetime.now(UTC)
            storage.update_memorial(root)
            storage.update_dag_node_status(
                execution.id,
                "one",
                "failed",
                error=root.error,
            )
            storage.update_dag_execution_status(execution.id, "failed")

    executor.set_dag_scheduler(FailedScheduler())
    edict = _governed_edict()
    storage.save_edict(edict)
    first_root = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(first_root)
    await executor._execute_dag(  # noqa: SLF001
        edict,
        Plan(tasks=[PlanTask(task_id="one", description="one")]),
        memorial=first_root,
    )
    execution = storage.get_dag_by_edict(edict.id)
    assert execution is not None

    if failure == "prepare":
        monkeypatch.setattr(
            executor._workspace_runtime,  # noqa: SLF001
            "prepare",
            AsyncMock(side_effect=WorkspaceContractError("retry prepare failed")),
        )
    else:
        monkeypatch.setattr(
            storage,
            "claim_dag_retry",
            lambda *_args, **_kwargs: None,
        )

    with pytest.raises(ValueError, match="Cannot retry"):
        await executor.retry_dag(execution.id)

    memorials = sorted(
        storage.list_memorials_by_edict(edict.id),
        key=lambda item: item.created_at,
    )
    assert len(memorials) == 2
    retry_root = memorials[-1]
    assert retry_root.parent_memorial_id == first_root.id
    assert retry_root.status is TaskStatus.FAILED
    assert retry_root.completed_at is not None
    reloaded = storage.get_dag_execution(execution.id)
    assert reloaded is not None and reloaded.status == "failed"
    assert reloaded.nodes[0].status.value == "failed"
    assert reloaded.root_memorial_id == first_root.id
    if failure == "prepare":
        assert storage.get_workspace_lease_by_run(retry_root.id) is None
    else:
        retry_lease = storage.get_workspace_lease_by_run(retry_root.id)
        assert retry_lease is not None
        assert retry_lease.state is WorkspaceLeaseState.CLOSED
    assert get_bound_workspace() is None
    assert not executor.running_tasks
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
async def test_dag_retry_uses_new_lineage_root_and_finalizes_each_attempt(
    storage,
    config_manager,
    tmp_path: Path,
) -> None:
    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=AsyncMock(),
    )
    run_roots: list[str] = []
    terminal_contexts: list[object] = []

    class Scheduler:
        async def run(self, _edict, execution, *, prepared_executor, persist_root_terminal=True):
            bound = require_bound_workspace(run_id=prepared_executor.prepared.run_id)
            assert execution.root_memorial_id == bound.lease.run_id
            run_roots.append(bound.lease.run_id)
            (bound.root / "tracked.txt").write_text(f"dag attempt {len(run_roots)}\n")
            root = storage.get_memorial(execution.root_memorial_id)
            if len(run_roots) == 1:
                root.status = TaskStatus.FAILED
                root.error = "retry this DAG"
                storage.update_dag_node_status(execution.id, "one", "failed", error=root.error)
                storage.update_dag_execution_status(execution.id, "failed")
            else:
                root.status = TaskStatus.COMPLETED
                root.result = "retried DAG"
                root.final_output = "retried DAG"
                storage.update_dag_execution_status(execution.id, "completed")
            root.completed_at = datetime.now(UTC)
            storage.update_memorial(root)

    async def on_terminal(_event):
        terminal_contexts.append(get_bound_workspace())

    executor.set_dag_scheduler(Scheduler())
    executor._bus.on(  # noqa: SLF001
        "execution.failed",
        on_terminal,
        consumer_name="test.execution_terminal.v1",
    )
    executor._bus.on(  # noqa: SLF001
        "execution.completed",
        on_terminal,
        consumer_name="test.execution_terminal.v1",
    )
    edict = _governed_edict()
    storage.save_edict(edict)
    first_root = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(first_root)
    plan = Plan(tasks=[PlanTask(task_id="one", description="one")])

    await executor._execute_dag(edict, plan, memorial=first_root)  # noqa: SLF001
    execution = storage.get_dag_by_edict(edict.id)
    assert execution is not None
    assert await executor.retry_dag(execution.id) == ["one"]
    while executor.running_tasks:
        await asyncio.gather(*tuple(executor.running_tasks))

    retried_execution = storage.get_dag_execution(execution.id)
    assert retried_execution is not None
    assert retried_execution.root_memorial_id != first_root.id
    retry_root = storage.get_memorial(retried_execution.root_memorial_id)
    assert retry_root is not None
    assert retry_root.parent_memorial_id == first_root.id
    assert retry_root.attempt == first_root.attempt + 1
    assert retry_root.status is TaskStatus.COMPLETED
    assert run_roots == [first_root.id, retry_root.id]
    assert terminal_contexts == [None, None]

    first_lease = storage.get_workspace_lease_by_run(first_root.id)
    retry_lease = storage.get_workspace_lease_by_run(retry_root.id)
    assert first_lease is not None and first_lease.state is WorkspaceLeaseState.CLOSED
    assert retry_lease is not None and retry_lease.state is WorkspaceLeaseState.ACTIVE
    assert retry_lease.lineage_root_run_id == first_root.id
    assert retry_lease.parent_run_id == first_root.id
    assert retry_lease.attempt == first_lease.attempt + 1
    assert storage.get_latest_canonical_change_set_for_lease(first_lease.id) is not None
    assert storage.get_latest_canonical_change_set_for_lease(retry_lease.id) is not None
    assert (source / "tracked.txt").read_text() == "base\n"
    await service.shutdown()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_dag_terminal_memorial_is_visible_only_after_changes_are_readable(
    storage,
    config_manager,
    tmp_path: Path,
    outcome: str,
) -> None:
    """DAG 路径的同一条不变量：根 memorial 终态可见 ⟹ 变更集立即可读。

    多任务受治理计划走的正是 DAG 路径（executor 按 plan.tasks > 1 分流），这是常见
    路径而非边角。成功/失败/取消三条分支都必须遵守同一时序：先捕获变更集，再落根终态。
    """
    from tianshu.executor.dag_scheduler import DAGScheduler
    from tianshu.executor.worker_pool import WorkerPool

    source = _repository(tmp_path / "source")
    service = WorkspaceService(storage, GitBackend(), tmp_path / "leases")

    async def execute(_edict, *, memorial: Memorial, **_kwargs):
        bound = require_bound_workspace(run_id=memorial.id)
        (bound.root / f"{memorial.dag_node_id or 'node'}.txt").write_text("dag change\n")
        if outcome == "failed":
            raise RuntimeError("node blew up")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return AgentResult(status=TaskStatus.COMPLETED, result="node done")

    agent = AsyncMock()
    agent.execute.side_effect = execute
    executor = _executor(
        storage=storage,
        config_manager=config_manager,
        hooks=HookRegistry(),
        service=service,
        source=source,
        agent=agent,
    )
    executor.set_dag_scheduler(
        DAGScheduler(
            event_bus=executor._bus,  # noqa: SLF001 - test seam
            storage=storage,
            agent=agent,
            worker_pool=WorkerPool(max_concurrency=1),
        )
    )

    edict = _governed_edict()
    storage.save_edict(edict)
    root = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(root)
    plan = Plan(
        tasks=[
            PlanTask(task_id="t1", description="first"),
            PlanTask(task_id="t2", description="second", depends_on=["t1"]),
        ]
    )

    terminal_statuses = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    changes_at_terminal: list[object] = []
    real_update = storage.update_memorial

    def update_memorial_spy(updated: Memorial):
        result = real_update(updated)
        if updated.id == root.id and updated.status in terminal_statuses:
            lease = storage.get_workspace_lease_by_run(updated.id)
            changes_at_terminal.append(
                storage.get_latest_canonical_change_set_for_lease(lease.id) if lease else None
            )
        return result

    storage.update_memorial = update_memorial_spy  # type: ignore[method-assign]
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await executor._execute_dag(  # noqa: SLF001 - exercising the DAG path directly
                storage.get_edict(edict.id), plan, memorial=root
            )
    finally:
        storage.update_memorial = real_update  # type: ignore[method-assign]

    assert changes_at_terminal, "根 memorial 从未落终态"
    change_set = changes_at_terminal[0]
    assert change_set is not None, (
        "根 memorial 已落终态，变更集却还没落库——客户端此刻取 changes 会拿到 changes_unavailable"
    )


def test_fake_dag_schedulers_track_the_real_run_signature() -> None:
    """本文件里的假 scheduler 必须接受真实 DAGScheduler.run 的全部关键字。

    桩落后于真实接口时，生产代码传新关键字会 TypeError——而它会被上游的
    `except Exception` 吞成 FAILED，测试于是挂起或以错误的理由通过（本轮就
    真的挂了）。这条守卫让"桩必须跟随真实签名"成为可执行的纪律，而不是口头约定。
    """
    import ast
    import inspect

    from tianshu.executor.dag_scheduler import DAGScheduler

    real_kwonly = {
        name
        for name, param in inspect.signature(DAGScheduler.run).parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    }

    source = Path(__file__).read_text(encoding="utf-8")
    # 只认 DAG scheduler 形态：类方法（首参 self）且第二个位置参数是 edict。
    # outer-loop 的 orchestrator 桩是模块级函数，不在此列。
    fakes = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "run"
        and [arg.arg for arg in node.args.args][:1] == ["self"]
        and any(arg.arg in {"_edict", "edict"} for arg in node.args.args)
    ]
    assert fakes, "未找到假 scheduler——守卫失效了"

    for fake in fakes:
        accepted = {arg.arg for arg in fake.args.kwonlyargs}
        if fake.args.kwarg is not None:  # **kwargs 兜底也算兼容
            continue
        missing = real_kwonly - accepted
        assert not missing, (
            f"tests/executor/test_executor_workspace_lifecycle.py:{fake.lineno} 的假 scheduler "
            f"未接受 DAGScheduler.run 的关键字 {sorted(missing)}——生产代码调用它会 TypeError"
        )
