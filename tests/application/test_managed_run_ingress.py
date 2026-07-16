"""One durable application ingress owns root creation and attempt wake-up."""

from __future__ import annotations

import importlib
import inspect
from datetime import UTC, datetime

import pytest

from tianshu.bootstrap.wiring_scheduler import wire_scheduling
from tianshu.models import DAGExecution, DAGNode, DAGNodeStatus, Edict, Memorial, TaskStatus
from tianshu.models.events import EventEnvelope

_NOW = datetime(2026, 7, 16, 13, tzinfo=UTC)


class _Reconciler:
    def __init__(self) -> None:
        self.calls = 0

    async def reconcile_once(self) -> int:
        self.calls += 1
        return 1


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
    ingress = ManagedRunIngress(storage, _Reconciler(), clock=lambda: _NOW)
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

    ingress = ManagedRunIngress(storage, _Reconciler(), clock=lambda: _NOW)
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
