"""Storage DAG 领域 Mixin —— DAG 执行与节点。"""

import json
import sqlite3
import threading
from datetime import UTC, datetime

from tianshu.models import DAGExecution, DAGNode, Memorial
from tianshu.storage.mappers import (
    _row_to_dag_execution,
    _row_to_dag_node,
    _row_to_memorial,
)


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

    def claim_dag_retry(
        self,
        dag_id: str,
        *,
        expected_root_memorial_id: str,
        root_memorial_id: str,
        from_node_ids: list[str] | None = None,
    ) -> list[str] | None:
        """Atomically claim a terminal DAG and reset exactly one retry slice.

        ``None`` means the terminal root/status compare-and-swap was lost.
        An empty list means the claim was valid but there was no retry target;
        both outcomes leave the DAG unchanged.
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                execution = self._conn.execute(
                    """
                    SELECT edict_id, status, root_memorial_id
                    FROM dag_executions
                    WHERE id = ?
                    """,
                    (dag_id,),
                ).fetchone()
                if (
                    execution is None
                    or execution["status"] not in {"failed", "cancelled"}
                    or execution["root_memorial_id"] != expected_root_memorial_id
                ):
                    self._conn.rollback()
                    return None

                rows = self._conn.execute(
                    """
                    SELECT node_id, status, depends_on_json
                    FROM dag_nodes
                    WHERE dag_execution_id = ?
                    """,
                    (dag_id,),
                ).fetchall()
                known = {row["node_id"] for row in rows}
                if from_node_ids is None:
                    targets = {row["node_id"] for row in rows if row["status"] == "failed"}
                else:
                    targets = set(from_node_ids)
                    unknown = sorted(targets - known)
                    if unknown:
                        raise ValueError("unknown DAG nodes: " + ", ".join(unknown))
                if not targets:
                    self._conn.rollback()
                    return []

                reset_ids = set(targets)
                changed = True
                while changed:
                    changed = False
                    for row in rows:
                        node_id = row["node_id"]
                        if node_id in reset_ids or row["status"] not in {
                            "failed",
                            "cancelled",
                        }:
                            continue
                        dependencies = json.loads(row["depends_on_json"] or "[]")
                        if any(dependency in reset_ids for dependency in dependencies):
                            reset_ids.add(node_id)
                            changed = True

                retry_root = self._conn.execute(
                    """
                    SELECT child.id
                    FROM memorials AS child
                    JOIN memorials AS parent ON parent.id = ?
                    WHERE child.id = ?
                      AND child.edict_id = ?
                      AND child.parent_memorial_id = parent.id
                      AND child.attempt = parent.attempt + 1
                      AND child.status = 'submitted'
                      AND parent.edict_id = child.edict_id
                      AND parent.status IN ('failed', 'cancelled')
                    """,
                    (
                        expected_root_memorial_id,
                        root_memorial_id,
                        execution["edict_id"],
                    ),
                ).fetchone()
                if retry_root is None:
                    self._conn.rollback()
                    return None

                claimed = self._conn.execute(
                    """
                    UPDATE dag_executions
                    SET root_memorial_id = ?, status = 'pending', completed_at = NULL
                    WHERE id = ?
                      AND root_memorial_id = ?
                      AND status IN ('failed', 'cancelled')
                    """,
                    (root_memorial_id, dag_id, expected_root_memorial_id),
                )
                if claimed.rowcount != 1:
                    self._conn.rollback()
                    return None

                ordered_ids = sorted(reset_ids)
                placeholders = ", ".join("?" for _ in ordered_ids)
                reset = self._conn.execute(
                    f"""
                    UPDATE dag_nodes
                    SET status = 'pending', error = NULL,
                        started_at = NULL, completed_at = NULL
                    WHERE dag_execution_id = ?
                      AND node_id IN ({placeholders})
                    """,  # noqa: S608 - placeholders are generated, never user input
                    (dag_id, *ordered_ids),
                )
                if reset.rowcount != len(ordered_ids):
                    raise RuntimeError("DAG retry reset set changed during claim")
                self._conn.commit()
                return ordered_ids
            except BaseException:
                self._conn.rollback()
                raise

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

    def update_dag_node_memorial(
        self,
        dag_execution_id: str,
        node_id: str,
        memorial_id: str,
    ) -> bool:
        """Bind a node attempt to its same-DAG, same-edict child memorial."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE dag_nodes
                SET memorial_id = ?
                WHERE dag_execution_id = ?
                  AND node_id = ?
                  AND EXISTS (
                      SELECT 1
                      FROM dag_executions AS d
                      JOIN memorials AS m
                        ON m.edict_id = d.edict_id
                       AND m.id = ?
                       AND m.dag_node_id = ?
                      WHERE d.id = ?
                        AND (
                            d.root_memorial_id IS NULL
                            OR m.parent_memorial_id = d.root_memorial_id
                        )
                  )
                """,
                (
                    memorial_id,
                    dag_execution_id,
                    node_id,
                    memorial_id,
                    node_id,
                    dag_execution_id,
                ),
            )
        return cursor.rowcount == 1

    def get_completed_dag_node_memorial(
        self,
        dag_execution_id: str,
        node_id: str,
    ) -> Memorial | None:
        """Load completed node evidence, with a bounded legacy fallback."""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT m.*
                FROM dag_nodes AS n
                JOIN dag_executions AS d ON d.id = n.dag_execution_id
                JOIN memorials AS m ON m.id = n.memorial_id
                WHERE n.dag_execution_id = ?
                  AND n.node_id = ?
                  AND n.status = 'completed'
                  AND m.edict_id = d.edict_id
                  AND m.dag_node_id = n.node_id
                  AND m.status = 'completed'
                LIMIT 1
                """,
                (dag_execution_id, node_id),
            ).fetchone()
            if row is not None:
                return _row_to_memorial(row)

            row = self._conn.execute(
                """
                SELECT m.*
                FROM dag_nodes AS n
                JOIN dag_executions AS d ON d.id = n.dag_execution_id
                JOIN memorials AS m
                  ON m.edict_id = d.edict_id
                 AND m.dag_node_id = n.node_id
                WHERE n.dag_execution_id = ?
                  AND n.node_id = ?
                  AND n.status = 'completed'
                  AND n.memorial_id IS NULL
                  AND m.status = 'completed'
                  AND (
                      m.created_at > d.created_at
                      OR (m.created_at = d.created_at AND m.id > d.id)
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM dag_executions AS later
                      WHERE later.edict_id = d.edict_id
                        AND (
                            later.created_at > d.created_at
                            OR (later.created_at = d.created_at AND later.id > d.id)
                        )
                        AND (
                            later.created_at < m.created_at
                            OR (later.created_at = m.created_at AND later.id < m.id)
                        )
                  )
                ORDER BY m.created_at DESC, m.id DESC
                LIMIT 1
                """,
                (dag_execution_id, node_id),
            ).fetchone()
        return _row_to_memorial(row) if row is not None else None

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
