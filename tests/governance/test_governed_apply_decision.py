"""Governed apply authorization is owned by the durable DecisionService."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from tianshu.executor.git_backend import GitBackend
from tianshu.executor.workspace_service import WorkspaceService
from tianshu.governance.decision_service import DecisionAuthorizationError, DecisionService
from tianshu.models.decision import (
    DecisionKind,
    RequestDecisionCommand,
    ResolveDecisionCommand,
)
from tianshu.models.principal import AuthContext, Principal

_NOW = datetime(2026, 7, 16, 2, tzinfo=UTC)


def _auth(*, scopes: frozenset[str]) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="user:reviewer",
            kind="human",
            display_name="Reviewer",
            scopes=scopes,
        ),
        source="bearer",
        client_kind="api",
        correlation_id="governed-apply-resolution",
    )


def _seed(storage) -> None:
    storage._conn.execute(  # noqa: SLF001 - focused durable fixture
        "INSERT INTO edicts (id, goal, created_at) VALUES ('edict-apply', 'apply', ?)",
        (_NOW.isoformat(),),
    )
    storage._conn.execute(  # noqa: SLF001 - focused durable fixture
        """
        INSERT INTO memorials (id, edict_id, status, created_at)
        VALUES ('memorial-apply', 'edict-apply', 'completed', ?)
        """,
        (_NOW.isoformat(),),
    )
    storage._conn.commit()  # noqa: SLF001


def _request(service: DecisionService, auth: AuthContext):
    return service.request(
        RequestDecisionCommand(
            kind=DecisionKind.GOVERNED_APPLY,
            edict_id="edict-apply",
            memorial_id="memorial-apply",
            request_key="workspace-apply:lease-1",
            payload={"schema_version": 1, "run_id": "memorial-apply"},
            expires_at=_NOW + timedelta(minutes=5),
        ),
        auth=auth,
    )


def test_governed_apply_resolution_requires_workspace_scope_and_audits_denial(storage) -> None:
    _seed(storage)
    service = DecisionService(storage, clock=lambda: _NOW)
    api_only = _auth(scopes=frozenset({"api"}))
    requested = _request(service, api_only)

    with pytest.raises(DecisionAuthorizationError) as caught:
        service.resolve(
            requested.decision_request_id,
            ResolveDecisionCommand(
                action="approve",
                reason="reviewed",
                payload={"schema_version": 1},
                expected_version=1,
            ),
            auth=api_only,
        )

    assert caught.value.code == "workspace_apply_scope_required"
    record = service.get(requested.decision_request_id)
    assert record is not None and record.resolution is None
    assert record.request.status.value == "pending"
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events"
        ).fetchone()[0]
        == 0
    )
    [denial] = [
        item for item in storage.list_system_audit() if item.action == "decision.resolve.denied"
    ]
    assert denial.reason_code == "workspace_apply_scope_required"
    assert denial.actor_digest == hashlib.sha256(b"user:reviewer").hexdigest()
    assert denial.correlation_id == "governed-apply-resolution"


def test_governed_apply_resolution_with_scope_keeps_single_resolution_and_outbox(storage) -> None:
    _seed(storage)
    service = DecisionService(storage, clock=lambda: _NOW)
    reviewer = _auth(scopes=frozenset({"api", "workspace:apply"}))
    requested = _request(service, reviewer)

    resolution = service.resolve(
        requested.decision_request_id,
        ResolveDecisionCommand(
            action="approve",
            reason="reviewed",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=reviewer,
    )

    assert resolution.actor_principal_id == reviewer.principal.id
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM decision_resolutions"
        ).fetchone()[0]
        == 1
    )
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='decision.resolved'"
        ).fetchone()[0]
        == 1
    )


def test_rejected_governed_apply_never_projects_apply_authority(storage, tmp_path) -> None:
    _seed(storage)
    service = DecisionService(storage, clock=lambda: _NOW)
    reviewer = _auth(scopes=frozenset({"api", "workspace:apply"}))
    requested = _request(service, reviewer)
    service.resolve(
        requested.decision_request_id,
        ResolveDecisionCommand(
            action="reject",
            reason="not approved",
            payload={"schema_version": 1},
            expected_version=1,
        ),
        auth=reviewer,
    )
    workspace = WorkspaceService(
        storage,
        GitBackend(),
        tmp_path / "rejected-staging",
        service,
    )

    assert workspace.project_governed_apply_decision(requested.decision_request_id) is None
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM apply_decisions"
        ).fetchone()[0]
        == 0
    )


def test_expired_governed_apply_never_projects_apply_authority(storage, tmp_path) -> None:
    _seed(storage)
    clock = [_NOW]
    service = DecisionService(storage, clock=lambda: clock[0])
    reviewer = _auth(scopes=frozenset({"api", "workspace:apply"}))
    requested = _request(service, reviewer)
    clock[0] = requested.expires_at
    assert service.expire_due() == 1
    workspace = WorkspaceService(
        storage,
        GitBackend(),
        tmp_path / "expired-staging",
        service,
    )

    assert workspace.project_governed_apply_decision(requested.decision_request_id) is None
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM apply_decisions"
        ).fetchone()[0]
        == 0
    )
