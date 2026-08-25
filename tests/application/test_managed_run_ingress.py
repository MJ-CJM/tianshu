"""One durable application ingress owns root creation and attempt wake-up."""

from __future__ import annotations

import importlib
import inspect
from datetime import UTC, datetime

import pytest

from tianshu.bootstrap.wiring_scheduler import wire_scheduling
from tianshu.models import DAGExecution, DAGNode, DAGNodeStatus, Edict, Memorial, TaskStatus
from tianshu.models.attempt import AttemptDisposition, AttemptOutcomeV1
from tianshu.models.events import EventEnvelope
from tianshu.models.run_assignment import LegacyRunAssignmentV1
from tianshu.universe.router import ChallengerRouter

_NOW = datetime(2026, 7, 16, 13, tzinfo=UTC)


class _Reconciler:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile_once(self) -> int:
        self.calls += 1
        return 1


class _UnexpectedGenerationController:
    def __init__(self) -> None:
        self.calls = 0
        self.releases: list[str] = []

    def resolve_for_binding_current(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise AssertionError("historical replay must not resolve a current generation")

    def release_binding(self, attempt_id: str) -> bool:
        self.releases.append(attempt_id)
        return True


def _boundary_types():  # type: ignore[no-untyped-def]
    try:
        module = importlib.import_module("tianshu.application.managed_run_ingress")
    except ModuleNotFoundError:
        pytest.fail("managed run ingress is missing", pytrace=False)
    return module.ManagedRunCommand, module.ManagedRunIngress


async def test_same_request_atomically_reuses_one_root_and_attempt(storage) -> None:
    ManagedRunCommand, ManagedRunIngress = _boundary_types()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    reconciler = _Reconciler()
    ingress = ManagedRunIngress(storage, reconciler, clock=lambda: _NOW)
    command = ManagedRunCommand(
        edict_id="edict-1",
        idempotency_key="api:follow-up-1",
        instruction="continue",
        event_type="followup.submitted",
        event_payload={"instruction": "continue"},
    )

    first = await ingress.start(command)
    replay = await ingress.start(command)

    assert replay.memorial == first.memorial
    assert replay.attempt_id == first.attempt_id
    assert first.deduplicated is False
    assert replay.deduplicated is True
    assert reconciler.calls == 2
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM execution_attempts WHERE memorial_id=?",
            (first.memorial.id,),
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE memorial_id=?",
            (first.memorial.id,),
        ).fetchone()[0]
        == 1
    )


async def test_same_request_with_different_envelope_fails_closed(storage) -> None:
    ManagedRunCommand, ManagedRunIngress = _boundary_types()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    ingress = ManagedRunIngress(storage, _Reconciler(), clock=lambda: _NOW)
    await ingress.start(
        ManagedRunCommand(
            edict_id="edict-1",
            idempotency_key="api:follow-up-1",
            instruction="first",
            event_type="followup.submitted",
            event_payload={"instruction": "first"},
        )
    )

    with pytest.raises(RuntimeError, match="conflicts"):
        await ingress.start(
            ManagedRunCommand(
                edict_id="edict-1",
                idempotency_key="api:follow-up-1",
                instruction="different",
                event_type="followup.submitted",
                event_payload={"instruction": "different"},
            )
        )


async def test_existing_root_adoption_reuses_attempt_and_wakes(storage) -> None:
    ManagedRunCommand, ManagedRunIngress = _boundary_types()
    del ManagedRunCommand
    storage.save_edict(Edict(id="edict-1", goal="work"))
    from tianshu.models import Memorial

    storage.save_memorial(Memorial(id="root-1", edict_id="edict-1"))
    reconciler = _Reconciler()
    ingress = ManagedRunIngress(storage, reconciler, clock=lambda: _NOW)

    first = await ingress.adopt_existing(
        memorial_id="root-1",
        idempotency_key="legacy:event-1",
        available_at=_NOW,
    )
    replay = await ingress.adopt_existing(
        memorial_id="root-1",
        idempotency_key="legacy:event-1",
        available_at=_NOW,
    )

    assert replay.attempt_id == first.attempt_id
    assert replay.deduplicated is True
    assert reconciler.calls == 2


async def test_legacy_consumer_delegates_event_owned_adoption_to_ingress(storage) -> None:
    _ManagedRunCommand, ManagedRunIngress = _boundary_types()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_memorial(Memorial(id="root-1", edict_id="edict-1"))
    ingress = ManagedRunIngress(storage, _Reconciler(), clock=lambda: _NOW)
    event = EventEnvelope(
        event_id="legacy-scheduled-1",
        event_type="edict.scheduled",
        edict_id="edict-1",
        memorial_id="root-1",
        timestamp=_NOW,
        producer="legacy",
        payload={},
    )

    first = await ingress.adopt_legacy(event)
    replay = await ingress.adopt_legacy(event)

    assert first.attempt_id == replay.attempt_id
    assert replay.deduplicated is True
    source = inspect.getsource(wire_scheduling)
    assert "attempt_repo.enqueue_initial" not in source
    assert "managed_run_ingress.adopt_legacy" in source


async def test_terminal_legacy_replay_requires_exact_canonical_envelope(storage) -> None:
    _ManagedRunCommand, ManagedRunIngress = _boundary_types()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_memorial(Memorial(id="root-1", edict_id="edict-1"))
    ingress = ManagedRunIngress(storage, _Reconciler(), clock=lambda: _NOW)
    event = EventEnvelope(
        event_id="legacy-scheduled-1",
        event_type="edict.scheduled",
        edict_id="edict-1",
        memorial_id="root-1",
        timestamp=_NOW,
        producer="legacy",
        payload={"goal": "work"},
    )
    first = await ingress.adopt_legacy(event)
    claimed = storage.attempt_repo.claim(
        memorial_id="root-1",
        owner_id="worker-1",
        now=_NOW,
        lease_seconds=30,
    )
    assert claimed is not None
    assert storage.attempt_repo.complete(
        attempt_id=claimed.attempt_id,
        owner_id="worker-1",
        fencing_token=claimed.fencing_token,
        outcome=AttemptOutcomeV1(
            disposition=AttemptDisposition.SUCCEEDED,
            completed_at=_NOW,
        ),
    )
    root = storage.get_memorial("root-1")
    assert root is not None
    root.status = TaskStatus.COMPLETED
    root.completed_at = _NOW
    storage.update_memorial(root)

    with storage.unit_of_work() as unit_of_work:
        unit_of_work.connection.execute(
            "DELETE FROM run_generation_bindings WHERE memorial_id=? AND attempt_id=?",
            ("root-1", first.attempt_id),
        )
        unit_of_work.commit()
    controller = _UnexpectedGenerationController()
    ingress = ManagedRunIngress(
        storage,
        _Reconciler(),
        clock=lambda: _NOW,
        challenger_router=ChallengerRouter(
            storage,
            generation_controller=lambda: controller,
        ),
    )

    replay = await ingress.adopt_legacy(event)

    assert replay.attempt_id == first.attempt_id
    assert replay.memorial.status is TaskStatus.COMPLETED
    assert replay.deduplicated is True
    assert controller.calls == 0
    assert controller.releases == []
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM run_generation_bindings WHERE memorial_id=? AND attempt_id=?",
            ("root-1", first.attempt_id),
        ).fetchone()[0]
        == 0
    )
    with pytest.raises(RuntimeError, match="conflict"):
        await ingress.adopt_legacy(event.model_copy(update={"event_id": "legacy-scheduled-2"}))
    with pytest.raises(RuntimeError, match="conflict"):
        await ingress.adopt_legacy(event.model_copy(update={"payload": {"goal": "forged"}}))


async def test_follow_up_exact_replay_precedes_busy_check_and_parent_selection(storage) -> None:
    ManagedRunCommand, ManagedRunIngress = _boundary_types()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    previous = Memorial(
        id="root-previous",
        edict_id="edict-1",
        status=TaskStatus.COMPLETED,
        completed_at=_NOW,
    )
    storage.save_memorial(previous)
    router = ChallengerRouter(storage)
    parent_assignment = router.assign(previous.id)
    ingress = ManagedRunIngress(
        storage,
        _Reconciler(),
        clock=lambda: _NOW,
        challenger_router=router,
    )
    command = ManagedRunCommand(
        edict_id="edict-1",
        idempotency_key="api:follow-up-1",
        instruction="continue",
        event_type="followup.submitted",
        event_payload={"instruction": "continue"},
    )

    first = await ingress.start(command)
    replay = await ingress.start(command)

    assert replay.memorial.id == first.memorial.id
    assert replay.memorial.parent_memorial_id == previous.id
    child_assignment = router.get(first.memorial.id)
    assert child_assignment is not None
    assert child_assignment.assignment_id != parent_assignment.assignment_id
    assert child_assignment.mode == parent_assignment.mode
    with pytest.raises(RuntimeError, match="active|busy"):
        await ingress.start(
            ManagedRunCommand(
                edict_id="edict-1",
                idempotency_key="api:follow-up-2",
                instruction="different",
                event_type="followup.submitted",
                event_payload={"instruction": "different"},
            )
        )


async def test_follow_up_from_historical_parent_without_assignment_stays_legacy(storage) -> None:
    ManagedRunCommand, ManagedRunIngress = _boundary_types()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_memorial(
        Memorial(
            id="root-without-assignment",
            edict_id="edict-1",
            status=TaskStatus.COMPLETED,
            completed_at=_NOW,
        )
    )
    reconciler = _Reconciler()
    router = ChallengerRouter(storage)
    ingress = ManagedRunIngress(
        storage,
        reconciler,
        clock=lambda: _NOW,
        challenger_router=router,
    )

    result = await ingress.start(
        ManagedRunCommand(
            edict_id="edict-1",
            idempotency_key="api:missing-parent-assignment",
            instruction="continue",
            event_type="followup.submitted",
            event_payload={"instruction": "continue"},
        )
    )

    assert result.memorial.parent_memorial_id == "root-without-assignment"
    assignment = router.get(result.memorial.id)
    assert isinstance(assignment, LegacyRunAssignmentV1)
    assert router.get("root-without-assignment") is None
    assert reconciler.calls == 1
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM execution_attempts WHERE memorial_id=?",
            (result.memorial.id,),
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize("boundary", ["after_root", "after_dag", "after_attempt", "after_outbox"])
async def test_dag_retry_is_atomic_and_exact_replay_is_stable(
    storage,
    boundary: str,
) -> None:
    _ManagedRunCommand, ManagedRunIngress = _boundary_types()
    storage.save_edict(Edict(id="edict-1", goal="work"))
    storage.save_memorial(
        Memorial(
            id="root-1",
            edict_id="edict-1",
            status=TaskStatus.FAILED,
            completed_at=_NOW,
        )
    )
    storage.save_dag_execution(
        DAGExecution(
            id="dag-1",
            edict_id="edict-1",
            status="failed",
            root_memorial_id="root-1",
            completed_at=_NOW,
            nodes=[
                DAGNode(
                    node_id="one",
                    description="work",
                    status=DAGNodeStatus.FAILED,
                    error="transient",
                )
            ],
        )
    )
    router = ChallengerRouter(storage)
    parent_assignment = router.assign("root-1")
    before = (
        storage._conn.execute("SELECT COUNT(*) FROM memorials").fetchone()[0],  # noqa: SLF001
        storage._conn.execute("SELECT status, root_memorial_id FROM dag_executions").fetchone(),  # noqa: SLF001
        storage._conn.execute("SELECT status FROM dag_nodes").fetchone()[0],  # noqa: SLF001
        storage._conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0],  # noqa: SLF001
    )

    def fail_at(observed: str) -> None:
        if observed == boundary:
            raise RuntimeError("injected DAG retry failure")

    failing = ManagedRunIngress(
        storage,
        _Reconciler(),
        clock=lambda: _NOW,
        boundary_hook=fail_at,
        challenger_router=router,
    )
    with pytest.raises(RuntimeError, match="injected DAG retry failure"):
        await failing.retry_dag(
            dag_id="dag-1",
            idempotency_key="request-1",
            from_node_ids=["one"],
        )
    after = (
        storage._conn.execute("SELECT COUNT(*) FROM memorials").fetchone()[0],  # noqa: SLF001
        storage._conn.execute("SELECT status, root_memorial_id FROM dag_executions").fetchone(),  # noqa: SLF001
        storage._conn.execute("SELECT status FROM dag_nodes").fetchone()[0],  # noqa: SLF001
        storage._conn.execute("SELECT COUNT(*) FROM execution_attempts").fetchone()[0],  # noqa: SLF001
    )
    assert tuple(before[1]) == tuple(after[1])
    assert before[0] == after[0]
    assert before[2:] == after[2:]

    ingress = ManagedRunIngress(
        storage,
        _Reconciler(),
        clock=lambda: _NOW,
        challenger_router=router,
    )
    first = await ingress.retry_dag(
        dag_id="dag-1",
        idempotency_key="request-1",
        from_node_ids=["one"],
    )
    replay = await ingress.retry_dag(
        dag_id="dag-1",
        idempotency_key="request-1",
        from_node_ids=["one"],
    )
    assert replay == first
    retry_assignment = router.get(first.memorial.id)
    assert retry_assignment is not None
    assert retry_assignment.assignment_id != parent_assignment.assignment_id
    assert retry_assignment.mode == parent_assignment.mode
