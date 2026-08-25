"""DAG runs share one governed root lease and own all worker lifetimes."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tianshu.bus.event_bus import EventBus
from tianshu.executor.adapters import (
    DelegatingExecutorAdapter,
    ExecutorAdapterRegistry,
    ExecutorGenerationUnavailable,
    PreparedExecutor,
)
from tianshu.executor.agent import AgentResult
from tianshu.executor.capabilities import HostCapabilityProbeV1, native_manifest
from tianshu.executor.dag_scheduler import DAGScheduler
from tianshu.executor.executor import Executor
from tianshu.executor.git_backend import GitBackend
from tianshu.executor.worker import Worker
from tianshu.executor.worker_pool import WorkerPool
from tianshu.executor.workspace_context import BoundWorkspace, bind_workspace
from tianshu.executor.workspace_service import WorkspaceService
from tianshu.governance.decision_service import DecisionService
from tianshu.kernel.hooks import HookRegistry
from tianshu.models import Edict, Memorial, TaskStatus, UsageSummary
from tianshu.models.dag import DAGExecution, DAGNode, DAGNodeStatus
from tianshu.models.governance_contract import (
    LegacyEdictGovernanceMapper,
    WorkspacePolicyV1,
)
from tianshu.models.plan import Plan, PlanTask
from tianshu.models.workspace import WorkspaceLease, WorkspaceLeaseState
from tianshu.storage import Storage


def _workspace_service(storage, backend, staging_root) -> WorkspaceService:
    return WorkspaceService(storage, backend, staging_root, DecisionService(storage))


def _persist_dag(
    storage,
    *,
    edict: Edict,
    nodes: list[DAGNode],
) -> tuple[DAGExecution, Memorial]:
    storage.save_edict(edict)
    root = Memorial(
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.RUNNING,
    )
    storage.save_memorial(root)
    execution = DAGExecution(
        edict_id=edict.id,
        root_memorial_id=root.id,
        max_concurrency=2,
        nodes=nodes,
    )
    storage.save_dag_execution(execution)
    return execution, root


def _scheduler(storage, agent, pool) -> DAGScheduler:
    return DAGScheduler(
        pool,
        agent,
        storage,
        EventBus(),
    )


async def test_scheduler_keeps_results_and_usage_local_to_each_run(storage) -> None:
    agent = AsyncMock()
    agent.execute.side_effect = [
        AgentResult(
            status=TaskStatus.COMPLETED,
            result="first result",
            usage=UsageSummary(total_tokens=7),
        ),
        AgentResult(
            status=TaskStatus.COMPLETED,
            result=None,
            usage=UsageSummary(total_tokens=0),
        ),
    ]
    pool = WorkerPool(max_concurrency=1)
    scheduler = _scheduler(storage, agent, pool)

    first_execution, first_root = _persist_dag(
        storage,
        edict=Edict(goal="first"),
        nodes=[DAGNode(node_id="same-node", description="first")],
    )
    await scheduler.run(storage.get_edict(first_execution.edict_id), first_execution)
    assert storage.get_memorial(first_root.id).result == ("## same-node: first\n\nfirst result")
    assert storage.get_dag_execution(first_execution.id).nodes[0].memorial_id is not None

    second_execution, second_root = _persist_dag(
        storage,
        edict=Edict(goal="second"),
        nodes=[DAGNode(node_id="same-node", description="second")],
    )
    await scheduler.run(storage.get_edict(second_execution.edict_id), second_execution)

    loaded = storage.get_memorial(second_root.id)
    assert loaded.result is None
    assert loaded.final_output is None
    assert loaded.usage.total_tokens == 0


async def test_partial_retry_rebuilds_completed_upstream_from_this_dag(storage) -> None:
    edict = Edict(goal="retry with context")
    storage.save_edict(edict)
    root = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.RUNNING)
    storage.save_memorial(root)
    upstream = Memorial(
        edict_id=edict.id,
        instruction="collect",
        status=TaskStatus.COMPLETED,
        result="persisted upstream",
        usage=UsageSummary(total_tokens=5),
        dag_node_id="collect",
        parent_memorial_id=root.id,
    )
    storage.save_memorial(upstream)
    execution = DAGExecution(
        edict_id=edict.id,
        root_memorial_id=root.id,
        nodes=[
            DAGNode(
                node_id="collect",
                description="collect",
                status=DAGNodeStatus.COMPLETED,
                memorial_id=upstream.id,
            ),
            DAGNode(node_id="write", description="write", depends_on=["collect"]),
        ],
    )
    storage.save_dag_execution(execution)
    agent = AsyncMock()
    agent.execute.return_value = AgentResult(
        status=TaskStatus.COMPLETED,
        result="new downstream",
        usage=UsageSummary(total_tokens=3),
    )

    await _scheduler(storage, agent, WorkerPool(max_concurrency=1)).run(
        storage.get_edict(edict.id),
        storage.get_dag_execution(execution.id),
    )

    assert agent.execute.await_args.kwargs["history"] == [
        {
            "role": "system",
            "content": "[Upstream node collect result]: persisted upstream",
        }
    ]
    loaded_root = storage.get_memorial(root.id)
    assert "persisted upstream" in loaded_root.result
    assert "new downstream" in loaded_root.result
    assert loaded_root.usage.total_tokens == 8
    loaded_nodes = {node.node_id: node for node in storage.get_dag_execution(execution.id).nodes}
    assert loaded_nodes["write"].memorial_id is not None


async def test_legacy_node_without_pointer_uses_only_its_dag_time_window(storage) -> None:
    edict = Edict(goal="legacy retry")
    storage.save_edict(edict)
    root = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.RUNNING)
    storage.save_memorial(root)
    execution = DAGExecution(
        edict_id=edict.id,
        root_memorial_id=root.id,
        nodes=[
            DAGNode(
                node_id="collect",
                description="collect",
                status=DAGNodeStatus.COMPLETED,
            ),
            DAGNode(node_id="write", description="write", depends_on=["collect"]),
        ],
    )
    storage.save_dag_execution(execution)
    storage.save_memorial(
        Memorial(
            edict_id=edict.id,
            instruction="legacy collect",
            status=TaskStatus.COMPLETED,
            result="legacy upstream",
            dag_node_id="collect",
        )
    )

    # A later DAG reuses the same node id. Its memorial must never contaminate
    # reconstruction of the earlier DAG's legacy, pointer-less node.
    later = DAGExecution(
        edict_id=edict.id,
        status="completed",
        nodes=[
            DAGNode(
                node_id="collect",
                description="other collect",
                status=DAGNodeStatus.COMPLETED,
            )
        ],
    )
    storage.save_dag_execution(later)
    storage.save_memorial(
        Memorial(
            edict_id=edict.id,
            instruction="other collect",
            status=TaskStatus.COMPLETED,
            result="other DAG upstream",
            dag_node_id="collect",
        )
    )
    agent = AsyncMock()
    agent.execute.return_value = AgentResult(status=TaskStatus.COMPLETED, result="done")

    await _scheduler(storage, agent, WorkerPool(max_concurrency=1)).run(
        storage.get_edict(edict.id),
        storage.get_dag_execution(execution.id),
    )

    assert agent.execute.await_args.kwargs["history"][0]["content"].endswith("legacy upstream")


class _TailPool:
    """A pool whose task has observable cleanup after its completion callback."""

    def __init__(self) -> None:
        self.tail_started = asyncio.Event()
        self.release_tail = asyncio.Event()

    async def submit(self, item, on_complete=None):
        async def run() -> None:
            await item.coro_factory()
            if on_complete is not None:
                await on_complete(item.node_id, None)
            self.tail_started.set()
            await self.release_tail.wait()

        return asyncio.create_task(run())


async def test_scheduler_waits_for_worker_callback_tail_before_returning(storage) -> None:
    agent = AsyncMock()
    agent.execute.return_value = AgentResult(status=TaskStatus.COMPLETED, result="done")
    pool = _TailPool()
    scheduler = _scheduler(storage, agent, pool)
    execution, _root = _persist_dag(
        storage,
        edict=Edict(goal="drain"),
        nodes=[DAGNode(node_id="one", description="one")],
    )

    run_task = asyncio.create_task(scheduler.run(storage.get_edict(execution.edict_id), execution))
    try:
        await asyncio.wait_for(pool.tail_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not run_task.done()
    finally:
        pool.release_tail.set()
        await run_task


async def test_scheduler_cancellation_drains_workers_and_never_starts_downstream(storage) -> None:
    started = asyncio.Event()
    never = asyncio.Event()

    async def execute(*args, **kwargs):
        started.set()
        await never.wait()

    agent = AsyncMock()
    agent.execute.side_effect = execute
    pool = WorkerPool(max_concurrency=1)
    scheduler = _scheduler(storage, agent, pool)
    execution, _root = _persist_dag(
        storage,
        edict=Edict(goal="cancel"),
        nodes=[
            DAGNode(node_id="one", description="one"),
            DAGNode(node_id="two", description="two", depends_on=["one"]),
        ],
    )
    run_task = asyncio.create_task(scheduler.run(storage.get_edict(execution.edict_id), execution))
    await asyncio.wait_for(started.wait(), timeout=1)

    try:
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task
        await asyncio.sleep(0)
        assert pool.active_count == 0
        assert storage.get_dag_execution(execution.id).status == "cancelled"
        assert all(
            memorial.dag_node_id != "two"
            for memorial in storage.list_memorials_by_edict(execution.edict_id)
        )
    finally:
        await pool.shutdown()


@pytest.mark.parametrize(
    ("tasks", "expected_error", "expected_node_ids"),
    (
        (
            [
                PlanTask(task_id="one", description="one", depends_on=["two"]),
                PlanTask(task_id="two", description="two", depends_on=["one"]),
            ],
            "DAG validation failed: Cycle detected in DAG",
            {"one", "two"},
        ),
        (
            [
                PlanTask(task_id="same", description="first"),
                PlanTask(task_id="same", description="second"),
            ],
            "DAG validation failed: Duplicate DAG node ids: same",
            {"same"},
        ),
        (
            [
                PlanTask(task_id="one", description="one", depends_on=["missing"]),
                PlanTask(task_id="two", description="two"),
            ],
            "DAG validation failed: Unknown DAG dependencies: missing",
            {"one", "two"},
        ),
    ),
    ids=("cycle", "duplicate-node-id", "unknown-dependency"),
)
async def test_invalid_dag_fails_before_workspace_or_execution_with_one_terminal(
    storage,
    config_manager,
    tmp_path,
    tasks: list[PlanTask],
    expected_error: str,
    expected_node_ids: set[str],
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    staging_root = tmp_path / "leases"
    service = _workspace_service(storage, GitBackend(), staging_root)
    bus = EventBus()
    agent = AsyncMock()
    pool = WorkerPool(max_concurrency=1)
    scheduler = DAGScheduler(pool, agent, storage, bus)
    executor = Executor(
        event_bus=bus,
        storage=storage,
        config_manager=config_manager,
        hook_registry=HookRegistry(),
        workspace_service=service,
        workspace_sources={"workspace-main": source},
    )
    executor.set_agent(agent)
    executor.set_dag_scheduler(scheduler)

    base = Edict(goal="reject invalid DAG", submitter="dag-test")
    requested = LegacyEdictGovernanceMapper.from_edict(
        base,
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "workspace": WorkspacePolicyV1(
                source_id=None,
                staging_mode="ephemeral",
                apply_mode="none",
            )
        }
    )
    edict = base.model_copy(update={"governance_contract": requested})
    storage.save_edict(edict)
    root = Memorial(edict_id=edict.id, instruction=edict.goal)
    storage.save_memorial(root)
    plan = Plan(tasks=tasks)
    terminal_events = []

    async def on_terminal(event) -> None:
        terminal_events.append(event)

    for event_type in ("execution.completed", "execution.failed", "execution.cancelled"):
        bus.on(event_type, on_terminal, consumer_name="test.dag_terminal.v1")

    try:
        await executor._execute_dag(edict, plan, memorial=root)  # noqa: SLF001

        loaded_root = storage.get_memorial(root.id)
        loaded_dag = storage.get_dag_by_edict(edict.id)
        assert loaded_root.status is TaskStatus.FAILED
        assert loaded_root.completed_at is not None
        assert loaded_root.started_at is None
        assert loaded_root.effective_governance_contract is None
        assert loaded_root.error == expected_error
        assert loaded_dag is not None and loaded_dag.status == "failed"
        assert loaded_dag.completed_at == loaded_root.completed_at
        assert {node.node_id for node in loaded_dag.nodes} == expected_node_ids
        assert len(loaded_dag.nodes) == len(expected_node_ids)
        assert len(terminal_events) == 1
        assert terminal_events[0].event_type == "execution.failed"
        assert terminal_events[0].memorial_id == root.id
        assert terminal_events[0].payload["status"] == "failed"
        assert terminal_events[0].payload["error"] == loaded_root.error
        agent.execute.assert_not_awaited()
        assert pool.active_count == 0
        assert storage.get_workspace_lease_by_run(root.id) is None
        assert list(staging_root.iterdir()) == []
    finally:
        await pool.shutdown()
        await service.shutdown()


def _ephemeral_prepared(edict: Edict, root_id: str, delegate) -> PreparedExecutor:
    requested = LegacyEdictGovernanceMapper.from_edict(
        edict,
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "workspace": WorkspacePolicyV1(
                source_id=None,
                staging_mode="ephemeral",
                apply_mode="none",
            )
        }
    )
    edict.governance_contract = requested
    probe = HostCapabilityProbeV1(
        probe_id="dag-test",
        os_name="test",
        architecture="test",
        git_available=True,
        process_groups_available=True,
        sandbox_backend=None,
    )
    adapter = DelegatingExecutorAdapter(
        adapter_id="native",
        manifest=native_manifest(),
        delegate=delegate,
        probe_factory=lambda: probe,
    )
    return ExecutorAdapterRegistry((adapter,)).prepare(
        requested,
        run_id=root_id,
        instruction=edict.goal,
        execution_mode="dag",
    )


def _bound_scratch(tmp_path, root_id: str, prepared: PreparedExecutor) -> BoundWorkspace:
    staging = tmp_path / "dag-stage"
    staging.mkdir()
    return BoundWorkspace(
        lease=WorkspaceLease(
            id="lease-root",
            run_id=root_id,
            lineage_root_run_id=root_id,
            attempt=0,
            source_kind="scratch",
            apply_mode="none",
            staging_root=str(staging),
            state=WorkspaceLeaseState.ACTIVE,
            state_version=1,
            created_at=datetime.now(UTC),
        ),
        effective_contract=prepared.effective,
    )


async def test_worker_authorizes_child_on_root_lease_before_running(
    storage,
    tmp_path,
    monkeypatch,
) -> None:
    edict = Edict(goal="root", submitter="test-service")
    root = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.RUNNING)
    agent = AsyncMock()
    agent.execute.return_value = AgentResult(status=TaskStatus.COMPLETED, result="done")
    prepared = _ephemeral_prepared(edict, root.id, agent)
    storage.save_edict(edict)
    storage.save_memorial(root)
    bound = _bound_scratch(tmp_path, root.id, prepared)
    original_update = storage.update_memorial

    def checked_update(memorial: Memorial) -> None:
        if memorial.id != root.id and memorial.status is TaskStatus.RUNNING:
            assert bound.is_run_authorized(memorial.id)
        original_update(memorial)

    monkeypatch.setattr(storage, "update_memorial", checked_update)
    node = DAGNode(node_id="child", description="child work")

    with bind_workspace(bound):
        result = await Worker(agent, storage).execute_node(
            storage.get_edict(edict.id),
            node,
            {},
            prepared_executor=prepared,
            root_memorial_id=root.id,
        )

    child = storage.get_memorial(node.memorial_id)
    assert result.status is TaskStatus.COMPLETED, result.error
    assert child.parent_memorial_id == root.id
    assert bound.is_run_authorized(child.id)
    assert bound.lease.run_id == root.id


async def test_worker_fails_closed_before_running_without_root_lease(
    storage,
    monkeypatch,
) -> None:
    edict = Edict(goal="root", submitter="test-service")
    root = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.RUNNING)
    agent = AsyncMock()
    prepared = _ephemeral_prepared(edict, root.id, agent)
    storage.save_edict(edict)
    storage.save_memorial(root)
    observed_statuses: list[TaskStatus] = []
    original_update = storage.update_memorial

    def record_update(memorial: Memorial) -> None:
        if memorial.id != root.id:
            observed_statuses.append(memorial.status)
        original_update(memorial)

    monkeypatch.setattr(storage, "update_memorial", record_update)
    node = DAGNode(node_id="child", description="child work")

    result = await Worker(agent, storage).execute_node(
        storage.get_edict(edict.id),
        node,
        {},
        prepared_executor=prepared,
        root_memorial_id=root.id,
    )

    assert result.status is TaskStatus.FAILED
    assert TaskStatus.RUNNING not in observed_statuses
    assert storage.get_memorial(node.memorial_id).parent_memorial_id == root.id
    agent.execute.assert_not_awaited()


async def test_dag_preserves_generation_retired_from_node_to_root(storage, tmp_path) -> None:
    edict = Edict(goal="pinned generation drift", submitter="test-service")
    root = Memorial(edict_id=edict.id, instruction=edict.goal, status=TaskStatus.RUNNING)
    delegate = AsyncMock()
    delegate.execute.side_effect = ExecutorGenerationUnavailable("managed package drifted")
    prepared = _ephemeral_prepared(edict, root.id, delegate)
    storage.save_edict(edict)
    storage.save_memorial(root)
    execution = DAGExecution(
        edict_id=edict.id,
        root_memorial_id=root.id,
        max_concurrency=1,
        nodes=[DAGNode(node_id="child", description="child work")],
    )
    storage.save_dag_execution(execution)
    bound = _bound_scratch(tmp_path, root.id, prepared)
    pool = WorkerPool(max_concurrency=1)
    scheduler = _scheduler(storage, delegate, pool)

    try:
        with bind_workspace(bound):
            terminal = await scheduler.run(
                storage.get_edict(edict.id),
                execution,
                prepared_executor=prepared,
            )
    finally:
        await pool.shutdown()

    assert terminal is not None
    assert terminal.status is TaskStatus.FAILED
    assert terminal.failure_reason == "generation_retired"
    assert terminal.error == "pinned runtime generation is unavailable"
    child = storage.get_memorial(execution.nodes[0].memorial_id)
    assert child.failure_reason == "generation_retired"


def _retry_claim_fixture(storage: Storage) -> tuple[DAGExecution, Memorial, Memorial]:
    edict = Edict(goal="atomic retry")
    storage.save_edict(edict)
    old_root = Memorial(
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.FAILED,
    )
    storage.save_memorial(old_root)
    now = datetime.now(UTC)
    execution = DAGExecution(
        edict_id=edict.id,
        status="failed",
        root_memorial_id=old_root.id,
        completed_at=now,
        nodes=[
            DAGNode(
                node_id="done",
                description="done",
                status=DAGNodeStatus.COMPLETED,
                started_at=now,
                completed_at=now,
            ),
            DAGNode(
                node_id="failed",
                description="failed",
                status=DAGNodeStatus.FAILED,
                started_at=now,
                completed_at=now,
                error="boom",
            ),
            DAGNode(
                node_id="downstream",
                description="downstream",
                depends_on=["failed"],
                status=DAGNodeStatus.CANCELLED,
                completed_at=now,
                error="blocked",
            ),
            DAGNode(
                node_id="unrelated",
                description="unrelated",
                depends_on=["done"],
                status=DAGNodeStatus.CANCELLED,
                completed_at=now,
                error="leave me",
            ),
        ],
    )
    storage.save_dag_execution(execution)
    new_root = Memorial(
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.SUBMITTED,
        parent_memorial_id=old_root.id,
        attempt=old_root.attempt + 1,
    )
    storage.save_memorial(new_root)
    return execution, old_root, new_root


def test_claim_dag_retry_atomically_moves_root_status_and_reset_set(storage) -> None:
    execution, old_root, new_root = _retry_claim_fixture(storage)

    reset = storage.claim_dag_retry(
        execution.id,
        expected_root_memorial_id=old_root.id,
        root_memorial_id=new_root.id,
    )

    assert reset == ["downstream", "failed"]
    loaded = storage.get_dag_execution(execution.id)
    assert loaded.root_memorial_id == new_root.id
    assert loaded.status == "pending"
    assert loaded.completed_at is None
    nodes = {node.node_id: node for node in loaded.nodes}
    for node_id in reset:
        assert nodes[node_id].status is DAGNodeStatus.PENDING
        assert nodes[node_id].error is None
        assert nodes[node_id].started_at is None
        assert nodes[node_id].completed_at is None
    assert nodes["done"].status is DAGNodeStatus.COMPLETED
    assert nodes["unrelated"].status is DAGNodeStatus.CANCELLED
    assert nodes["unrelated"].error == "leave me"


def test_claim_dag_retry_failures_are_zero_write(storage) -> None:
    execution, old_root, new_root = _retry_claim_fixture(storage)
    before = storage.get_dag_execution(execution.id).model_dump(mode="json")

    assert (
        storage.claim_dag_retry(
            execution.id,
            expected_root_memorial_id="wrong-root",
            root_memorial_id=new_root.id,
        )
        is None
    )
    assert storage.get_dag_execution(execution.id).model_dump(mode="json") == before

    with pytest.raises(ValueError, match="unknown DAG nodes"):
        storage.claim_dag_retry(
            execution.id,
            expected_root_memorial_id=old_root.id,
            root_memorial_id=new_root.id,
            from_node_ids=["missing"],
        )
    assert storage.get_dag_execution(execution.id).model_dump(mode="json") == before


def test_claim_dag_retry_without_targets_is_zero_write(storage) -> None:
    execution, old_root, new_root = _retry_claim_fixture(storage)
    storage.update_dag_node_status(execution.id, "failed", "completed")
    before = storage.get_dag_execution(execution.id).model_dump(mode="json")

    assert (
        storage.claim_dag_retry(
            execution.id,
            expected_root_memorial_id=old_root.id,
            root_memorial_id=new_root.id,
        )
        == []
    )
    assert storage.get_dag_execution(execution.id).model_dump(mode="json") == before


def test_concurrent_dag_retry_claims_have_exactly_one_winner(tmp_path) -> None:
    path = tmp_path / "claim.sqlite3"
    first = Storage(str(path))
    first.init_db()
    second = Storage(str(path))
    second.init_db()
    execution, old_root, first_root = _retry_claim_fixture(first)
    second_root = Memorial(
        edict_id=old_root.edict_id,
        instruction=old_root.instruction,
        status=TaskStatus.SUBMITTED,
        parent_memorial_id=old_root.id,
        attempt=old_root.attempt + 1,
    )
    first.save_memorial(second_root)
    barrier = threading.Barrier(2)

    def claim(storage: Storage, root: Memorial) -> list[str] | None:
        barrier.wait()
        return storage.claim_dag_retry(
            execution.id,
            expected_root_memorial_id=old_root.id,
            root_memorial_id=root.id,
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda args: claim(*args),
                    ((first, first_root), (second, second_root)),
                )
            )
        assert sum(result == ["downstream", "failed"] for result in results) == 1
        assert sum(result is None for result in results) == 1
        loaded = first.get_dag_execution(execution.id)
        assert loaded.root_memorial_id in {first_root.id, second_root.id}
        assert loaded.status == "pending"
    finally:
        second.close()
        first.close()
