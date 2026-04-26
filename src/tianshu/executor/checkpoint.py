"""CheckpointManager — save/restore node execution state for resilient retry."""

from __future__ import annotations

import json
import logging

from tianshu.dag.models import DAGNode
from tianshu.models.common import UsageSummary
from tianshu.storage import Storage

logger = logging.getLogger(__name__)


class Checkpoint:
    """Snapshot of a node's execution state."""

    def __init__(
        self,
        iteration: int,
        messages: list[dict],
        usage: UsageSummary,
    ) -> None:
        self.iteration = iteration
        self.messages = messages
        self.usage = usage

    def to_json(self) -> str:
        return json.dumps({
            "iteration": self.iteration,
            "messages": self.messages,
            "usage": self.usage.model_dump(),
        })

    @classmethod
    def from_json(cls, data: str) -> Checkpoint:
        d = json.loads(data)
        return cls(
            iteration=d["iteration"],
            messages=d["messages"],
            usage=UsageSummary(**d["usage"]),
        )


class CheckpointManager:
    """Saves and restores execution checkpoints for DAG nodes."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def save(
        self,
        dag_execution_id: str,
        node_id: str,
        checkpoint: Checkpoint,
    ) -> None:
        """Persist a checkpoint for a node."""
        self._storage.update_dag_node_checkpoint(
            dag_execution_id, node_id, checkpoint.to_json(),
        )

    def load(
        self,
        dag_execution_id: str,
        node_id: str,
    ) -> Checkpoint | None:
        """Load a previously saved checkpoint."""
        nodes = self._storage.get_dag_nodes(dag_execution_id)
        for node in nodes:
            if node.node_id == node_id and node.checkpoint_json:
                return Checkpoint.from_json(node.checkpoint_json)
        return None

    def clear(self, dag_execution_id: str, node_id: str) -> None:
        """Clear a checkpoint."""
        self._storage.update_dag_node_checkpoint(
            dag_execution_id, node_id, None,
        )


class OuterLoopCheckpoint:
    """outer loop 状态快照 —— per-edict（区别于 DAG node 的 Checkpoint）。"""

    KIND = "outer_loop"

    def __init__(self, edict_id: str, state_dict: dict, saved_at: str) -> None:
        self.edict_id = edict_id
        self.state_dict = state_dict
        self.saved_at = saved_at

    def to_json(self) -> str:
        return json.dumps({
            "kind": self.KIND,
            "edict_id": self.edict_id,
            "state": self.state_dict,
            "saved_at": self.saved_at,
        })

    @classmethod
    def from_json(cls, data: str) -> "OuterLoopCheckpoint":
        d = json.loads(data)
        return cls(
            edict_id=d["edict_id"],
            state_dict=d["state"],
            saved_at=d["saved_at"],
        )
