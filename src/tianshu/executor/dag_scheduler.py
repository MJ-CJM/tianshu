"""DAGScheduler — monitors DAG state, submits ready nodes to WorkerPool."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from tianshu.bus.event_bus import EventBus
from tianshu.dag.graph import DAG
from tianshu.dag.models import DAGExecution, DAGNode, DAGNodeStatus
from tianshu.executor.agent import Agent, AgentResult
from tianshu.executor.worker import Worker
from tianshu.executor.worker_pool import WorkItem, WorkerPool
from tianshu.models.common import TaskStatus, UsageSummary
from tianshu.models.edict import Edict
from tianshu.models.events import make_event
from tianshu.persona.loader import PersonaLoader
from tianshu.persona.model import DEFAULT_EXECUTOR_ID
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class DAGScheduler:
    """Orchestrates DAG execution by scheduling ready nodes to WorkerPool."""

    def __init__(
        self,
        worker_pool: WorkerPool,
        agent: Agent,
        storage: Storage,
        event_bus: EventBus,
        persona_loader: PersonaLoader | None = None,
        prompt_builder: PromptBuilder | None = None,
        session_lane: object | None = None,
        global_lane: object | None = None,
    ) -> None:
        self._pool = worker_pool
        self._agent = agent
        self._storage = storage
        self._bus = event_bus
        self._persona_loader = persona_loader
        self._prompt_builder = prompt_builder
        self._session_lane = session_lane
        self._global_lane = global_lane
        self._worker = Worker(agent, storage, prompt_builder)
        self._node_results: dict[str, str] = {}
        self._node_usage: dict[str, UsageSummary] = {}

    async def run(self, edict: Edict, execution: DAGExecution) -> None:
        """Run a full DAG execution to completion."""
        dag = DAG.from_execution(execution)

        # Validate DAG
        try:
            dag.topological_sort()
        except ValueError as e:
            execution.status = "failed"
            execution.completed_at = datetime.now(UTC)
            self._storage.update_dag_execution_status(execution.id, "failed")
            logger.error("DAG validation failed: %s", e)
            return

        execution.status = "running"
        self._storage.update_dag_execution_status(execution.id, "running")

        await self._bus.emit(make_event(
            "dag.started",
            edict_id=edict.id,
            producer="dag_scheduler",
            payload={"dag_id": execution.id},
        ))

        # Scheduling loop
        completion_event = asyncio.Event()
        pending_tasks: set[asyncio.Task] = set()

        async def on_node_complete(node_id: str, error: str | None) -> None:
            if error:
                logger.debug("[DAG] Edict %s: node %s failed, error=%s", edict.id, node_id, error)
                dag.mark_failed(node_id, error)
                cancelled = dag.propagate_failure(node_id)
                for cid in cancelled:
                    self._storage.update_dag_node_status(
                        execution.id, cid, DAGNodeStatus.CANCELLED.value,
                    )
                self._storage.update_dag_node_status(
                    execution.id, node_id, DAGNodeStatus.FAILED.value, error=error,
                )
                await self._bus.emit(make_event(
                    "dag.node.failed",
                    edict_id=edict.id,
                    producer="dag_scheduler",
                    payload={"dag_id": execution.id, "node_id": node_id, "error": error},
                ))
            else:
                logger.debug("[DAG] Edict %s: node %s completed", edict.id, node_id)
                dag.mark_completed(node_id)
                self._storage.update_dag_node_status(
                    execution.id, node_id, DAGNodeStatus.COMPLETED.value,
                )
                await self._bus.emit(make_event(
                    "dag.node.completed",
                    edict_id=edict.id,
                    producer="dag_scheduler",
                    payload={"dag_id": execution.id, "node_id": node_id},
                ))

            # Schedule next batch
            if dag.is_complete():
                completion_event.set()
            else:
                await self._schedule_ready(edict, execution, dag, pending_tasks, on_node_complete)

        # Initial scheduling
        await self._schedule_ready(edict, execution, dag, pending_tasks, on_node_complete)

        if not pending_tasks and dag.is_complete():
            completion_event.set()

        # Wait for completion
        await completion_event.wait()

        # Determine final status
        if dag.has_failures():
            execution.status = "failed"
        else:
            execution.status = "completed"
        execution.completed_at = datetime.now(UTC)
        self._storage.update_dag_execution_status(
            execution.id,
            execution.status,
            completed_at=execution.completed_at,
        )

        # Aggregate usage + results into root memorial
        if execution.root_memorial_id:
            total_usage = UsageSummary()
            for usage in self._node_usage.values():
                total_usage = UsageSummary(
                    prompt_tokens=total_usage.prompt_tokens + usage.prompt_tokens,
                    completion_tokens=total_usage.completion_tokens + usage.completion_tokens,
                    total_tokens=total_usage.total_tokens + usage.total_tokens,
                )

            # Synthesize sub-task results into root memorial
            result_parts: list[str] = []
            for node in execution.nodes:
                node_result = self._node_results.get(node.node_id)
                if node_result:
                    result_parts.append(
                        f"## {node.node_id}: {node.description}\n\n{node_result}"
                    )
            combined_result = "\n\n---\n\n".join(result_parts) if result_parts else None

            root = self._storage.get_memorial(execution.root_memorial_id)
            if root:
                root.usage = total_usage
                root.result = combined_result
                root.status = TaskStatus.COMPLETED if execution.status == "completed" else TaskStatus.FAILED
                root.completed_at = datetime.now(UTC)
                self._storage.update_memorial(root)

        event_type = f"execution.{execution.status}"
        await self._bus.emit(make_event(
            event_type,
            edict_id=edict.id,
            memorial_id=execution.root_memorial_id,
            producer="dag_scheduler",
            payload={
                "dag_id": execution.id,
                "status": execution.status,
            },
        ))

    async def _schedule_ready(
        self,
        edict: Edict,
        execution: DAGExecution,
        dag: DAG,
        pending_tasks: set[asyncio.Task],
        on_complete,
    ) -> None:
        ready_nodes = dag.get_ready_nodes()
        for node in ready_nodes:
            logger.debug(
                "[DAG] Edict %s: scheduling node %s, persona=%s, deps=%s",
                edict.id, node.node_id, node.assigned_official, node.depends_on,
            )
            dag.mark_running(node.node_id)
            node.started_at = datetime.now(UTC)
            self._storage.update_dag_node_status(
                execution.id, node.node_id, DAGNodeStatus.RUNNING.value,
            )

            # Resolve persona
            persona = None
            if node.assigned_official and self._persona_loader:
                persona = self._persona_loader.get(node.assigned_official)
            if persona is None and self._persona_loader:
                # Fallback: use bingbu (default executor) rather than None
                fallback = self._persona_loader.get(DEFAULT_EXECUTOR_ID)
                if fallback:
                    persona = fallback
                    logger.warning(
                        "[DAG] Edict %s node %s: assigned_official '%s' not found, "
                        "falling back to bingbu",
                        edict.id, node.node_id, node.assigned_official,
                    )

            # Gather upstream results
            upstream = {
                dep: self._node_results.get(dep, "")
                for dep in node.depends_on
                if dep in self._node_results
            }

            # Acquire lanes if available
            session_lane = self._session_lane
            global_lane = self._global_lane

            async def _run_node(
                _node: DAGNode = node,
                _upstream: dict = upstream,
                _persona=persona,
                _session_lane=session_lane,
                _global_lane=global_lane,
            ) -> None:
                if _session_lane:
                    await _session_lane.acquire()
                if _global_lane:
                    await _global_lane.acquire()
                try:
                    result = await self._worker.execute_node(
                        edict, _node, _upstream, persona=_persona,
                    )
                    if result.result:
                        self._node_results[_node.node_id] = result.result
                    self._node_usage[_node.node_id] = result.usage
                    if result.status == TaskStatus.FAILED:
                        raise RuntimeError(result.error or "Node execution failed")
                finally:
                    if _global_lane:
                        _global_lane.release()
                    if _session_lane:
                        _session_lane.release()

            item = WorkItem(
                id=f"{execution.id}:{node.node_id}",
                dag_execution_id=execution.id,
                node_id=node.node_id,
                description=node.description,
                coro_factory=_run_node,
            )
            task = await self._pool.submit(item, on_complete=on_complete)
            pending_tasks.add(task)
            task.add_done_callback(pending_tasks.discard)
