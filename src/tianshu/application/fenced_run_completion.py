"""Fenced atomic projection of terminal run state."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.models import TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.events import EventEnvelope
from tianshu.models.run_state import RunPhase, RunStateV1
from tianshu.storage.attempt_ledger import (
    AttemptConflict,
    AttemptFenceLost,
    AttemptLeaseRepository,
)
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.storage.run_state_repo import RunStateRepository
from tianshu.storage.unit_of_work import SqliteUnitOfWork


@dataclass(frozen=True, slots=True)
class FencedRunCompletionCommand:
    authority: AttemptAuthority
    outcome: AttemptOutcomeV1
    memorial_status: TaskStatus
    summary: str | None = None
    result: str | None = None
    error: str | None = None
    run_state: RunStateV1 | None = None
    expected_run_state_version: int | None = None


class FencedRunCompletion:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], SqliteUnitOfWork],
        attempt_repository: AttemptLeaseRepository,
        *,
        boundary_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._attempt_repository = attempt_repository
        self._boundary_hook = boundary_hook
        self._outbox_repository = OutboxRepository()
        self._run_state_repository = RunStateRepository()

    def complete(self, command: FencedRunCompletionCommand) -> str:
        self._validate_command(command)
        authority = command.authority
        completed_at = command.outcome.completed_at
        event_id = _event_id(command)
        event_type = (
            "execution.completed"
            if command.outcome.disposition is AttemptDisposition.SUCCEEDED
            else "execution.failed"
        )
        try:
            with self._unit_of_work_factory() as unit_of_work:
                connection = unit_of_work.connection
                self._attempt_repository.require_current(
                    connection,
                    attempt_id=authority.attempt_id,
                    owner_id=authority.owner_id,
                    fencing_token=authority.fencing_token,
                    now=completed_at,
                )
                attempt = connection.execute(
                    "SELECT memorial_id FROM execution_attempts WHERE attempt_id = ?",
                    (authority.attempt_id,),
                ).fetchone()
                if attempt is None or attempt["memorial_id"] != authority.memorial_id:
                    raise AttemptFenceLost("attempt authority does not bind the Memorial")
                memorial = connection.execute(
                    "SELECT edict_id, dag_node_id FROM memorials WHERE id = ?",
                    (authority.memorial_id,),
                ).fetchone()
                if memorial is None or memorial["dag_node_id"] is not None:
                    raise AttemptConflict("attempt authority does not bind a root Memorial")
                cursor = connection.execute(
                    """
                    UPDATE memorials
                    SET status = ?, summary = ?, result = ?, error = ?, completed_at = ?
                    WHERE id = ? AND edict_id = ? AND dag_node_id IS NULL
                    """,
                    (
                        command.memorial_status.value,
                        command.summary,
                        command.result,
                        command.error,
                        completed_at.isoformat(),
                        authority.memorial_id,
                        memorial["edict_id"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise AttemptConflict("root Memorial projection conflict")
                self._observe_boundary("after_memorial")

                if command.run_state is not None:
                    assert command.expected_run_state_version is not None
                    self._run_state_repository.compare_and_swap(
                        connection,
                        command.run_state,
                        expected_version=command.expected_run_state_version,
                    )
                    self._observe_boundary("after_run_state")

                self._outbox_repository.add(
                    connection,
                    EventEnvelope(
                        event_id=event_id,
                        event_type=event_type,
                        edict_id=str(memorial["edict_id"]),
                        memorial_id=authority.memorial_id,
                        timestamp=completed_at,
                        producer="run-dispatcher",
                        payload={
                            "attempt_id": authority.attempt_id,
                            "fencing_token": authority.fencing_token,
                            "status": command.memorial_status.value,
                        },
                    ),
                )
                self._observe_boundary("after_outbox")
                self._observe_boundary("before_attempt")
                if not self._attempt_repository.complete_current(
                    connection,
                    attempt_id=authority.attempt_id,
                    owner_id=authority.owner_id,
                    fencing_token=authority.fencing_token,
                    outcome=command.outcome,
                ):
                    raise AttemptFenceLost("attempt authority is no longer current")
                unit_of_work.commit()
                return event_id
        except sqlite3.IntegrityError as exc:
            raise AttemptConflict("fenced completion projection conflict") from exc

    @staticmethod
    def _validate_command(command: FencedRunCompletionCommand) -> None:
        if not isinstance(command, FencedRunCompletionCommand):
            raise TypeError("command must be FencedRunCompletionCommand")
        expected = {
            AttemptDisposition.SUCCEEDED: (TaskStatus.COMPLETED, RunPhase.COMPLETED),
            AttemptDisposition.FAILED: (TaskStatus.FAILED, RunPhase.FAILED),
        }.get(command.outcome.disposition)
        if expected is None:
            raise ValueError("fenced terminal completion requires a terminal outcome")
        if command.memorial_status is not expected[0]:
            raise ValueError("Memorial terminal status conflicts with attempt outcome")
        if (command.run_state is None) != (command.expected_run_state_version is None):
            raise ValueError("RunState and its expected version must be supplied together")
        if command.run_state is not None:
            if command.run_state.memorial_id != command.authority.memorial_id:
                raise ValueError("RunState does not belong to the attempt Memorial")
            if command.run_state.phase is not expected[1]:
                raise ValueError("RunState terminal phase conflicts with attempt outcome")

    def _observe_boundary(self, boundary: str) -> None:
        if self._boundary_hook is not None:
            self._boundary_hook(boundary)


def _event_id(command: FencedRunCompletionCommand) -> str:
    authority = command.authority
    digest = hashlib.sha256(
        (
            f"{authority.attempt_id}\0{authority.owner_id}\0"
            f"{authority.fencing_token}\0{command.outcome.disposition.value}"
        ).encode()
    ).hexdigest()
    return f"run-event-{digest}"


__all__ = ["FencedRunCompletion", "FencedRunCompletionCommand"]
