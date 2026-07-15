"""HTTP tool decisions bind durable IDs and middleware AuthContext."""

from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tianshu.bus.event_bus import EventBus
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.execution_api import execution_router
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.decision import DecisionKind, RequestDecisionCommand
from tianshu.models.principal import AuthContext, Principal

_NOW = datetime(2026, 7, 15, 15, tzinfo=UTC)


def _auth(identity: str) -> AuthContext:
    return AuthContext(
        principal=Principal(
            id=identity,
            kind="human",
            display_name=identity,
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="web",
        correlation_id=f"http:{identity}",
    )


def _request(service: DecisionService, memorial: Memorial, key: str):
    return service.request(
        RequestDecisionCommand(
            kind=DecisionKind.TOOL,
            edict_id=memorial.edict_id,
            memorial_id=memorial.id,
            request_key=key,
            payload={
                "schema_version": 1,
                "tool_name": "write_file",
                "arguments": {"path": "README.md"},
                "tool_tier": "T1_WORKSPACE",
                "policy_rule_id": "approval_required",
            },
            expires_at=_NOW + timedelta(minutes=5),
        ),
        auth=_auth("system:policy"),
    )


def _client(storage, identity: str = "user:owner"):
    service = DecisionService(storage, clock=lambda: _NOW)
    manager = ApprovalManager(EventBus(), storage, decision_service=service, clock=lambda: _NOW)
    app = FastAPI()
    app.state.approval_manager = manager

    @app.middleware("http")
    async def inject_auth(request: Request, call_next):
        request.state.auth_context = _auth(identity)
        return await call_next(request)

    app.include_router(execution_router, prefix="/api")
    return TestClient(app), manager, service


def test_http_resolves_canonical_id_and_rejects_forged_actor(storage) -> None:
    edict = Edict(id="edict-http-tool", goal="approve a tool")
    memorial = Memorial(id="memorial-http-tool", edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    client, _, service = _client(storage)
    requested = _request(service, memorial, "http-tool-1")

    forged = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "approve",
            "actor": "forged",
        },
    )
    assert forged.status_code == 422
    assert service.get(requested.decision_request_id).resolution is None  # type: ignore[union-attr]

    oversized = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "guide",
            "comment": "x" * 2001,
        },
    )
    assert oversized.status_code == 422
    assert service.get(requested.decision_request_id).resolution is None  # type: ignore[union-attr]

    malformed = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "guide",
            "comment": {"nested": {"unexpected": [1, 2, 3]}},
        },
    )
    assert malformed.status_code == 422
    assert service.get(requested.decision_request_id).resolution is None  # type: ignore[union-attr]

    response = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "approve",
            "grant_scope": "once",
        },
    )
    replay = client.post(
        "/api/approvals/tool_decision",
        json={"decision_request_id": requested.decision_request_id, "action": "reject"},
    )

    assert response.status_code == 201
    assert replay.status_code == 201
    assert response.json()["data"] == replay.json()["data"]
    assert response.json()["data"]["decision_request_id"] == requested.decision_request_id
    assert response.json()["data"]["actor"] == "user:owner"
    assert response.json()["data"]["action"] == "approve"


def test_memorial_alias_requires_exactly_one_pending_decision(storage) -> None:
    edict = Edict(id="edict-http-alias", goal="approve tools")
    memorial = Memorial(id="memorial-http-alias", edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    client, _, service = _client(storage)
    first = _request(service, memorial, "http-alias-1")
    second = _request(service, memorial, "http-alias-2")

    ambiguous = client.post(
        "/api/approvals/tool_decision",
        json={"memorial_id": memorial.id, "action": "approve"},
    )

    assert ambiguous.status_code == 409
    assert service.get(first.decision_request_id).resolution is None  # type: ignore[union-attr]
    assert service.get(second.decision_request_id).resolution is None  # type: ignore[union-attr]
