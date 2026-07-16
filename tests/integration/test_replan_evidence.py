"""Lean plan-revision evidence, lineage, and restart contracts."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from tianshu.application.run_dispatcher import AttemptAuthority
from tianshu.bus.event_bus import EventBus
from tianshu.executor.dag_scheduler import DAGScheduler
from tianshu.models import Edict, Memorial, Plan, PlanRevisionV1, PlanTask, TaskStatus
from tianshu.models.canonical import canonical_json_bytes, canonical_sha256
from tianshu.models.plan_revision import build_plan_revision, validate_plan_revision_lineage
from tianshu.models.run_state import AgentContinuationV1, RunPhase
from tianshu.planner.planner import Planner
from tianshu.storage import Storage
from tianshu.storage.run_state_repo import RunStateConflict

_NOW = datetime(2026, 7, 17, 3, 4, 5, tzinfo=UTC)


def _plan(description: str = "collect evidence") -> Plan:
    return Plan(
        tasks=[
            PlanTask(
                task_id="research",
                description=description,
                assigned_official="official-researcher",
            )
        ],
        priority_order=["research"],
    )


def _seed(storage: Storage) -> tuple[Edict, Memorial]:
    edict = Edict(id="edict-revision", goal="produce evidence")
    memorial = Memorial(
        id="memorial-revision",
        edict_id=edict.id,
        instruction=edict.goal,
        status=TaskStatus.PLANNING,
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    return edict, memorial


def test_plan_revision_is_strict_frozen_canonical_redacted_and_artifact_ready() -> None:
    plan_a = {
        "priority_order": ["research"],
        "tasks": [{"description": "collect evidence", "task_id": "research"}],
        "total_estimated_tokens": None,
    }
    plan_b = {
        "total_estimated_tokens": None,
        "tasks": [{"task_id": "research", "description": "collect evidence"}],
        "priority_order": ["research"],
    }
    raw_reason = "retry after sk-abcdefghijklmnopqrstuvwxyz012345 failed"

    first = build_plan_revision(
        plan_a,
        revision_id="revision-1",
        parent_revision_id=None,
        reason_code="initial_plan",
        reason_summary=raw_reason,
        created_at=_NOW,
    )
    equivalent = build_plan_revision(
        plan_b,
        revision_id="revision-equivalent",
        parent_revision_id=None,
        reason_code="initial_plan",
        reason_summary="equivalent canonical plan",
        created_at=_NOW,
    )

    expected_bytes = canonical_json_bytes(plan_a)
    expected_digest = hashlib.sha256(expected_bytes).hexdigest()
    assert first.plan_hash == equivalent.plan_hash == canonical_sha256(plan_a)
    assert first.artifact_digest == expected_digest == first.plan_hash
    assert first.reason_summary == "retry after [REDACTED API KEY] failed"
    assert first.created_at.tzinfo is UTC
    with pytest.raises(ValidationError, match="frozen"):
        first.parent_revision_id = "revision-forged"
    with pytest.raises(ValidationError, match="extra"):
        PlanRevisionV1.model_validate({**first.model_dump(mode="python"), "quality_score": 1.0})
    with pytest.raises(ValidationError, match="redacted"):
        PlanRevisionV1.model_validate(
            {
                **first.model_dump(mode="python"),
                "reason_summary": raw_reason,
            }
        )


def test_lineage_rejects_disconnected_duplicate_and_self_parent_revisions() -> None:
    first = build_plan_revision(
        _plan(),
        revision_id="revision-1",
        parent_revision_id=None,
        reason_code="initial_plan",
        reason_summary="initial plan",
        created_at=_NOW,
    )
    second = build_plan_revision(
        _plan("collect primary and secondary evidence"),
        revision_id="revision-2",
        parent_revision_id=first.revision_id,
        reason_code="missing_source",
        reason_summary="add a second source",
        created_at=_NOW + timedelta(seconds=1),
    )
    assert validate_plan_revision_lineage((first, second)) == (first, second)

    disconnected = second.model_copy(update={"parent_revision_id": "revision-other"})
    duplicate = second.model_copy(update={"revision_id": first.revision_id})
    self_parent = second.model_copy(update={"parent_revision_id": second.revision_id})
    for invalid in (disconnected, duplicate, self_parent):
        with pytest.raises(ValueError, match="lineage"):
            validate_plan_revision_lineage((first, invalid))


async def test_planner_persists_full_lineage_and_reuses_it_after_restart(
    tmp_path, config_manager
) -> None:
    database = str(tmp_path / "replan-evidence.db")
    storage = Storage(database)
    storage.init_db()
    edict, memorial = _seed(storage)
    planner = Planner(EventBus(), storage, config_manager, clock=lambda: _NOW)
    initial_plan = _plan()
    revised_plan = _plan("collect primary and secondary evidence")

    first = planner.persist_plan_revision(
        memorial_id=memorial.id,
        plan=initial_plan,
        parent_revision_id=None,
        reason_code="initial_plan",
        reason_summary="initial plan",
    )
    second = planner.persist_plan_revision(
        memorial_id=memorial.id,
        plan=revised_plan,
        parent_revision_id=first.revision_id,
        reason_code="missing_source",
        reason_summary="add a second source",
    )
    storage.close()

    reopened = Storage(database)
    reopened.init_db()
    try:
        with reopened.unit_of_work() as unit_of_work:
            state = reopened.run_state_repo.load(unit_of_work.connection, memorial.id)
            unit_of_work.commit()
        assert state is not None and isinstance(state.continuation, AgentContinuationV1)
        assert state.continuation.plan_revisions == (first, second)
        assert state.continuation.plan_revision_id == second.revision_id
        assert state.continuation.plan_hash == second.plan_hash
        assert state.continuation.plan_snapshot == revised_plan.model_dump(mode="json")

        retry_planner = Planner(EventBus(), reopened, config_manager, clock=lambda: _NOW)
        retry_planner.plan = AsyncMock(side_effect=AssertionError("must reuse durable plan"))
        result = await retry_planner.plan_attempt(
            AttemptAuthority(
                attempt_id="attempt-after-restart",
                memorial_id=memorial.id,
                owner_id="worker-restart",
                fencing_token=2,
            )
        )
        assert result.plan == revised_plan
        retry_planner.plan.assert_not_awaited()
    finally:
        reopened.close()


def test_run_state_cas_rejects_ancestor_rewrite_truncation_and_multi_append(
    storage: Storage, config_manager
) -> None:
    _edict, memorial = _seed(storage)
    planner = Planner(EventBus(), storage, config_manager, clock=lambda: _NOW)
    first = planner.persist_plan_revision(
        memorial_id=memorial.id,
        plan=_plan(),
        parent_revision_id=None,
        reason_code="initial_plan",
        reason_summary="initial plan",
    )
    planner.persist_plan_revision(
        memorial_id=memorial.id,
        plan=_plan("revised"),
        parent_revision_id=first.revision_id,
        reason_code="missing_source",
        reason_summary="revised plan",
    )
    with storage.unit_of_work() as unit_of_work:
        durable = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert durable is not None and isinstance(durable.continuation, AgentContinuationV1)
    original_lineage = durable.continuation.plan_revisions
    rewritten_ancestor = original_lineage[0].model_copy(
        update={"reason_summary": "forged ancestor"}
    )
    third = build_plan_revision(
        _plan("third"),
        revision_id="revision-3",
        parent_revision_id=original_lineage[-1].revision_id,
        reason_code="missing_source",
        reason_summary="third plan",
        created_at=_NOW + timedelta(seconds=2),
    )
    fourth = build_plan_revision(
        _plan("fourth"),
        revision_id="revision-4",
        parent_revision_id=third.revision_id,
        reason_code="missing_source",
        reason_summary="fourth plan",
        created_at=_NOW + timedelta(seconds=3),
    )
    candidates = (
        (rewritten_ancestor, *original_lineage[1:]),
        original_lineage[:1],
        (*original_lineage, third, fourth),
    )
    for lineage in candidates:
        forged_continuation = durable.continuation.model_copy(update={"plan_revisions": lineage})
        forged = durable.model_copy(
            update={
                "continuation": forged_continuation,
                "updated_at": durable.updated_at + timedelta(seconds=10),
            }
        )
        with pytest.raises(RunStateConflict, match="lineage"), storage.unit_of_work() as uow:
            storage.run_state_repo.compare_and_swap(
                uow.connection,
                forged,
                expected_version=durable.version,
            )
        with storage.unit_of_work() as unit_of_work:
            assert storage.run_state_repo.load(unit_of_work.connection, memorial.id) == durable
            unit_of_work.commit()


async def test_dag_rejects_plan_projection_that_does_not_match_current_revision(
    storage: Storage, config_manager
) -> None:
    edict, memorial = _seed(storage)
    planner = Planner(EventBus(), storage, config_manager, clock=lambda: _NOW)
    planner.persist_plan_revision(
        memorial_id=memorial.id,
        plan=_plan(),
        parent_revision_id=None,
        reason_code="initial_plan",
        reason_summary="initial plan",
    )
    scheduler = DAGScheduler(
        worker_pool=AsyncMock(),
        agent=AsyncMock(),
        storage=storage,
        event_bus=EventBus(),
    )
    matching_execution = _plan().to_dag(edict.id)
    matching_execution.root_memorial_id = memorial.id
    scheduler._require_plan_revision_binding(matching_execution)  # noqa: SLF001

    forged_execution = _plan("forged after revision persistence").to_dag(edict.id)
    forged_execution.root_memorial_id = memorial.id

    with pytest.raises(RuntimeError, match="plan revision"):
        await scheduler.run(edict, forged_execution)

    scheduler._pool.submit.assert_not_awaited()  # noqa: SLF001
    assert storage.get_memorial(memorial.id).status is TaskStatus.PLANNING
    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.PLANNING


def test_dag_activation_freezes_lineage_before_worker_dispatch(
    storage: Storage, config_manager
) -> None:
    edict, memorial = _seed(storage)
    planner = Planner(EventBus(), storage, config_manager, clock=lambda: _NOW)
    revision = planner.persist_plan_revision(
        memorial_id=memorial.id,
        plan=_plan(),
        parent_revision_id=None,
        reason_code="initial_plan",
        reason_summary="initial plan",
    )
    execution = _plan().to_dag(edict.id)
    execution.root_memorial_id = memorial.id
    scheduler = DAGScheduler(
        worker_pool=AsyncMock(),
        agent=AsyncMock(),
        storage=storage,
        event_bus=EventBus(),
    )

    scheduler._activate_plan_revision(execution)  # noqa: SLF001

    with storage.unit_of_work() as unit_of_work:
        state = storage.run_state_repo.load(unit_of_work.connection, memorial.id)
        unit_of_work.commit()
    assert state is not None and state.phase is RunPhase.EXECUTING
    assert state.continuation.plan_revisions[-1] == revision
    with pytest.raises(RuntimeError, match="non-executing"):
        planner.persist_plan_revision(
            memorial_id=memorial.id,
            plan=_plan("forged active replan"),
            parent_revision_id=revision.revision_id,
            reason_code="runtime_change",
            reason_summary="must be rejected while active",
        )
