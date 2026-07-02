"""CascadeCanceller — cascade cancel running + pending nodes in a DAG."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from tianshu.dag.graph import DAG
from tianshu.executor.worker_pool import WorkerPool
from tianshu.models.dag import DAGExecution, DAGNodeStatus
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class CascadeCanceller:
    """Cancels a DAG execution with cascade to downstream nodes."""

    def __init__(self, storage: Storage, worker_pool: WorkerPool) -> None:
        self._storage = storage
        self._pool = worker_pool

    async def cancel(self, execution: DAGExecution) -> list[str]:
        """Cancel all non-terminal nodes. Returns list of cancelled node_ids."""
        dag = DAG.from_execution(execution)
        cancelled: list[str] = []

        for node in execution.nodes:
            if node.status == DAGNodeStatus.RUNNING:
                # Cancel running worker
                work_id = f"{execution.id}:{node.node_id}"
                await self._pool.cancel(work_id)
                dag.mark_failed(node.node_id, "Cancelled by user")
                self._storage.update_dag_node_status(
                    execution.id,
                    node.node_id,
                    DAGNodeStatus.CANCELLED.value,
                    error="Cancelled by user",
                )
                cancelled.append(node.node_id)
                # Propagate to downstream
                downstream = dag.propagate_failure(node.node_id)
                for nid in downstream:
                    self._storage.update_dag_node_status(
                        execution.id,
                        nid,
                        DAGNodeStatus.CANCELLED.value,
                    )
                cancelled.extend(downstream)

            elif node.status in (DAGNodeStatus.PENDING, DAGNodeStatus.READY):
                node.status = DAGNodeStatus.CANCELLED
                self._storage.update_dag_node_status(
                    execution.id,
                    node.node_id,
                    DAGNodeStatus.CANCELLED.value,
                )
                cancelled.append(node.node_id)

        execution.status = "cancelled"
        execution.completed_at = datetime.now(UTC)
        self._storage.update_dag_execution_status(
            execution.id,
            "cancelled",
            completed_at=execution.completed_at,
        )

        return list(set(cancelled))
