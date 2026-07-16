"""Atomic, idempotent ingress for dispatcher-owned root executions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from tianshu.models import Memorial, TaskStatus
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from tianshu.models.edict import EdictRuntime
from tianshu.models.events import EventEnvelope
from tianshu.storage import Storage
from tianshu.storage.memorial_repo import insert_memorial
from tianshu.storage.outbox_repo import OutboxRepository


class _Reconciler(Protocol):
    def reconcile_once(self) -> Coroutine[Any, Any, int]: ...


@dataclass(frozen=True, slots=True)
class ManagedRunCommand:
    edict_id: str
    idempotency_key: str
    instruction: str
    event_type: str
    event_payload: Mapping[str, JsonValue]
    parent_memorial_id: str | None = None
    runtime_override: dict[str, Any] | None = None
    acceptance_override: AcceptanceCriteria | None = None


@dataclass(frozen=True, slots=True)
class ManagedRunResult:
    memorial: Memorial
    attempt_id: str
    event_id: str
    deduplicated: bool


@dataclass(frozen=True, slots=True)
class ManagedDagRetryResult:
    memorial: Memorial
    attempt_id: str
    reset_node_ids: tuple[str, ...]


class ManagedRunBusy(RuntimeError):
    """A distinct managed root is still active for the Edict."""


class ManagedRunIngress:
    """Create/reuse one root and attempt, commit, then wake reconciliation."""

    def __init__(
        self,
        storage: Storage,
        reconciler: _Reconciler,
        *,
        clock: Callable[[], datetime] | None = None,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._storage = storage
        self._reconciler = reconciler
        self._clock = clock or (lambda: datetime.now(UTC))
        self._boundary_hook = boundary_hook
        self._outbox = OutboxRepository()

    async def start(self, command: ManagedRunCommand) -> ManagedRunResult:
        self._validate(command)
        digest = hashlib.sha256(
            f"{command.edict_id}\0{command.idempotency_key}".encode()
        ).hexdigest()
        memorial_id = f"managed-root-{digest}"
        attempt_id = f"managed-attempt-{digest}"
        event_id = f"managed-event-{digest}"
        fingerprint = canonical_sha256(
            {
                "schema_version": 1,
                "edict_id": command.edict_id,
                "idempotency_key": command.idempotency_key,
                "instruction": command.instruction,
                "event_type": command.event_type,
                "event_payload": dict(command.event_payload),
                "parent_memorial_id": command.parent_memorial_id,
                "runtime_override": command.runtime_override,
                "acceptance_override": (
                    command.acceptance_override.model_dump(mode="json")
                    if command.acceptance_override is not None
                    else None
                ),
            }
        )
        now = self._clock().astimezone(UTC)
        deduplicated = False
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            edict = connection.execute(
                "SELECT runtime_json FROM edicts WHERE id=?",
                (command.edict_id,),
            ).fetchone()
            if edict is None:
                raise RuntimeError("managed run edict is unavailable")
            runtime = EdictRuntime.model_validate(json.loads(str(edict["runtime_json"])))
            existing = connection.execute(
                "SELECT * FROM memorials WHERE id=?",
                (memorial_id,),
            ).fetchone()
            if existing is None:
                parent_memorial_id = command.parent_memorial_id
                if command.event_type == "followup.submitted":
                    active = connection.execute(
                        """
                        SELECT 1 FROM memorials
                        WHERE edict_id=? AND dag_node_id IS NULL
                          AND status NOT IN ('completed','failed','cancelled')
                        LIMIT 1
                        """,
                        (command.edict_id,),
                    ).fetchone()
                    if active is not None:
                        raise ManagedRunBusy("managed run has active work")
                    if parent_memorial_id is None:
                        parent = connection.execute(
                            """
                            SELECT id FROM memorials
                            WHERE edict_id=? AND dag_node_id IS NULL
                            ORDER BY created_at DESC, id DESC
                            LIMIT 1
                            """,
                            (command.edict_id,),
                        ).fetchone()
                        parent_memorial_id = str(parent["id"]) if parent is not None else None
                memorial = Memorial(
                    id=memorial_id,
                    edict_id=command.edict_id,
                    instruction=command.instruction,
                    status=TaskStatus.SUBMITTED,
                    parent_memorial_id=parent_memorial_id,
                    runtime_override=command.runtime_override,
                    acceptance_override=command.acceptance_override,
                    created_at=now,
                )
                insert_memorial(connection, memorial)
                available_at = now
                payload = {**dict(command.event_payload), "managed_request_hash": fingerprint}
                self._outbox.add(
                    connection,
                    EventEnvelope(
                        event_id=event_id,
                        event_type=command.event_type,
                        edict_id=command.edict_id,
                        memorial_id=memorial_id,
                        producer="managed-run-ingress",
                        timestamp=now,
                        payload=payload,
                    ),
                )
            else:
                deduplicated = True
                available_at = datetime.fromisoformat(str(existing["created_at"])).astimezone(UTC)
                stored_event = self._outbox.get(connection, event_id)
                expected_payload = canonical_json_bytes(
                    {**dict(command.event_payload), "managed_request_hash": fingerprint}
                ).decode("utf-8")
                if (
                    existing["edict_id"] != command.edict_id
                    or existing["instruction"] != command.instruction
                    or (
                        command.parent_memorial_id is not None
                        and existing["parent_memorial_id"] != command.parent_memorial_id
                    )
                    or stored_event is None
                    or stored_event.event_type != command.event_type
                    or stored_event.payload_json != expected_payload
                ):
                    raise RuntimeError(
                        "managed run idempotency key conflicts with durable envelope"
                    )
            attempt = self._storage.attempt_repo.enqueue_initial(
                connection,
                memorial_id=memorial_id,
                available_at=available_at,
                max_attempts=runtime.retry_limit + 1,
                attempt_id=attempt_id,
            )
            unit_of_work.commit()
        durable = self._storage.get_memorial(memorial_id)
        if durable is None:  # pragma: no cover - committed insert/replay established it
            raise RuntimeError("managed run root disappeared after commit")
        await self._reconciler.reconcile_once()
        return ManagedRunResult(
            memorial=durable,
            attempt_id=attempt.attempt_id,
            event_id=event_id,
            deduplicated=deduplicated,
        )

    async def adopt_legacy(self, event: EventEnvelope) -> ManagedRunResult:
        """Adopt one safe upgrade-time event using its durable event identity."""
        if event.event_type not in {"edict.scheduled", "plan.completed", "edict.resume"}:
            raise ValueError("unsupported legacy managed event")
        if not event.event_id.strip() or event.memorial_id is None or event.edict_id is None:
            raise ValueError("legacy managed event identity is incomplete")
        digest = canonical_sha256(event)
        requested_attempt_id = f"legacy-attempt-{digest}"
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            root = connection.execute(
                """
                SELECT memorials.edict_id, memorials.status, memorials.dag_node_id,
                       edicts.runtime_json
                FROM memorials
                JOIN edicts ON edicts.id=memorials.edict_id
                WHERE memorials.id=? AND memorials.edict_id=?
                """,
                (event.memorial_id, event.edict_id),
            ).fetchone()
            if root is None or root["dag_node_id"] is not None:
                raise RuntimeError("legacy managed root is unavailable")
            existing = connection.execute(
                """
                SELECT attempt_id FROM execution_attempts
                WHERE memorial_id=? AND attempt_no=1
                """,
                (event.memorial_id,),
            ).fetchone()
            runtime = EdictRuntime.model_validate(json.loads(str(root["runtime_json"])))
            if existing is not None and existing["attempt_id"] != requested_attempt_id:
                raise RuntimeError("legacy managed event conflicts with prior adoption")
            if existing is None:
                if root["status"] in {"completed", "failed", "cancelled"}:
                    raise RuntimeError("legacy managed root is already terminal")
                if event.event_type == "plan.completed":
                    self._require_restart_safe_legacy_plan(connection, event)
            attempt = self._storage.attempt_repo.enqueue_initial(
                connection,
                memorial_id=event.memorial_id,
                available_at=event.timestamp.astimezone(UTC),
                max_attempts=runtime.retry_limit + 1,
                attempt_id=requested_attempt_id,
            )
            unit_of_work.commit()
        durable = self._storage.get_memorial(event.memorial_id)
        if durable is None:  # pragma: no cover
            raise RuntimeError("legacy managed root disappeared after commit")
        await self._reconciler.reconcile_once()
        return ManagedRunResult(
            memorial=durable,
            attempt_id=attempt.attempt_id,
            event_id=event.event_id,
            deduplicated=existing is not None,
        )

    async def retry_dag(
        self,
        *,
        dag_id: str,
        idempotency_key: str,
        from_node_ids: Sequence[str] | None = None,
    ) -> ManagedDagRetryResult:
        """Atomically create/reuse one retry root, DAG claim, and attempt."""
        if not dag_id.strip() or not idempotency_key.strip():
            raise ValueError("DAG retry identity must be non-blank")
        requested_nodes = tuple(sorted(set(from_node_ids))) if from_node_ids is not None else None
        digest = hashlib.sha256(f"{dag_id}\0{idempotency_key}".encode()).hexdigest()
        memorial_id = f"managed-dag-retry-root-{digest}"
        attempt_id = f"managed-dag-retry-attempt-{digest}"
        event_id = f"managed-dag-retry-event-{digest}"
        fingerprint = canonical_sha256(
            {
                "schema_version": 1,
                "dag_id": dag_id,
                "idempotency_key": idempotency_key,
                "from_node_ids": list(requested_nodes) if requested_nodes is not None else None,
            }
        )
        now = self._clock().astimezone(UTC)
        reset_ids: tuple[str, ...]
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            existing_root = connection.execute(
                "SELECT edict_id FROM memorials WHERE id=?",
                (memorial_id,),
            ).fetchone()
            if existing_root is not None:
                stored_event = self._outbox.get(connection, event_id)
                stored_attempt = connection.execute(
                    """
                    SELECT attempt_id FROM execution_attempts
                    WHERE attempt_id=? AND memorial_id=?
                    """,
                    (attempt_id, memorial_id),
                ).fetchone()
                payload = (
                    json.loads(stored_event.payload_json) if stored_event is not None else None
                )
                if (
                    stored_attempt is None
                    or not isinstance(payload, dict)
                    or payload.get("managed_request_hash") != fingerprint
                    or not isinstance(payload.get("reset_node_ids"), list)
                ):
                    raise RuntimeError(
                        "managed DAG retry idempotency key conflicts with durable envelope"
                    )
                reset_ids = tuple(str(item) for item in payload["reset_node_ids"])
            else:
                execution = connection.execute(
                    """
                    SELECT dag_executions.edict_id, dag_executions.status,
                           dag_executions.root_memorial_id, edicts.runtime_json
                    FROM dag_executions
                    JOIN edicts ON edicts.id=dag_executions.edict_id
                    WHERE dag_executions.id=?
                    """,
                    (dag_id,),
                ).fetchone()
                if execution is None or execution["status"] not in {"failed", "cancelled"}:
                    raise ValueError("DAG execution must be failed or cancelled to retry")
                previous_root_id = execution["root_memorial_id"]
                previous_root = connection.execute(
                    """
                    SELECT instruction, attempt, runtime_override_json, acceptance_override_json
                    FROM memorials
                    WHERE id=? AND edict_id=? AND dag_node_id IS NULL
                      AND status IN ('failed','cancelled')
                    """,
                    (previous_root_id, execution["edict_id"]),
                ).fetchone()
                if previous_root is None:
                    raise ValueError("DAG retry root is unavailable")
                rows = connection.execute(
                    """
                    SELECT node_id, status, depends_on_json
                    FROM dag_nodes WHERE dag_execution_id=?
                    """,
                    (dag_id,),
                ).fetchall()
                known = {str(row["node_id"]) for row in rows}
                targets = (
                    {str(row["node_id"]) for row in rows if row["status"] == "failed"}
                    if requested_nodes is None
                    else set(requested_nodes)
                )
                unknown = sorted(targets - known)
                if unknown:
                    raise ValueError("unknown DAG nodes: " + ", ".join(unknown))
                if not targets:
                    raise ValueError("DAG retry has no retryable nodes")
                reset = set(targets)
                changed = True
                while changed:
                    changed = False
                    for row in rows:
                        node_id = str(row["node_id"])
                        if node_id in reset or row["status"] not in {"failed", "cancelled"}:
                            continue
                        dependencies = json.loads(str(row["depends_on_json"] or "[]"))
                        if any(dependency in reset for dependency in dependencies):
                            reset.add(node_id)
                            changed = True
                reset_ids = tuple(sorted(reset))
                runtime_override = (
                    json.loads(str(previous_root["runtime_override_json"]))
                    if previous_root["runtime_override_json"] is not None
                    else None
                )
                acceptance_override = (
                    AcceptanceCriteria.model_validate_json(
                        str(previous_root["acceptance_override_json"])
                    )
                    if previous_root["acceptance_override_json"] is not None
                    else None
                )
                retry_root = Memorial(
                    id=memorial_id,
                    edict_id=str(execution["edict_id"]),
                    instruction=str(previous_root["instruction"] or ""),
                    status=TaskStatus.SUBMITTED,
                    attempt=int(previous_root["attempt"]) + 1,
                    parent_memorial_id=str(previous_root_id),
                    runtime_override=runtime_override,
                    acceptance_override=acceptance_override,
                    created_at=now,
                )
                insert_memorial(connection, retry_root)
                self._observe_boundary("after_root")
                claimed = connection.execute(
                    """
                    UPDATE dag_executions
                    SET root_memorial_id=?, status='pending', completed_at=NULL
                    WHERE id=? AND root_memorial_id=?
                      AND status IN ('failed','cancelled')
                    """,
                    (memorial_id, dag_id, previous_root_id),
                )
                if claimed.rowcount != 1:
                    raise RuntimeError("DAG retry lost the atomic claim")
                placeholders = ", ".join("?" for _ in reset_ids)
                reset_cursor = connection.execute(
                    f"""
                    UPDATE dag_nodes
                    SET status='pending', error=NULL, started_at=NULL, completed_at=NULL
                    WHERE dag_execution_id=? AND node_id IN ({placeholders})
                    """,  # noqa: S608 - placeholders are generated, never user input
                    (dag_id, *reset_ids),
                )
                if reset_cursor.rowcount != len(reset_ids):
                    raise RuntimeError("DAG retry reset set changed during claim")
                self._observe_boundary("after_dag")
                runtime = EdictRuntime.model_validate(json.loads(str(execution["runtime_json"])))
                self._storage.attempt_repo.enqueue_initial(
                    connection,
                    memorial_id=memorial_id,
                    available_at=now,
                    max_attempts=runtime.retry_limit + 1,
                    attempt_id=attempt_id,
                )
                self._observe_boundary("after_attempt")
                self._outbox.add(
                    connection,
                    EventEnvelope(
                        event_id=event_id,
                        event_type="dag.retry_requested",
                        edict_id=str(execution["edict_id"]),
                        memorial_id=memorial_id,
                        timestamp=now,
                        producer="managed-run-ingress",
                        payload={
                            "dag_id": dag_id,
                            "reset_node_ids": list(reset_ids),
                            "managed_request_hash": fingerprint,
                        },
                    ),
                )
                self._observe_boundary("after_outbox")
            unit_of_work.commit()
        durable = self._storage.get_memorial(memorial_id)
        if durable is None:  # pragma: no cover
            raise RuntimeError("managed DAG retry root disappeared after commit")
        await self._reconciler.reconcile_once()
        return ManagedDagRetryResult(
            memorial=durable,
            attempt_id=attempt_id,
            reset_node_ids=reset_ids,
        )

    async def adopt_existing(
        self,
        *,
        memorial_id: str,
        idempotency_key: str,
        available_at: datetime,
    ) -> ManagedRunResult:
        """Attach durable attempt authority to an already-persisted root."""
        if not memorial_id.strip() or not idempotency_key.strip():
            raise ValueError("managed adoption identity must be non-blank")
        available_at = available_at.astimezone(UTC)
        digest = hashlib.sha256(f"{memorial_id}\0{idempotency_key}".encode()).hexdigest()
        requested_attempt_id = f"managed-attempt-{digest}"
        with self._storage.unit_of_work() as unit_of_work:
            connection = unit_of_work.connection
            root = connection.execute(
                """
                SELECT memorials.edict_id, memorials.status, memorials.dag_node_id,
                       edicts.runtime_json
                FROM memorials
                JOIN edicts ON edicts.id = memorials.edict_id
                WHERE memorials.id=?
                """,
                (memorial_id,),
            ).fetchone()
            if root is None or root["dag_node_id"] is not None:
                raise RuntimeError("managed adoption root is unavailable")
            if root["status"] in {"completed", "failed", "cancelled"}:
                raise RuntimeError("managed adoption root is already terminal")
            existing = connection.execute(
                """
                SELECT attempt_id FROM execution_attempts
                WHERE memorial_id=? AND attempt_no=1
                """,
                (memorial_id,),
            ).fetchone()
            runtime = EdictRuntime.model_validate(json.loads(str(root["runtime_json"])))
            attempt = self._storage.attempt_repo.enqueue_initial(
                connection,
                memorial_id=memorial_id,
                available_at=(
                    available_at
                    if existing is None
                    else datetime.fromisoformat(
                        str(
                            connection.execute(
                                "SELECT available_at FROM execution_attempts WHERE attempt_id=?",
                                (existing["attempt_id"],),
                            ).fetchone()[0]
                        )
                    ).astimezone(UTC)
                ),
                max_attempts=runtime.retry_limit + 1,
                attempt_id=requested_attempt_id
                if existing is None
                else str(existing["attempt_id"]),
            )
            unit_of_work.commit()
        durable = self._storage.get_memorial(memorial_id)
        if durable is None:  # pragma: no cover
            raise RuntimeError("managed adoption root disappeared after commit")
        await self._reconciler.reconcile_once()
        return ManagedRunResult(
            memorial=durable,
            attempt_id=attempt.attempt_id,
            event_id=idempotency_key,
            deduplicated=existing is not None,
        )

    @staticmethod
    def _validate(command: ManagedRunCommand) -> None:
        if not isinstance(command, ManagedRunCommand):
            raise TypeError("command must be ManagedRunCommand")
        for value, field in (
            (command.edict_id, "edict_id"),
            (command.idempotency_key, "idempotency_key"),
            (command.instruction, "instruction"),
            (command.event_type, "event_type"),
        ):
            if not value.strip():
                raise ValueError(f"{field} must be non-blank")

    def _require_restart_safe_legacy_plan(
        self,
        connection,
        event: EventEnvelope,
    ) -> None:
        decision_id = event.payload.get("decision_request_id")
        if not isinstance(decision_id, str) or event.memorial_id is None:
            raise RuntimeError(
                "legacy plan.completed retained: restart-safe canonical plan binding is missing"
            )
        from tianshu.application.plan_review_lifecycle import PlanReviewAttemptCoordinator

        state = self._storage.run_state_repo.load(connection, event.memorial_id)
        record = self._storage.decision_repo.get(connection, decision_id)
        root = connection.execute(
            "SELECT edict_id FROM memorials WHERE id=?",
            (event.memorial_id,),
        ).fetchone()
        binding_error = PlanReviewAttemptCoordinator._binding_error(  # noqa: SLF001
            state=state,
            record=record,
            memorial_id=event.memorial_id,
            decision_id=decision_id,
            memorial_edict_id=str(root["edict_id"]) if root is not None else None,
        )
        if binding_error is not None:
            raise RuntimeError(
                "legacy plan.completed retained: canonical plan binding conflicts with durable state"
            )

    def _observe_boundary(self, boundary: str) -> None:
        if self._boundary_hook is not None:
            self._boundary_hook(boundary)


__all__ = [
    "ManagedDagRetryResult",
    "ManagedRunBusy",
    "ManagedRunCommand",
    "ManagedRunIngress",
    "ManagedRunResult",
]
