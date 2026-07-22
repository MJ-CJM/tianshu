from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.evidence._fixtures import evidence_service, seed_closed_run

from tianshu.application.edict_detail import (
    EdictDetailNotFound,
    EdictDetailQueryService,
    EdictDetailUnavailable,
)
from tianshu.governance.decision_service import DecisionService
from tianshu.models.decision import DecisionKind, RequestDecisionCommand
from tianshu.models.principal import AuthContext, Principal

NOW = datetime(2026, 7, 17, 8, 10, tzinfo=UTC)


def _auth(principal_id: str = "user:owner") -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=principal_id,
            kind="human",
            display_name="Owner",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="corr-edict-detail",
    )


def _seed(storage, tmp_path):
    edict, memorial = seed_closed_run(storage, correlation_id="corr-edict-detail")
    storage._conn.execute(  # noqa: SLF001 - principal-scoping fixture
        "UPDATE edicts SET submitter = ? WHERE id = ?",
        ("user:owner", edict.id),
    )
    storage._conn.commit()  # noqa: SLF001
    bundles = evidence_service(storage, tmp_path / "artifacts")
    opened = bundles.build_open(memorial.id)
    closed = bundles.close(memorial.id, expected_version=opened.version)
    decision_service = DecisionService(storage, clock=lambda: NOW)
    decision = decision_service.request(
        RequestDecisionCommand(
            kind=DecisionKind.TOOL,
            edict_id=edict.id,
            memorial_id=memorial.id,
            request_key="tool:detail",
            payload={
                "tool_name": "workspace_apply",
                "permission_boundary": "workspace:apply",
                "restore_point": "artifact:restore-point",
                "tool_arguments": {"path": "/private/internal-worktree"},
            },
            expires_at=NOW + timedelta(minutes=10),
        ),
        auth=_auth(),
    )
    return edict, memorial, closed, decision


def test_snapshot_composes_scoped_durable_truth_without_sensitive_evidence_fields(
    storage,
    tmp_path,
) -> None:
    edict, memorial, closed, decision = _seed(storage, tmp_path)

    snapshot = EdictDetailQueryService(storage).get_snapshot(_auth(), edict.id)

    assert snapshot.edict.id == edict.id
    assert snapshot.edict.governance_contract == edict.governance_contract
    assert [item.id for item in snapshot.memorials] == [memorial.id]
    assert snapshot.memorials[0].effective_governance_contract is not None
    assert [(item.memorial_id, item.phase, item.version) for item in snapshot.runs] == [
        (memorial.id, "completed", 1)
    ]
    assert [revision.revision_id for revision in snapshot.runs[0].plan_lineage] == [
        "plan-revision-1"
    ]
    assert [item.request.decision_request_id for item in snapshot.decisions] == [
        decision.decision_request_id
    ]
    assert snapshot.decisions[0].request.payload == {
        "tool_name": "workspace_apply",
        "permission_boundary": "workspace:apply",
        "restore_point": "artifact:restore-point",
    }
    [evidence] = snapshot.evidence
    assert evidence.bundle_id == closed.bundle_id
    assert evidence.status == "closed"
    assert evidence.content_hash == closed.content_hash
    assert evidence.executor.adapter_id == closed.snapshot.executor_manifest.adapter_id
    assert evidence.executor.display_name == closed.snapshot.executor_manifest.display_name
    assert evidence.auditor.auditor_id == closed.snapshot.auditor.auditor_id
    assert evidence.auditor.auditor_id != evidence.executor.adapter_id
    assert evidence.artifacts[0].digest == closed.snapshot.artifacts[0].digest
    assert evidence.checks == closed.snapshot.checks

    payload = snapshot.model_dump(mode="json")
    evidence_payload = payload["evidence"][0]
    assert "snapshot" not in evidence_payload
    assert "reproduction_command" not in evidence_payload
    assert "workspace_base_revision" not in evidence_payload["environment"]
    assert "uri" not in evidence_payload["artifacts"][0]
    assert "root_fingerprint" not in evidence_payload["artifacts"][0]
    decision_payload = payload["decisions"][0]
    assert "request_key" not in decision_payload["request"]
    assert "payload_hash" not in decision_payload["request"]
    assert "tool_arguments" not in decision_payload["request"]["payload"]


def test_snapshot_hides_other_principals_and_fails_closed_on_repository_error(
    storage,
    tmp_path,
    monkeypatch,
) -> None:
    edict, _, _, _ = _seed(storage, tmp_path)
    service = EdictDetailQueryService(storage)

    with pytest.raises(EdictDetailNotFound):
        service.get_snapshot(_auth("user:other"), edict.id)

    monkeypatch.setattr(
        storage.run_state_repo,
        "list_for_edict",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database offline")),
    )
    with pytest.raises(EdictDetailUnavailable):
        service.get_snapshot(_auth(), edict.id)
