"""One durable application ingress owns root creation and attempt wake-up."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest

from tianshu.models import Edict

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
