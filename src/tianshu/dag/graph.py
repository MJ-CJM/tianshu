"""DAG graph operations — topological sort, ready-node detection, failure propagation."""

from __future__ import annotations

from collections import deque

from tianshu.models.dag import DAGExecution, DAGNode, DAGNodeStatus


class DAG:
    """In-memory DAG representation with graph operations."""

    def __init__(
        self,
        nodes: dict[str, DAGNode],
        edges: dict[str, list[str]],
    ) -> None:
        self._nodes = nodes
        # edges: node_id -> list of dependency node_ids (predecessors)
        self._edges = edges
        # reverse_edges: node_id -> list of successor node_ids
        self._reverse_edges: dict[str, list[str]] = {}
        for nid, deps in edges.items():
            for dep in deps:
                self._reverse_edges.setdefault(dep, []).append(nid)

    @property
    def nodes(self) -> dict[str, DAGNode]:
        return self._nodes

    def topological_sort(self) -> list[str]:
        """Kahn's algorithm. Raises ValueError on cycle."""
        in_degree: dict[str, int] = {}
        for nid in self._nodes:
            in_degree[nid] = len(self._edges.get(nid, []))

        queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
        result: list[str] = []

        while queue:
            nid = queue.popleft()
            result.append(nid)
            for child in self._reverse_edges.get(nid, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(result) != len(self._nodes):
            raise ValueError("Cycle detected in DAG")
        return result

    def get_ready_nodes(self) -> list[DAGNode]:
        """Return nodes whose deps are all COMPLETED and self is PENDING."""
        ready: list[DAGNode] = []
        for nid, node in self._nodes.items():
            if node.status != DAGNodeStatus.PENDING:
                continue
            deps = self._edges.get(nid, [])
            if all(self._nodes[d].status == DAGNodeStatus.COMPLETED for d in deps):
                ready.append(node)
        return ready

    def mark_running(self, node_id: str) -> None:
        self._nodes[node_id].status = DAGNodeStatus.RUNNING

    def mark_completed(self, node_id: str) -> None:
        self._nodes[node_id].status = DAGNodeStatus.COMPLETED

    def mark_failed(self, node_id: str, error: str | None = None) -> None:
        self._nodes[node_id].status = DAGNodeStatus.FAILED
        if error:
            self._nodes[node_id].error = error

    def propagate_failure(self, node_id: str) -> list[str]:
        """Cancel all downstream nodes. Returns list of cancelled node_ids."""
        cancelled: list[str] = []
        queue = deque(self._reverse_edges.get(node_id, []))
        visited: set[str] = set()
        while queue:
            nid = queue.popleft()
            if nid in visited:
                continue
            visited.add(nid)
            node = self._nodes[nid]
            if node.status in (DAGNodeStatus.PENDING, DAGNodeStatus.READY):
                node.status = DAGNodeStatus.CANCELLED
                cancelled.append(nid)
                queue.extend(self._reverse_edges.get(nid, []))
        return cancelled

    def is_complete(self) -> bool:
        """Check if all nodes are in a terminal state."""
        terminal = {DAGNodeStatus.COMPLETED, DAGNodeStatus.FAILED, DAGNodeStatus.CANCELLED}
        return all(n.status in terminal for n in self._nodes.values())

    def has_failures(self) -> bool:
        return any(n.status == DAGNodeStatus.FAILED for n in self._nodes.values())

    @classmethod
    def from_execution(cls, execution: DAGExecution) -> DAG:
        """Build DAG from a DAGExecution."""
        nodes = {n.node_id: n for n in execution.nodes}
        edges = {n.node_id: list(n.depends_on) for n in execution.nodes}
        return cls(nodes, edges)
