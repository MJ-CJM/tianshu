"""Governance mutations must persist the authenticated principal as actor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.edicts_api import edicts_router
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.decision import DecisionKind, RequestDecisionCommand
from tianshu.models.plan import Plan, PlanTask
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)


def _app_with_identity(storage) -> FastAPI:
    app = FastAPI()
    app.state.storage = storage
    app.state.event_bus = EventBus()
    app.state.decision_service = DecisionService(storage)
    app.state.approval_manager = ApprovalManager(
        event_bus=app.state.event_bus,
        storage=storage,
        decision_service=app.state.decision_service,
    )
    app.include_router(edicts_router)
    context = AuthContext(
        principal=Principal(
            id="user:owner",
            kind=PrincipalKind.HUMAN,
            display_name="Owner",
            scopes=frozenset({"api"}),
        ),
        source=AuthenticationSource.BEARER,
        client_kind=ClientKind.API,
        correlation_id="test-correlation",
    )

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope.setdefault("state", {})["auth_context"] = context
        return await call_next(request)

    return app


@pytest.mark.parametrize(
    "endpoint",
    ["approve", "reject"],
)
def test_plan_review_uses_authenticated_actor(
    storage,
    endpoint: str,
) -> None:
    edict = Edict(goal=f"plan {endpoint}")
    memorial = Memorial(edict_id=edict.id, status=TaskStatus.NEEDS_REVIEW)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    app = _app_with_identity(storage)
    requested = app.state.approval_manager.request_plan_review_decision(
        edict=edict,
        memorial=memorial,
        plan=Plan(tasks=[PlanTask(task_id="one", description="one")]),
        revision=1,
    )

    with TestClient(app) as client:
        response = client.post(
            f"/edicts/{edict.id}/plan/{endpoint}",
            json={"actor": "forged:admin"},
        )

    assert response.status_code == 200
    record = app.state.decision_service.get(requested.decision_request_id)
    assert record is not None and record.resolution is not None
    assert record.resolution.actor_principal_id == "user:owner"
    assert not storage.get_events(edict.id)


def test_outer_loop_review_uses_authenticated_actor_not_body_identity(storage) -> None:
    edict = Edict(goal="outer loop review")
    memorial = Memorial(edict_id=edict.id, status=TaskStatus.NEEDS_REVIEW)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    app = _app_with_identity(storage)
    requested = app.state.decision_service.request(
        RequestDecisionCommand(
            kind=DecisionKind.OUTER_LOOP,
            edict_id=edict.id,
            memorial_id=memorial.id,
            request_key="outer-loop:L3:1",
            payload={"schema_version": 1},
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        ),
        auth=AuthContext(
            principal=Principal(
                id="system:outer-loop",
                kind=PrincipalKind.SERVICE,
                display_name="Outer Loop",
                scopes=frozenset({"decision:request"}),
            ),
            source=AuthenticationSource.TRUSTED_LOCAL,
            client_kind=ClientKind.SYSTEM,
            correlation_id="outer-loop:request",
        ),
    )

    with TestClient(app) as client:
        response = client.post(
            f"/edicts/{edict.id}/outer-loop/decide",
            json={"action": "abort", "actor": "forged:admin"},
        )

    assert response.status_code == 200
    record = app.state.decision_service.get(requested.decision_request_id)
    assert record is not None and record.resolution is not None
    assert record.resolution.actor_principal_id == "user:owner"
