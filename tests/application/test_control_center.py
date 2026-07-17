from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from tests.evidence._fixtures import evidence_service, seed_closed_run

from tianshu.application.control_center import (
    ControlCenterQueryService,
    ControlCenterUnavailable,
)
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.canonical import canonical_sha256
from tianshu.models.control_center import ControlCenterSnapshotV1
from tianshu.models.decision import (
    DecisionKind,
    DecisionRequestV1,
    DecisionStatus,
)
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.models.run_state import (
    AgentContinuationV1,
    PersistedUsageSummaryV1,
    RunPhase,
    RunStateV1,
)
from tianshu.storage import Storage

NOW = datetime(2026, 7, 17, 9, tzinfo=UTC)


def _auth(principal_id: str = "user:owner") -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=principal_id,
            kind=PrincipalKind.HUMAN,
            display_name=principal_id,
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.WEB,
        correlation_id="corr-control",
    )


def _run_state(
    memorial: Memorial,
    *,
    phase: RunPhase,
    updated_at: datetime,
) -> RunStateV1:
    usage = PersistedUsageSummaryV1(
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        cache_read_tokens=0,
        cost_cny=0.01,
        actual_model="demo-model",
        upstream_provider=None,
    )
    continuation = AgentContinuationV1(
        messages=(),
        pending_tool=None,
        iteration=1,
        usage=usage,
        checkpoint_ref=None,
        resolved_decision_id=None,
        side_effect_cursor=0,
    )
    return RunStateV1(
        memorial_id=memorial.id,
        edict_id=memorial.edict_id,
        phase=phase,
        continuation=continuation,
        checkpoint_ref=None,
        side_effect_cursor=0,
        version=1,
        created_at=NOW - timedelta(hours=1),
        updated_at=updated_at,
    )


def _seed_run(
    storage: Storage,
    *,
    edict_id: str,
    memorial_id: str,
    submitter: str,
    title: str,
    phase: RunPhase,
    updated_at: datetime,
) -> None:
    edict = Edict(id=edict_id, title=title, goal=title, submitter=submitter)
    memorial = Memorial(
        id=memorial_id,
        edict_id=edict_id,
        instruction=title,
        status=TaskStatus.RUNNING,
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    with storage.unit_of_work() as unit_of_work:
        storage.run_state_repo.create(
            unit_of_work.connection,
            _run_state(memorial, phase=phase, updated_at=updated_at),
        )
        unit_of_work.commit()


def _seed_decision(
    storage: Storage,
    *,
    decision_id: str,
    edict_id: str,
    memorial_id: str,
    expires_at: datetime,
) -> None:
    payload = {"tool_name": "read_file", "arguments": {"path": "README.md"}}
    request = DecisionRequestV1(
        decision_request_id=decision_id,
        kind=DecisionKind.TOOL,
        edict_id=edict_id,
        memorial_id=memorial_id,
        request_key=f"tool:{decision_id}",
        payload=payload,
        payload_hash=canonical_sha256(payload),
        requested_by="service:executor",
        expires_at=expires_at,
        status=DecisionStatus.PENDING,
        version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    with storage.unit_of_work() as unit_of_work:
        storage.decision_repo.add_or_get(unit_of_work.connection, request)
        unit_of_work.commit()


def test_snapshot_uses_real_scoped_storage_and_stable_sorting(storage, tmp_path) -> None:
    _seed_run(
        storage,
        edict_id="edict-owner-new",
        memorial_id="memorial-owner-new",
        submitter="user:owner",
        title="最近运行",
        phase=RunPhase.EXECUTING,
        updated_at=NOW,
    )
    _seed_run(
        storage,
        edict_id="edict-owner-old",
        memorial_id="memorial-owner-old",
        submitter="user:owner",
        title="较早运行",
        phase=RunPhase.PAUSED,
        updated_at=NOW - timedelta(minutes=2),
    )
    _seed_run(
        storage,
        edict_id="edict-other",
        memorial_id="memorial-other",
        submitter="user:other",
        title="不可见运行",
        phase=RunPhase.EXECUTING,
        updated_at=NOW + timedelta(minutes=1),
    )
    _seed_decision(
        storage,
        decision_id="decision-later",
        edict_id="edict-owner-new",
        memorial_id="memorial-owner-new",
        expires_at=NOW + timedelta(minutes=20),
    )
    _seed_decision(
        storage,
        decision_id="decision-sooner",
        edict_id="edict-owner-old",
        memorial_id="memorial-owner-old",
        expires_at=NOW + timedelta(minutes=10),
    )
    _seed_decision(
        storage,
        decision_id="decision-other",
        edict_id="edict-other",
        memorial_id="memorial-other",
        expires_at=NOW + timedelta(minutes=1),
    )
    evidence_edict, evidence_memorial = seed_closed_run(storage)
    storage._conn.execute(  # noqa: SLF001 - scoped read-model fixture
        "UPDATE edicts SET submitter = ?, title = ? WHERE id = ?",
        ("user:owner", "已完成证据", evidence_edict.id),
    )
    storage._conn.commit()  # noqa: SLF001
    service = evidence_service(storage, tmp_path / "artifacts")
    opened = service.build_open(evidence_memorial.id)
    closed = service.close(evidence_memorial.id, expected_version=opened.version)

    query = ControlCenterQueryService(
        unit_of_work=storage.unit_of_work,
        decision_repository=storage.decision_repo,
        run_state_repository=storage.run_state_repo,
        evidence_repository=storage.evidence_repo,
        readiness_status=lambda: "ready",
        clock=lambda: NOW,
    )

    snapshot = query.get_snapshot(_auth())

    assert snapshot.generated_at == NOW
    assert snapshot.readiness == "ready"
    assert snapshot.evolution_status == "not_enabled"
    assert [item.memorial_id for item in snapshot.active_runs] == [
        "memorial-owner-new",
        "memorial-owner-old",
    ]
    assert [item.decision_request_id for item in snapshot.pending_decisions] == [
        "decision-sooner",
        "decision-later",
    ]
    assert [item.bundle_id for item in snapshot.recent_evidence] == [closed.bundle_id]
    dumped = snapshot.model_dump(mode="json")
    assert len(dumped["active_runs"]) == 2
    assert len(dumped["pending_decisions"]) == 2
    assert len(dumped["recent_evidence"]) == 1
    assert "不可见运行" not in json.dumps(dumped, ensure_ascii=False)


def test_snapshot_exposes_degraded_evolution_but_rejects_not_ready(storage) -> None:
    degraded = ControlCenterQueryService(
        unit_of_work=storage.unit_of_work,
        decision_repository=storage.decision_repo,
        run_state_repository=storage.run_state_repo,
        evidence_repository=storage.evidence_repo,
        readiness_status=lambda: "degraded",
        clock=lambda: NOW,
    ).get_snapshot(_auth())
    assert degraded.readiness == "degraded"
    assert degraded.evolution_status == "degraded"

    unavailable = ControlCenterQueryService(
        unit_of_work=storage.unit_of_work,
        decision_repository=storage.decision_repo,
        run_state_repository=storage.run_state_repo,
        evidence_repository=storage.evidence_repo,
        readiness_status=lambda: "not_ready",
        clock=lambda: NOW,
    )
    with pytest.raises(ControlCenterUnavailable):
        unavailable.get_snapshot(_auth())


def test_storage_decode_failure_is_not_hidden_as_an_empty_snapshot(storage) -> None:
    _seed_run(
        storage,
        edict_id="edict-corrupt",
        memorial_id="memorial-corrupt",
        submitter="user:owner",
        title="损坏运行",
        phase=RunPhase.EXECUTING,
        updated_at=NOW,
    )
    storage._conn.execute(  # noqa: SLF001 - bypass durability guard for decode fixture
        "DROP TRIGGER run_states_validate_update_v12"
    )
    storage._conn.execute("PRAGMA ignore_check_constraints=ON")  # noqa: SLF001
    storage._conn.execute(  # noqa: SLF001 - deliberate corruption fixture
        "UPDATE run_states SET continuation_json = '{}' WHERE memorial_id = ?",
        ("memorial-corrupt",),
    )
    storage._conn.commit()  # noqa: SLF001
    query = ControlCenterQueryService(
        unit_of_work=storage.unit_of_work,
        decision_repository=storage.decision_repo,
        run_state_repository=storage.run_state_repo,
        evidence_repository=storage.evidence_repo,
        readiness_status=lambda: "ready",
        clock=lambda: NOW,
    )

    with pytest.raises(ControlCenterUnavailable):
        query.get_snapshot(_auth())


def test_contract_has_no_system_confidence_or_trust_score() -> None:
    schema = json.dumps(ControlCenterSnapshotV1.model_json_schema(), ensure_ascii=False).lower()
    for forbidden in ("system_confidence", "trust_score", "confidence", "系统可信", "置信度"):
        assert forbidden not in schema
