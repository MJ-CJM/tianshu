"""Restart and replay coverage for governed-apply projection authority."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from tests.services.test_workspace_governed_apply import _prepared, _principal
from tianshu.application.outbox import OutboxDispatcher
from tianshu.bus.event_bus import EventBus
from tianshu.executor.git_backend import GitBackend
from tianshu.executor.workspace_service import WorkspaceApplyError, WorkspaceService
from tianshu.governance.decision_service import DecisionConflict, DecisionService
from tianshu.models.decision import (
    DecisionKind,
    DecisionStatus,
    ResolveDecisionCommand,
)
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository

_CONSUMER = "workspace_service.governed_apply_projection.v1"


def _decision_counts(storage) -> tuple[int, int, int]:
    with storage._lock:  # noqa: SLF001 - durable restart assertions
        return tuple(
            int(storage._conn.execute(statement).fetchone()[0])  # noqa: SLF001
            for statement in (
                "SELECT COUNT(*) FROM decision_requests WHERE kind='governed_apply'",
                "SELECT COUNT(*) FROM decision_resolutions",
                "SELECT COUNT(*) FROM outbox_events WHERE event_type='decision.resolved'",
            )
        )


def _projection_dispatcher(storage, service: WorkspaceService) -> OutboxDispatcher:
    bus = EventBus()
    bus.on(
        "decision.resolved",
        service.handle_decision_resolved,
        consumer_name=_CONSUMER,
    )
    return OutboxDispatcher(
        OutboxRepository(storage.unit_of_work),
        bus,
        owner_id="governed-apply-restart-test",
    )


@pytest.mark.asyncio
async def test_request_commit_then_process_exit_leaves_pending_without_projection_and_reuses_id(
    storage,
    tmp_path,
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_text("approved\n", encoding="utf-8")

    def stop_after_request(stage: str) -> None:
        if stage == "after_request":
            raise RuntimeError("simulated process exit after request")

    prepared.service._governed_apply_failure_injector = stop_after_request  # noqa: SLF001
    with pytest.raises(RuntimeError, match="after request"):
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal(),
            "reviewed exact change set",
            timedelta(minutes=5),
        )

    [pending] = prepared.service._decision_service.list_pending(  # noqa: SLF001
        kind=DecisionKind.GOVERNED_APPLY
    )
    assert storage.get_apply_decision(pending.decision_request_id) is None
    assert _decision_counts(storage) == (1, 0, 0)

    restarted = WorkspaceService(
        storage,
        GitBackend(),
        tmp_path / "leases",
        DecisionService(storage),
    )
    projected, token = await restarted.issue_apply_decision(
        prepared.run_id,
        _principal(),
        "reviewed exact change set",
        timedelta(minutes=5),
    )

    assert projected.id == token == pending.decision_request_id
    assert projected.decision_request_id == pending.decision_request_id
    assert _decision_counts(storage) == (1, 1, 1)


@pytest.mark.asyncio
async def test_request_payload_drift_after_restart_is_safe_workspace_conflict(
    storage,
    tmp_path,
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_text("approved\n", encoding="utf-8")

    def stop_after_request(stage: str) -> None:
        if stage == "after_request":
            raise RuntimeError("simulated request-only commit")

    prepared.service._governed_apply_failure_injector = stop_after_request  # noqa: SLF001
    with pytest.raises(RuntimeError, match="request-only"):
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal(),
            "first review payload",
            timedelta(minutes=5),
        )
    prepared.service._governed_apply_failure_injector = None  # noqa: SLF001

    with pytest.raises(WorkspaceApplyError) as caught:
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal(),
            "drifted review payload",
            timedelta(minutes=5),
        )

    assert caught.value.code == "binding_mismatch"
    assert _decision_counts(storage) == (1, 0, 0)


@pytest.mark.asyncio
async def test_resolve_commit_then_process_exit_is_projected_by_restarted_dispatcher(
    storage,
    tmp_path,
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_text("approved\n", encoding="utf-8")

    def stop_before_projection(stage: str) -> None:
        if stage == "after_resolve_before_projection":
            raise RuntimeError("simulated process exit before projection")

    prepared.service._governed_apply_failure_injector = stop_before_projection  # noqa: SLF001
    with pytest.raises(RuntimeError, match="before projection"):
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal(),
            "reviewed exact change set",
            timedelta(minutes=5),
        )

    record_id = storage._conn.execute(  # noqa: SLF001 - durable restart assertion
        "SELECT decision_request_id FROM decision_requests WHERE kind='governed_apply'"
    ).fetchone()[0]
    record = prepared.service._decision_service.get(record_id)  # noqa: SLF001
    assert record is not None and record.request.status is DecisionStatus.RESOLVED
    assert storage.get_apply_decision(record_id) is None
    assert _decision_counts(storage) == (1, 1, 1)

    restarted = WorkspaceService(
        storage,
        GitBackend(),
        tmp_path / "leases",
        DecisionService(storage),
    )
    dispatcher = _projection_dispatcher(storage, restarted)

    assert await dispatcher.drain_once() == 1
    projection = storage.get_apply_decision(record_id)
    assert projection is not None
    assert projection.id == projection.decision_request_id == record_id
    assert await dispatcher.drain_once() == 0


@pytest.mark.asyncio
async def test_consumed_projection_replay_is_noop_and_does_not_apply_again(
    storage,
    tmp_path,
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_text("approved\n", encoding="utf-8")
    decision, token = await prepared.service.issue_apply_decision(
        prepared.run_id,
        _principal(),
        "reviewed exact change set",
        timedelta(minutes=5),
    )
    receipt = await prepared.service.apply(
        prepared.run_id,
        decision.id,
        token,
        _principal(),
    )
    source_after = (prepared.source / "modify.txt").read_bytes()
    dispatcher = _projection_dispatcher(storage, prepared.service)

    assert await dispatcher.drain_once() == 1
    replayed = storage.get_apply_decision(decision.id)
    assert replayed is not None and replayed.state == "consumed"
    assert storage.get_apply_receipt_for_decision(decision.id) == receipt
    assert (prepared.source / "modify.txt").read_bytes() == source_after
    assert await dispatcher.drain_once() == 0


@pytest.mark.asyncio
async def test_non_requester_resolution_winner_owns_restart_projection_and_apply(
    storage,
    tmp_path,
) -> None:
    prepared = await _prepared(storage, tmp_path)
    (prepared.staging / "modify.txt").write_text("winner\n", encoding="utf-8")

    def stop_after_request(stage: str) -> None:
        if stage == "after_request":
            raise RuntimeError("pause for independent resolvers")

    prepared.service._governed_apply_failure_injector = stop_after_request  # noqa: SLF001
    with pytest.raises(RuntimeError, match="independent resolvers"):
        await prepared.service.issue_apply_decision(
            prepared.run_id,
            _principal("requester"),
            "independent review",
            timedelta(minutes=5),
        )
    [pending] = prepared.service._decision_service.list_pending(  # noqa: SLF001
        kind=DecisionKind.GOVERNED_APPLY
    )
    command = ResolveDecisionCommand(
        action="approve",
        reason="independent review",
        payload={"schema_version": 1},
        expected_version=1,
    )
    candidates = (_principal("winner-a"), _principal("winner-b"))
    barrier = Barrier(2)

    def resolve(candidate):
        connection = Storage(storage._db_path)  # noqa: SLF001 - separate restart connection
        connection.init_db()
        try:
            barrier.wait()
            return DecisionService(connection).resolve(
                pending.decision_request_id,
                command,
                auth=candidate,
            )
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(resolve, candidate) for candidate in candidates]
        results = [future.exception() or future.result() for future in futures]

    resolutions = [item for item in results if not isinstance(item, BaseException)]
    conflicts = [item for item in results if isinstance(item, DecisionConflict)]
    assert len(resolutions) == len(conflicts) == 1
    winner_id = resolutions[0].actor_principal_id
    winner = next(item for item in candidates if item.principal.id == winner_id)
    loser = next(item for item in candidates if item.principal.id != winner_id)

    restarted = WorkspaceService(
        storage,
        GitBackend(),
        tmp_path / "leases",
        DecisionService(storage),
    )
    dispatcher = _projection_dispatcher(storage, restarted)
    assert await dispatcher.drain_once() == 1
    projection = storage.get_apply_decision(pending.decision_request_id)
    assert projection is not None

    with pytest.raises(Exception) as denied:
        await restarted.apply(
            prepared.run_id,
            projection.id,
            projection.id,
            loser,
        )
    assert getattr(denied.value, "code", None) == "binding_mismatch"
    receipt = await restarted.apply(
        prepared.run_id,
        projection.id,
        projection.id,
        winner,
    )
    assert receipt.outcome == "succeeded"
    assert (prepared.source / "modify.txt").read_text(encoding="utf-8") == "winner\n"
