"""PartialRetrier — retry failed nodes in a DAG, preserving completed work."""

from __future__ import annotations

import logging

from tianshu.dag.graph import DAG
from tianshu.models.dag import DAGExecution, DAGNodeStatus
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class PartialRetrier:
    """Resets failed + downstream nodes to PENDING for re-execution."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def prepare_retry(
        self,
        execution: DAGExecution,
        from_node_ids: list[str] | None = None,
    ) -> list[str]:
        """Reset failed nodes (and their downstream) to PENDING.

        Args:
            execution: The DAG execution to retry.
            from_node_ids: Specific nodes to retry. Defaults to all FAILED nodes.

        Returns:
            List of node_ids that were reset.
        """
        dag = DAG.from_execution(execution)

        # Determine which nodes to retry
        if from_node_ids:
            target_ids = from_node_ids
        else:
            target_ids = [n.node_id for n in execution.nodes if n.status == DAGNodeStatus.FAILED]

        if not target_ids:
            return []

        reset_ids: set[str] = set()

        for nid in target_ids:
            reset_ids.add(nid)
            # Also reset all downstream (CANCELLED or FAILED)
            self._collect_downstream(dag, nid, reset_ids)

        # Reset nodes
        for nid in reset_ids:
            node = dag.nodes[nid]
            node.status = DAGNodeStatus.PENDING
            node.error = None
            node.started_at = None
            node.completed_at = None
            self._storage.update_dag_node_status(
                execution.id,
                nid,
                DAGNodeStatus.PENDING.value,
            )

        # Reset execution status
        execution.status = "pending"
        execution.completed_at = None
        self._storage.update_dag_execution_status(execution.id, "pending")

        return list(reset_ids)

    def _collect_downstream(
        self,
        dag: DAG,
        node_id: str,
        result: set[str],
    ) -> None:
        """Collect all downstream nodes (BFS) that need resetting."""
        for nid, node in dag.nodes.items():
            if nid in result:
                continue
            if node_id in node.depends_on and node.status in (
                DAGNodeStatus.CANCELLED,
                DAGNodeStatus.FAILED,
            ):
                result.add(nid)
                self._collect_downstream(dag, nid, result)
