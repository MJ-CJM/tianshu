"""Atomic, idempotent ingress for dispatcher-owned root executions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Coroutine, Mapping
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


class ManagedRunIngress:
    """Create/reuse one root and attempt, commit, then wake reconciliation."""

    def __init__(
        self,
        storage: Storage,
        reconciler: _Reconciler,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._storage = storage
        self._reconciler = reconciler
        self._clock = clock or (lambda: datetime.now(UTC))
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
                memorial = Memorial(
                    id=memorial_id,
                    edict_id=command.edict_id,
                    instruction=command.instruction,
                    status=TaskStatus.SUBMITTED,
                    parent_memorial_id=command.parent_memorial_id,
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
                    or existing["parent_memorial_id"] != command.parent_memorial_id
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


__all__ = ["ManagedRunCommand", "ManagedRunIngress", "ManagedRunResult"]
