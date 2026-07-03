"""Tests for tianshu.dag — topological sort / cycle detection / ready-node advance.

Pure data structure, no IO — mirrors tests/test_loop_state.py style.
"""

from __future__ import annotations

import pytest

from tianshu.dag import DAG, DAGExecution, DAGNode, DAGNodeStatus


def _node(node_id: str, depends_on: list[str] | None = None) -> DAGNode:
    return DAGNode(node_id=node_id, description=f"do {node_id}", depends_on=depends_on or [])


def _dag(nodes: list[DAGNode]) -> DAG:
    node_map = {n.node_id: n for n in nodes}
    edges = {n.node_id: list(n.depends_on) for n in nodes}
    return DAG(node_map, edges)


class TestTopologicalSort:
    def test_linear_chain_respects_dependency_order(self):
        dag = _dag([_node("a"), _node("b", ["a"]), _node("c", ["b"])])
        order = dag.topological_sort()
        assert len(order) == 3
        assert order.index("a") < order.index("b") < order.index("c")

    def test_diamond_shape_respects_partial_order(self):
        # a -> b, a -> c, b -> d, c -> d
        dag = _dag(
            [
                _node("a"),
                _node("b", ["a"]),
                _node("c", ["a"]),
                _node("d", ["b", "c"]),
            ]
        )
        order = dag.topological_sort()
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_cycle_raises_value_error(self):
        dag = _dag([_node("a", ["b"]), _node("b", ["a"])])
        with pytest.raises(ValueError, match="Cycle"):
            dag.topological_sort()

    def test_self_dependency_is_a_cycle(self):
        dag = _dag([_node("a", ["a"])])
        with pytest.raises(ValueError, match="Cycle"):
            dag.topological_sort()


class TestReadyNodes:
    def test_only_no_dep_nodes_ready_initially(self):
        dag = _dag([_node("a"), _node("b", ["a"])])
        ready_ids = {n.node_id for n in dag.get_ready_nodes()}
        assert ready_ids == {"a"}

    def test_downstream_becomes_ready_after_upstream_completes(self):
        dag = _dag([_node("a"), _node("b", ["a"])])
        dag.mark_completed("a")
        ready_ids = {n.node_id for n in dag.get_ready_nodes()}
        assert ready_ids == {"b"}

    def test_running_node_is_not_ready(self):
        dag = _dag([_node("a")])
        dag.mark_running("a")
        assert dag.get_ready_nodes() == []

    def test_node_with_partial_deps_completed_not_ready(self):
        dag = _dag([_node("a"), _node("b"), _node("c", ["a", "b"])])
        dag.mark_completed("a")
        ready_ids = {n.node_id for n in dag.get_ready_nodes()}
        assert ready_ids == {"b"}


class TestStatusTransitions:
    def test_mark_running_then_completed(self):
        dag = _dag([_node("a")])
        dag.mark_running("a")
        assert dag.nodes["a"].status == DAGNodeStatus.RUNNING
        dag.mark_completed("a")
        assert dag.nodes["a"].status == DAGNodeStatus.COMPLETED

    def test_mark_failed_records_error(self):
        dag = _dag([_node("a")])
        dag.mark_failed("a", error="boom")
        assert dag.nodes["a"].status == DAGNodeStatus.FAILED
        assert dag.nodes["a"].error == "boom"


class TestFailurePropagation:
    def test_propagate_failure_cancels_all_downstream(self):
        # a -> b -> c；d 是无关分支，不应受影响
        dag = _dag(
            [
                _node("a"),
                _node("b", ["a"]),
                _node("c", ["b"]),
                _node("d"),
            ]
        )
        dag.mark_failed("a")
        cancelled = dag.propagate_failure("a")
        assert set(cancelled) == {"b", "c"}
        assert dag.nodes["b"].status == DAGNodeStatus.CANCELLED
        assert dag.nodes["c"].status == DAGNodeStatus.CANCELLED
        assert dag.nodes["d"].status == DAGNodeStatus.PENDING

    def test_propagate_failure_skips_already_terminal_downstream(self):
        dag = _dag([_node("a"), _node("b", ["a"])])
        dag.mark_completed("b")  # 模拟已提前完成（并发/异常场景）
        dag.mark_failed("a")
        cancelled = dag.propagate_failure("a")
        assert cancelled == []
        assert dag.nodes["b"].status == DAGNodeStatus.COMPLETED


class TestCompletionChecks:
    def test_is_complete_false_when_pending_nodes_remain(self):
        dag = _dag([_node("a"), _node("b", ["a"])])
        dag.mark_completed("a")
        assert dag.is_complete() is False

    def test_is_complete_true_when_all_terminal(self):
        dag = _dag([_node("a"), _node("b", ["a"])])
        dag.mark_completed("a")
        dag.mark_failed("b")
        assert dag.is_complete() is True

    def test_has_failures(self):
        dag = _dag([_node("a")])
        assert dag.has_failures() is False
        dag.mark_failed("a")
        assert dag.has_failures() is True


class TestFromExecution:
    def test_from_execution_builds_matching_graph(self):
        execution = DAGExecution(
            edict_id="ed1",
            nodes=[_node("a"), _node("b", ["a"])],
        )
        dag = DAG.from_execution(execution)
        assert set(dag.nodes.keys()) == {"a", "b"}
        order = dag.topological_sort()
        assert order.index("a") < order.index("b")
