"""Governance mutations must persist the authenticated principal as actor."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tianshu.bus.event_bus import EventBus
from tianshu.gateway.edicts_api import edicts_router
from tianshu.models import Edict
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
    app.state.event_bus = EventBus(storage=storage)
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
    ("endpoint", "event_type"),
    [
        ("approve", "plan.approved"),
        ("reject", "plan.rejected"),
    ],
)
def test_plan_review_uses_authenticated_actor(
    storage,
    endpoint: str,
    event_type: str,
) -> None:
    edict = Edict(goal=f"plan {endpoint}")
    storage.save_edict(edict)
    storage.append_event(
        edict.id,
        None,
        "plan.pending_review",
        {"plan": {"steps": ["one"]}},
    )

    with TestClient(_app_with_identity(storage)) as client:
        response = client.post(
            f"/edicts/{edict.id}/plan/{endpoint}",
            json={"actor": "forged:admin"},
        )

    assert response.status_code == 200
    event = next(item for item in storage.get_events(edict.id) if item["event_type"] == event_type)
    assert event["payload"]["actor"] == "user:owner"
