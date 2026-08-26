"""Restart and fault-matrix evidence for governed Evolution rollback."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event

import pytest
from tests.diagnostics.test_doctor_report import _inputs
from tests.evolution.test_promotion_fail_closed import (
    _GateAuthority,
    _green,
    _ready,
    _service,
    _skill_adapter_case,
    _start_command,
)

import tianshu.evolution.promotion as promotion_module
from tianshu.application.run_reconciler import RunReconciler, RunReconcilerState
from tianshu.diagnostics import assess_readiness
from tianshu.evolution.adapters.base import (
    ActivationReceiptV1,
    AdapterError,
)
from tianshu.evolution.adapters.base import (
    RollbackReceiptV1 as AdapterRollbackReceiptV1,
)
from tianshu.evolution.promotion import (
    PromotionConflict,
    PromotionService,
    RollbackCommand,
    SkillPromotionAdapter,
)
from tianshu.evolution.reconciler import EvolutionRollbackReconciler
from tianshu.models.canonical import canonical_json_bytes
from tianshu.models.evolution_candidate import (
    CandidateLifecycle,
)
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.run_assignment import LegacyRunAssignmentV1
from tianshu.storage.evolution_repo import EvolutionRepository
from tianshu.universe.router import ChallengerRouter


def _auth() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="principal-1",
            kind=PrincipalKind.HUMAN,
            display_name="Operator",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.API,
        correlation_id="rollback-fault-matrix",
    )


def _pending(storage):
    candidate = _ready(storage)
    service, _gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=_auth())
    command = RollbackCommand(
        expected_version=canary.candidate_version,
        idempotency_key="rollback-reconcile-v1",
        reason="measured canary regression",
    )
    adapter.fail_rollback = True
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        service.rollback(candidate.candidate_id, command, auth=_auth())
    adapter.fail_rollback = False
    adapter.rollback_is_idempotent = True
    return candidate, service, adapter, command


def _real_skill_rollback_case(storage, tmp_path):
    artifacts, live_root, skill_root, envelope, base_text, changed_text = _skill_adapter_case(
        storage, tmp_path
    )
    proposed = envelope.model_copy(update={"lifecycle": CandidateLifecycle.PROPOSED})
    repository = EvolutionRepository()
    with storage.unit_of_work() as unit_of_work:
        current = repository.insert_candidate(unit_of_work.connection, proposed)
        for lifecycle in (
            CandidateLifecycle.STAGED,
            CandidateLifecycle.EVALUATING,
            CandidateLifecycle.READY,
        ):
            current = repository.save_candidate(
                unit_of_work.connection,
                current.model_copy(update={"lifecycle": lifecycle}),
                expected_version=current.version,
            )
        unit_of_work.commit()
    adapter = SkillPromotionAdapter(artifacts, live_root=live_root)
    service = PromotionService(
        storage,
        _GateAuthority(_green(current)),
        adapter_resolver=lambda _kind: adapter,
        clock=lambda: current.updated_at + timedelta(minutes=1),
    )
    canary = service.start_canary(current.candidate_id, _start_command(current), auth=_auth())
    with storage.unit_of_work() as unit_of_work:
        canary_candidate = repository.get_candidate(unit_of_work.connection, current.candidate_id)
        unit_of_work.commit()
    assert canary_candidate is not None
    adapter.activate(canary_candidate)
    assert (skill_root / "SKILL.md").read_text(encoding="utf-8") == changed_text
    command = RollbackCommand(
        expected_version=canary.candidate_version,
        idempotency_key="golden-rollback-after-effect-crash",
        reason="restore base after regression",
    )
    return (
        live_root,
        skill_root / "SKILL.md",
        base_text,
        changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    )


def test_reconciler_restarts_from_pending_without_reopening_new_challenger_traffic(storage) -> None:
    candidate, service, adapter, _command = _pending(storage)
    router = ChallengerRouter(
        storage,
        allocation_secret=b"rollback-secret",
        payload_resolver=lambda *_args: {"selected": "verified"},
    )

    # The first durable rollback transaction has already committed. New assignments
    # are legacy/champion-only; old durable assignments remain governed and frozen.
    storage._conn.execute(
        "INSERT INTO edicts (id, goal, created_at) VALUES ('rollback-edict', 'route', ?)",
        (datetime(2026, 7, 18, 11, tzinfo=UTC).isoformat(),),
    )
    storage._conn.execute(
        """INSERT INTO memorials (id, edict_id, status, created_at)
           VALUES ('new-after-zero', 'rollback-edict', 'submitted', ?)""",
        (datetime(2026, 7, 18, 11, tzinfo=UTC).isoformat(),),
    )
    storage._conn.commit()
    new_assignment = router.assign("new-after-zero")
    assert isinstance(new_assignment, LegacyRunAssignmentV1)
    assert new_assignment.memorial_id == "new-after-zero"
    assert service.has_pending_rollbacks() is True

    restarted = EvolutionRollbackReconciler(service)
    assert restarted.readiness_probe() is False
    assert restarted.reconcile_once() == 1
    assert restarted.reconcile_once() == 0
    assert restarted.readiness_probe() is True
    assert adapter.rollback_calls == 2  # one failed attempt, one successful restore

    with storage.unit_of_work() as unit_of_work:
        durable = EvolutionRepository().get_candidate(
            unit_of_work.connection, candidate.candidate_id
        )
        counts = tuple(
            unit_of_work.connection.execute(f"SELECT COUNT(*) FROM {table} WHERE 1=1").fetchone()[0]
            for table in ("evolution_promotion_journal", "outbox_events", "system_audit_events")
        )
        unit_of_work.commit()
    assert durable is not None and durable.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert restarted.reconcile_once() == 0
    with storage.unit_of_work() as unit_of_work:
        repeated_counts = tuple(
            unit_of_work.connection.execute(f"SELECT COUNT(*) FROM {table} WHERE 1=1").fetchone()[0]
            for table in ("evolution_promotion_journal", "outbox_events", "system_audit_events")
        )
        unit_of_work.commit()
    assert repeated_counts == counts


def test_failure_before_allocation_zero_commit_preserves_canary_and_skips_restore(
    storage, monkeypatch
) -> None:
    candidate = _ready(storage)
    service, _gates, adapter = _service(storage, candidate)
    canary = service.start_canary(candidate.candidate_id, _start_command(candidate), auth=_auth())
    command = RollbackCommand(
        expected_version=canary.candidate_version,
        idempotency_key="rollback-before-zero-commit",
        reason="inject first transaction failure",
    )

    monkeypatch.setattr(
        service,
        "_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("commit injection")),
    )
    with pytest.raises(RuntimeError, match="commit injection"):
        service.rollback(candidate.candidate_id, command, auth=_auth())

    with storage.unit_of_work() as unit_of_work:
        current = EvolutionRepository().get_candidate(
            unit_of_work.connection, candidate.candidate_id
        )
        allocation = unit_of_work.connection.execute(
            """SELECT allocation_basis_points FROM evolution_routing_allocations
               WHERE candidate_id=?""",
            (candidate.candidate_id,),
        ).fetchone()[0]
        rollback_rows = unit_of_work.connection.execute(
            "SELECT COUNT(*) FROM evolution_promotion_journal WHERE action='rollback'"
        ).fetchone()[0]
        unit_of_work.commit()
    assert current is not None and current.lifecycle is CandidateLifecycle.CANARY
    assert allocation == 250
    assert rollback_rows == 0
    assert adapter.rollback_calls == 0


def test_real_skill_restore_crash_before_applied_receipt_recovers_by_live_verification(
    storage, tmp_path, monkeypatch
) -> None:
    (
        live_root,
        live_skill,
        base_text,
        changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)

    exchange_calls = 0
    original_exchange = promotion_module._atomic_exchange

    def counted_exchange(source, target):
        nonlocal exchange_calls
        exchange_calls += 1
        return original_exchange(source, target)

    monkeypatch.setattr(promotion_module, "_atomic_exchange", counted_exchange)
    original_append = service._append_journal

    def fail_applied(*args, entry, **kwargs):
        if entry.action == "rollback" and entry.status == "applied":
            raise RuntimeError("crash before applied receipt")
        return original_append(*args, entry=entry, **kwargs)

    monkeypatch.setattr(service, "_append_journal", fail_applied)
    with pytest.raises(RuntimeError, match="crash before applied receipt"):
        service.rollback(current.candidate_id, command, auth=_auth())
    assert exchange_calls == 1
    assert live_skill.read_text(encoding="utf-8") == base_text
    stage = (
        live_root
        / f".promotion-stage-{live_skill.parent.name}-{canary_candidate.candidate.artifact_digest}"
    )
    stage.mkdir()
    (stage / "SKILL.md").write_text(changed_text, encoding="utf-8")

    monkeypatch.setattr(service, "_append_journal", original_append)
    assert EvolutionRollbackReconciler(service).reconcile_once() == 1
    assert exchange_calls == 1
    assert not stage.exists()
    assert live_skill.read_text(encoding="utf-8") == base_text


def test_skill_rollback_blocks_competing_activation_until_applied_and_final_commit(
    storage, tmp_path, monkeypatch
) -> None:
    (
        _live_root,
        live_skill,
        base_text,
        _changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    applied_reached = Event()
    allow_applied = Event()
    activation_started = Event()
    activation_done = Event()
    original_append = service._append_journal

    def pause_before_applied_commit(*args, entry, **kwargs):
        if entry.action == "rollback" and entry.status == "applied":
            applied_reached.set()
            if not allow_applied.wait(timeout=5):
                raise RuntimeError("test did not release applied commit")
        return original_append(*args, entry=entry, **kwargs)

    def compete_activate():
        activation_started.set()
        try:
            return adapter.activate(canary_candidate)
        finally:
            activation_done.set()

    monkeypatch.setattr(service, "_append_journal", pause_before_applied_commit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rollback_future = pool.submit(service.rollback, current.candidate_id, command, auth=_auth())
        assert applied_reached.wait(timeout=5)
        activation_future = pool.submit(compete_activate)
        assert activation_started.wait(timeout=5)
        activation_escaped_before_commit = activation_done.wait(timeout=0.2)
        allow_applied.set()
        receipt = rollback_future.result(timeout=5)
        try:
            activation_future.result(timeout=5)
        except AdapterError as exc:
            activation_error: AdapterError | None = exc
        else:
            activation_error = None

    assert activation_escaped_before_commit is False
    assert activation_error is not None
    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert live_skill.read_text(encoding="utf-8") == base_text


def test_skill_applied_rollback_blocks_competing_activation_until_completed_commit(
    storage, tmp_path, monkeypatch
) -> None:
    (
        _live_root,
        live_skill,
        base_text,
        _changed_text,
        current,
        canary_candidate,
        adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    original_record = service._record

    def fail_first_final(*args, pending=False, **kwargs):
        if not pending:
            raise RuntimeError("crash after applied before completed")
        return original_record(*args, pending=pending, **kwargs)

    monkeypatch.setattr(service, "_record", fail_first_final)
    with pytest.raises(RuntimeError, match="crash after applied before completed"):
        service.rollback(current.candidate_id, command, auth=_auth())
    assert live_skill.read_text(encoding="utf-8") == base_text
    with storage.unit_of_work() as unit_of_work:
        applied_count = unit_of_work.connection.execute(
            """SELECT COUNT(*) FROM evolution_promotion_journal
               WHERE candidate_id=? AND action='rollback' AND status='applied'""",
            (current.candidate_id,),
        ).fetchone()[0]
        pending = EvolutionRepository().get_candidate(unit_of_work.connection, current.candidate_id)
        unit_of_work.commit()
    assert applied_count == 1
    assert pending is not None and pending.lifecycle is CandidateLifecycle.ROLLBACK_PENDING

    final_reached = Event()
    allow_final = Event()
    activation_started = Event()
    activation_done = Event()

    def pause_before_final_commit(*args, pending=False, **kwargs):
        if not pending:
            final_reached.set()
            if not allow_final.wait(timeout=5):
                raise RuntimeError("test did not release final commit")
        return original_record(*args, pending=pending, **kwargs)

    def compete_activate():
        activation_started.set()
        try:
            return adapter.activate(canary_candidate)
        finally:
            activation_done.set()

    monkeypatch.setattr(service, "_record", pause_before_final_commit)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rollback_future = pool.submit(service.rollback, current.candidate_id, command, auth=_auth())
        assert final_reached.wait(timeout=5)
        activation_future = pool.submit(compete_activate)
        assert activation_started.wait(timeout=5)
        activation_escaped_before_commit = activation_done.wait(timeout=0.2)
        allow_final.set()
        receipt = rollback_future.result(timeout=5)
        try:
            activation_future.result(timeout=5)
        except AdapterError as exc:
            activation_error: AdapterError | None = exc
        else:
            activation_error = None

    assert activation_escaped_before_commit is False
    assert activation_error is not None
    assert receipt.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert live_skill.read_text(encoding="utf-8") == base_text


def test_skill_applied_retry_with_preexisting_wrong_live_stays_pending(
    storage, tmp_path, monkeypatch
) -> None:
    (
        _live_root,
        live_skill,
        base_text,
        changed_text,
        current,
        _canary_candidate,
        _adapter,
        service,
        command,
    ) = _real_skill_rollback_case(storage, tmp_path)
    original_record = service._record

    def fail_final(*args, pending=False, **kwargs):
        if not pending:
            raise RuntimeError("crash after applied before completed")
        return original_record(*args, pending=pending, **kwargs)

    monkeypatch.setattr(service, "_record", fail_final)
    with pytest.raises(RuntimeError, match="crash after applied before completed"):
        service.rollback(current.candidate_id, command, auth=_auth())
    assert live_skill.read_text(encoding="utf-8") == base_text
    live_skill.write_text(changed_text, encoding="utf-8")
    monkeypatch.setattr(service, "_record", original_record)

    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        service.rollback(current.candidate_id, command, auth=_auth())
    with storage.unit_of_work() as unit_of_work:
        durable = EvolutionRepository().get_candidate(unit_of_work.connection, current.candidate_id)
        completed_count = unit_of_work.connection.execute(
            """SELECT COUNT(*) FROM evolution_promotion_journal
               WHERE candidate_id=? AND action='rollback' AND status='completed'""",
            (current.candidate_id,),
        ).fetchone()[0]
        unit_of_work.commit()

    assert durable is not None and durable.lifecycle is CandidateLifecycle.ROLLBACK_PENDING
    assert completed_count == 0
    assert live_skill.read_text(encoding="utf-8") == changed_text


def test_reconciler_fails_closed_for_legacy_missing_identity_and_corrupt_binding(storage) -> None:
    candidate, service, adapter, _command = _pending(storage)
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        connection.execute("DROP TRIGGER evolution_promotion_journal_no_update")
        row = connection.execute(
            """SELECT promotion_journal_id, entry_json FROM evolution_promotion_journal
               WHERE candidate_id=? AND action='rollback' AND status='rollback_pending'""",
            (candidate.candidate_id,),
        ).fetchone()
        payload = json.loads(row["entry_json"])
        payload.pop("idempotency_key", None)  # exact pre-Task-6 journal shape
        legacy = canonical_json_bytes(payload).decode()
        connection.execute(
            """UPDATE evolution_promotion_journal SET entry_json=?, entry_hash=?
               WHERE promotion_journal_id=?""",
            (legacy, sha256(legacy.encode()).hexdigest(), row["promotion_journal_id"]),
        )
        unit_of_work.commit()

    reconciler = EvolutionRollbackReconciler(service)
    assert reconciler.readiness_probe() is False
    assert reconciler.reconcile_once() == 0
    assert reconciler.last_error_code == "rollback_reconciliation_identity_missing"
    assert adapter.rollback_calls == 1
    assert service.has_pending_rollbacks() is True

    with storage.unit_of_work() as unit_of_work:
        current = EvolutionRepository().get_candidate(
            unit_of_work.connection, candidate.candidate_id
        )
        allocation = unit_of_work.connection.execute(
            """SELECT allocation_basis_points FROM evolution_routing_allocations
               WHERE candidate_id=?""",
            (candidate.candidate_id,),
        ).fetchone()[0]
        unit_of_work.commit()
    assert current is not None and current.lifecycle is CandidateLifecycle.ROLLBACK_PENDING
    assert allocation == 0


def test_reconciler_refuses_blind_replay_without_verification_or_idempotency(storage) -> None:
    _candidate_record, service, adapter, _command = _pending(storage)
    del adapter.rollback_is_idempotent

    reconciler = EvolutionRollbackReconciler(service)
    assert reconciler.reconcile_once() == 0
    assert reconciler.last_error_code == "rollback_restore_safety_unavailable"
    assert reconciler.readiness_probe() is False
    assert adapter.rollback_calls == 1


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("corrupt", "promotion_journal_conflict"),
        ("missing", "rollback_reconciliation_journal_conflict"),
        ("cross-bound", "promotion_journal_conflict"),
    ),
)
def test_reconciler_fails_closed_for_corrupt_missing_and_cross_bound_journal(
    storage, case: str, expected_code: str
) -> None:
    candidate, service, adapter, _command = _pending(storage)
    if case == "cross-bound":
        _ready(storage, candidate_id="candidate-cross-bound")
    with storage.unit_of_work() as unit_of_work:
        connection = unit_of_work.connection
        if case == "missing":
            connection.execute("DROP TRIGGER evolution_promotion_journal_no_delete")
            connection.execute(
                """DELETE FROM evolution_promotion_journal
                   WHERE candidate_id=? AND action='rollback'
                     AND status='rollback_pending'""",
                (candidate.candidate_id,),
            )
        else:
            connection.execute("DROP TRIGGER evolution_promotion_journal_no_update")
            if case == "corrupt":
                connection.execute(
                    """UPDATE evolution_promotion_journal SET entry_hash=?
                       WHERE candidate_id=? AND action='rollback'
                         AND status='rollback_pending'""",
                    ("f" * 64, candidate.candidate_id),
                )
            else:
                connection.execute(
                    """UPDATE evolution_promotion_journal SET candidate_id=?
                       WHERE candidate_id=? AND action='rollback'
                         AND status='rollback_pending'""",
                    ("candidate-cross-bound", candidate.candidate_id),
                )
        unit_of_work.commit()

    reconciler = EvolutionRollbackReconciler(service)
    assert reconciler.reconcile_once() == 0
    assert reconciler.last_error_code == expected_code
    assert reconciler.readiness_probe() is False
    assert adapter.rollback_calls == 1

    with storage.unit_of_work() as unit_of_work:
        current = EvolutionRepository().get_candidate(
            unit_of_work.connection, candidate.candidate_id
        )
        allocation = unit_of_work.connection.execute(
            """SELECT allocation_basis_points FROM evolution_routing_allocations
               WHERE candidate_id=?""",
            (candidate.candidate_id,),
        ).fetchone()[0]
        unit_of_work.commit()
    assert current is not None and current.lifecycle is CandidateLifecycle.ROLLBACK_PENDING
    assert allocation == 0


def test_readiness_degrades_for_pending_rollback_and_probe_failure() -> None:
    pending = assess_readiness(_inputs(evolution_rollback_ready=lambda: False))
    pending_check = next(check for check in pending.checks if check.id == "evolution.rollback")
    assert pending.status == "degraded"
    assert pending_check.status == "degraded"
    assert pending_check.evidence == {"ok": False, "rollback_pending": True}

    def fail_probe() -> bool:
        raise RuntimeError("sensitive probe detail")

    failed = assess_readiness(_inputs(evolution_rollback_ready=fail_probe))
    failed_check = next(check for check in failed.checks if check.id == "evolution.rollback")
    assert failed.status == "degraded"
    assert failed_check.evidence == {"ok": False, "probe_error": True}

    healthy = assess_readiness(_inputs(evolution_rollback_ready=lambda: True))
    assert healthy.status == "ready"

    disabled = assess_readiness(_inputs(evolution_routing_enabled=lambda: False))
    disabled_check = next(check for check in disabled.checks if check.id == "evolution.rollback")
    assert disabled.status == "degraded"
    assert disabled_check.evidence == {"ok": False, "routing_disabled": True}
    assert "routing" in disabled_check.remediation.lower()

    cleanup = assess_readiness(_inputs(evolution_generation_cleanup_pending=lambda: True))
    cleanup_check = next(check for check in cleanup.checks if check.id == "evolution.rollback")
    assert cleanup.status == "degraded"
    assert cleanup_check.evidence == {
        "ok": False,
        "generation_cleanup_pending": True,
    }
    assert "generation" in cleanup_check.remediation.lower()


@pytest.mark.asyncio
async def test_restore_failure_degrades_but_does_not_stop_champion_dispatch(storage) -> None:
    _candidate_record, service, adapter, _command = _pending(storage)
    adapter.fail_rollback = True
    evolution = EvolutionRollbackReconciler(service)

    class Repository:
        def list_dispatchable_memorial_ids(self, *, now, limit):
            del now, limit
            return ("champion-run",)

    class Dispatcher:
        def __init__(self) -> None:
            self.dispatched: list[str] = []

        async def dispatch(self, memorial_id: str) -> bool:
            self.dispatched.append(memorial_id)
            return True

        async def stop(self) -> None:
            return None

    dispatcher = Dispatcher()
    run_reconciler = RunReconciler(
        Repository(),  # type: ignore[arg-type]
        dispatcher,  # type: ignore[arg-type]
        before_scan=evolution.reconcile_once,
    )

    assert await run_reconciler.reconcile_once() == 1
    assert dispatcher.dispatched == ["champion-run"]
    assert run_reconciler.state is RunReconcilerState.STOPPED
    assert run_reconciler.failure_code is None
    assert evolution.last_error_code == "rollback_restore_failed"
    assert evolution.readiness_probe() is False


def test_multiple_pending_are_deterministic_and_failure_does_not_starve_healthy_limit(
    storage,
) -> None:
    first = _ready(storage, candidate_id="candidate-a")
    first_service, first_gates, first_adapter = _service(storage, first)
    first_canary = first_service.start_canary(
        first.candidate_id,
        _start_command(first, key="start-a"),
        auth=_auth(),
    )
    first_adapter.fail_rollback = True
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        first_service.rollback(
            first.candidate_id,
            RollbackCommand(
                expected_version=first_canary.candidate_version,
                idempotency_key="rollback-a",
                reason="candidate a regression",
            ),
            auth=_auth(),
        )

    second = _ready(storage, candidate_id="candidate-b")
    second_service, _second_gates, second_adapter = _service(storage, second)
    second_canary = second_service.start_canary(
        second.candidate_id,
        _start_command(second, key="start-b"),
        auth=_auth(),
    )
    second_adapter.fail_rollback = True
    with pytest.raises(PromotionConflict, match="rollback_restore_failed"):
        second_service.rollback(
            second.candidate_id,
            RollbackCommand(
                expected_version=second_canary.candidate_version,
                idempotency_key="rollback-b",
                reason="candidate b regression",
            ),
            auth=_auth(),
        )

    class MixedAdapter:
        rollback_is_idempotent = True

        def __init__(self) -> None:
            self.order: list[str] = []

        def activate(self, candidate):
            return ActivationReceiptV1(
                candidate_id=candidate.candidate_id,
                artifact_digest=candidate.candidate.artifact_digest,
            )

        def rollback(self, candidate):
            self.order.append(candidate.candidate_id)
            if candidate.candidate_id == "candidate-a":
                raise RuntimeError("candidate a restore unavailable")
            return AdapterRollbackReceiptV1(
                candidate_id=candidate.candidate_id,
                artifact_digest=candidate.base.artifact_digest,
            )

    mixed = MixedAdapter()
    reconciliation_service = PromotionService(
        storage,
        first_gates,
        adapter_resolver=lambda _kind: mixed,
        clock=lambda: datetime(2026, 7, 18, 12, tzinfo=UTC),
    )
    reconciler = EvolutionRollbackReconciler(reconciliation_service, limit=1)

    assert reconciler.reconcile_once() == 1
    assert mixed.order == ["candidate-a", "candidate-b"]
    assert reconciler.last_error_code == "rollback_restore_failed"
    with storage.unit_of_work() as unit_of_work:
        first_durable = EvolutionRepository().get_candidate(unit_of_work.connection, "candidate-a")
        second_durable = EvolutionRepository().get_candidate(unit_of_work.connection, "candidate-b")
        allocations = tuple(
            row[0]
            for row in unit_of_work.connection.execute(
                """SELECT allocation_basis_points FROM evolution_routing_allocations
                   ORDER BY candidate_id"""
            ).fetchall()
        )
        unit_of_work.commit()
    assert first_durable is not None
    assert first_durable.lifecycle is CandidateLifecycle.ROLLBACK_PENDING
    assert second_durable is not None
    assert second_durable.lifecycle is CandidateLifecycle.ROLLED_BACK
    assert allocations == (0, 0)
    assert reconciler.readiness_probe() is False


@pytest.mark.asyncio
async def test_production_lifespan_exposes_reconciler_and_runs_startup_probe(
    tmp_path, monkeypatch
) -> None:
    from tianshu.app import create_app, lifespan

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TIANSHU_ARTIFACT_DIR", str(tmp_path / "artifacts"))
    app = create_app()
    async with lifespan(app):
        assert isinstance(app.state.evolution_reconciler, EvolutionRollbackReconciler)
        assert app.state.evolution_reconciler.readiness_probe() is True
        assert app.state.run_reconciler.is_ready is True
