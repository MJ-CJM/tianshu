"""Storage DAG 领域 Mixin —— DAG 执行与节点。"""

import json
import sqlite3
import threading
from datetime import UTC, datetime

from tianshu.models import DAGExecution, DAGNode
from tianshu.storage.mappers import _row_to_dag_execution, _row_to_dag_node


class DagMixin:
    _conn: sqlite3.Connection
    _lock: threading.Lock

    # --- DAG Executions ---

    def save_dag_execution(self, execution: DAGExecution) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO dag_executions
                   (id, edict_id, plan_json, status, root_memorial_id,
                    max_concurrency, created_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution.id,
                    execution.edict_id,
                    execution.plan_json,
                    execution.status,
                    execution.root_memorial_id,
                    execution.max_concurrency,
                    execution.created_at.isoformat(),
                    execution.completed_at.isoformat() if execution.completed_at else None,
                ),
            )
            for node in execution.nodes:
                node.dag_execution_id = execution.id
                self._conn.execute(
                    """INSERT INTO dag_nodes
                       (node_id, dag_execution_id, description, depends_on_json,
                        status, assigned_official, assigned_worker,
                        tools_required_json, memorial_id, checkpoint_json,
                        started_at, completed_at, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        node.node_id,
                        execution.id,
                        node.description,
                        json.dumps(node.depends_on),
                        node.status.value,
                        node.assigned_official,
                        node.assigned_worker,
                        json.dumps(node.tools_required),
                        node.memorial_id,
                        node.checkpoint_json,
                        node.started_at.isoformat() if node.started_at else None,
                        node.completed_at.isoformat() if node.completed_at else None,
                        node.error,
                    ),
                )

    def get_dag_execution(self, dag_id: str) -> DAGExecution | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM dag_executions WHERE id = ?", (dag_id,)
            ).fetchone()
            if not row:
                return None
            nodes = self._get_dag_nodes_internal(dag_id)
        return _row_to_dag_execution(row, nodes)

    def get_dag_by_edict(self, edict_id: str) -> DAGExecution | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM dag_executions WHERE edict_id = ? ORDER BY created_at DESC LIMIT 1",
                (edict_id,),
            ).fetchone()
            if not row:
                return None
            nodes = self._get_dag_nodes_internal(row["id"])
        return _row_to_dag_execution(row, nodes)

    def get_dag_nodes(self, dag_execution_id: str) -> list[DAGNode]:
        with self._lock:
            return self._get_dag_nodes_internal(dag_execution_id)

    def _get_dag_nodes_internal(self, dag_execution_id: str) -> list[DAGNode]:
        rows = self._conn.execute(
            "SELECT * FROM dag_nodes WHERE dag_execution_id = ?",
            (dag_execution_id,),
        ).fetchall()
        return [_row_to_dag_node(r) for r in rows]

    def update_dag_execution_status(
        self,
        dag_id: str,
        status: str,
        completed_at: datetime | None = None,
    ) -> None:
        with self._lock, self._conn:
            if completed_at:
                self._conn.execute(
                    "UPDATE dag_executions SET status = ?, completed_at = ? WHERE id = ?",
                    (status, completed_at.isoformat(), dag_id),
                )
            else:
                self._conn.execute(
                    "UPDATE dag_executions SET status = ? WHERE id = ?",
                    (status, dag_id),
                )

    def update_dag_node_status(
        self,
        dag_execution_id: str,
        node_id: str,
        status: str,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            if status == "running":
                self._conn.execute(
                    "UPDATE dag_nodes SET status = ?, started_at = ? WHERE dag_execution_id = ? AND node_id = ?",
                    (status, now, dag_execution_id, node_id),
                )
            elif status in ("completed", "failed", "cancelled"):
                self._conn.execute(
                    "UPDATE dag_nodes SET status = ?, completed_at = ?, error = ? WHERE dag_execution_id = ? AND node_id = ?",
                    (status, now, error, dag_execution_id, node_id),
                )
            else:
                self._conn.execute(
                    "UPDATE dag_nodes SET status = ?, error = ? WHERE dag_execution_id = ? AND node_id = ?",
                    (status, error, dag_execution_id, node_id),
                )

    def update_dag_node_checkpoint(
        self,
        dag_execution_id: str,
        node_id: str,
        checkpoint_json: str | None,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE dag_nodes SET checkpoint_json = ? WHERE dag_execution_id = ? AND node_id = ?",
                (checkpoint_json, dag_execution_id, node_id),
            )

    def save_dag_node(self, node: DAGNode) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO dag_nodes
                   (node_id, dag_execution_id, description, depends_on_json,
                    status, assigned_official, assigned_worker,
                    tools_required_json, memorial_id, checkpoint_json,
                    started_at, completed_at, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    node.node_id,
                    node.dag_execution_id,
                    node.description,
                    json.dumps(node.depends_on),
                    node.status.value,
                    node.assigned_official,
                    node.assigned_worker,
                    json.dumps(node.tools_required),
                    node.memorial_id,
                    node.checkpoint_json,
                    node.started_at.isoformat() if node.started_at else None,
                    node.completed_at.isoformat() if node.completed_at else None,
                    node.error,
                ),
            )
