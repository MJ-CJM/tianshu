"""HTTP tool decisions bind durable IDs and middleware AuthContext."""

from concurrent.futures import ThreadPoolExecutor
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
    app.state.decision_service = service
    app.state.storage = storage

    @app.middleware("http")
    async def inject_auth(request: Request, call_next):
        request.state.auth_context = _auth(identity)
        return await call_next(request)

    app.include_router(execution_router, prefix="/api")
    return TestClient(app), manager, service


def test_http_resolves_canonical_id_and_rejects_forged_actor(storage) -> None:
    edict = Edict(id="edict-http-tool", goal="approve a tool", submitter="user:owner")
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
            "comment": "reviewed",
            "expected_version": requested.version,
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
            "expected_version": requested.version,
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
            "expected_version": requested.version,
        },
    )
    assert malformed.status_code == 422
    assert service.get(requested.decision_request_id).resolution is None  # type: ignore[union-attr]

    response = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "approve",
            "comment": "reviewed",
            "expected_version": requested.version,
            "grant_scope": "once",
        },
    )
    replay = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "reject",
            "comment": "changed my mind",
            "expected_version": requested.version,
        },
    )

    assert response.status_code == 201
    assert replay.status_code == 409
    assert response.json()["data"]["decision_request_id"] == requested.decision_request_id
    assert response.json()["data"]["actor"] == "user:owner"
    assert response.json()["data"]["action"] == "approve"


def test_memorial_alias_requires_exactly_one_pending_decision(storage) -> None:
    edict = Edict(id="edict-http-alias", goal="approve tools", submitter="user:owner")
    memorial = Memorial(id="memorial-http-alias", edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    client, _, service = _client(storage)
    first = _request(service, memorial, "http-alias-1")
    second = _request(service, memorial, "http-alias-2")

    ambiguous = client.post(
        "/api/approvals/tool_decision",
        json={
            "memorial_id": memorial.id,
            "action": "approve",
            "comment": "reviewed",
            "expected_version": first.version,
        },
    )

    assert ambiguous.status_code == 422
    assert service.get(first.decision_request_id).resolution is None  # type: ignore[union-attr]
    assert service.get(second.decision_request_id).resolution is None  # type: ignore[union-attr]


def test_http_tool_decision_requires_reason_and_exact_version(storage) -> None:
    edict = Edict(id="edict-http-strict", goal="strict tool", submitter="user:owner")
    memorial = Memorial(id="memorial-http-strict", edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    client, _, service = _client(storage)
    requested = _request(service, memorial, "http-tool-strict")

    blank = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "approve",
            "comment": "   ",
            "expected_version": requested.version,
        },
    )
    stale = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "approve",
            "comment": "reviewed",
            "expected_version": requested.version + 1,
        },
    )

    assert blank.status_code == 422
    assert stale.status_code == 409
    record = service.get(requested.decision_request_id)
    assert record is not None and record.resolution is None


def test_http_tool_decision_hides_non_owned_target(storage) -> None:
    edict = Edict(id="edict-http-owned", goal="owned tool", submitter="user:owner")
    memorial = Memorial(id="memorial-http-owned", edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    owner_client, _, service = _client(storage)
    requested = _request(service, memorial, "http-tool-owned")
    outsider_client, _, _ = _client(storage, identity="user:outsider")

    hidden = outsider_client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "reject",
            "comment": "not mine",
            "expected_version": requested.version,
        },
    )
    listed = outsider_client.get("/api/approvals/pending_tool_calls")

    assert hidden.status_code == 404
    assert listed.status_code == 200
    assert listed.json()["data"]["items"] == []
    assert owner_client.get("/api/approvals/pending_tool_calls").json()["data"]["items"]


def test_decree_adapter_resolves_durable_tool_then_projects_once(storage) -> None:
    edict = Edict(id="edict-http-decree", goal="safe decree", submitter="user:owner")
    memorial = Memorial(id="memorial-http-decree", edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    client, _, service = _client(storage)
    requested = _request(service, memorial, "http-decree")
    body = {
        "decision_request_id": requested.decision_request_id,
        "memorial_id": memorial.id,
        "action": "approve",
        "comment": "reviewed through legacy adapter",
        "expected_version": requested.version,
    }

    response = client.post("/api/decrees", json=body)
    replay = client.post("/api/decrees", json=body)

    assert response.status_code == 201
    assert replay.status_code == 409
    assert response.json()["data"]["id"] == requested.decision_request_id
    assert response.json()["data"]["actor"] == "user:owner"
    record = service.get(requested.decision_request_id)
    assert record is not None and record.resolution is not None
    assert record.request.version == requested.version + 1
    assert storage.get_memorial(memorial.id).status is TaskStatus.RUNNING  # type: ignore[union-attr]
    assert [item.id for item in storage.list_decrees_by_memorial(memorial.id)] == [
        requested.decision_request_id
    ]


def test_decree_adapter_fails_closed_for_retired_actions_and_wrong_memorial(storage) -> None:
    edict = Edict(id="edict-http-decree-closed", goal="safe decree", submitter="user:owner")
    memorial = Memorial(
        id="memorial-http-decree-closed", edict_id=edict.id, status=TaskStatus.RUNNING
    )
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    client, _, service = _client(storage)
    outsider_client, _, _ = _client(storage, identity="user:outsider")
    requested = _request(service, memorial, "http-decree-closed")
    wrong_kind = service.request(
        RequestDecisionCommand(
            kind=DecisionKind.PLAN_REVIEW,
            edict_id=edict.id,
            memorial_id=memorial.id,
            request_key="http-decree-wrong-kind",
            payload={"schema_version": 1, "revision": 1},
            expires_at=_NOW + timedelta(minutes=5),
        ),
        auth=_auth("system:policy"),
    )
    common = {
        "decision_request_id": requested.decision_request_id,
        "memorial_id": memorial.id,
        "comment": "reviewed",
        "expected_version": requested.version,
    }

    retired = client.post("/api/decrees", json={**common, "action": "retry"})
    mismatched = client.post(
        "/api/decrees",
        json={**common, "action": "reject", "memorial_id": "memorial-other"},
    )
    hidden = outsider_client.post("/api/decrees", json={**common, "action": "reject"})
    unsupported_kind = client.post(
        "/api/decrees",
        json={
            **common,
            "decision_request_id": wrong_kind.decision_request_id,
            "action": "reject",
        },
    )
    tool_kind_mismatch = client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": wrong_kind.decision_request_id,
            "action": "reject",
            "comment": "reviewed",
            "expected_version": wrong_kind.version,
        },
    )

    assert retired.status_code == 410
    assert retired.json()["detail"]["code"] == "legacy_decree_action_retired"
    assert mismatched.status_code == 422
    assert mismatched.json()["detail"]["code"] == "decision_identity_conflict"
    assert hidden.status_code == 404
    assert unsupported_kind.status_code == 410
    assert unsupported_kind.json()["detail"]["code"] == "legacy_decree_kind_unsupported"
    assert tool_kind_mismatch.status_code == 422
    assert tool_kind_mismatch.json()["detail"]["code"] == "invalid_decision_kind"
    record = service.get(requested.decision_request_id)
    assert record is not None and record.resolution is None
    assert storage.list_decrees_by_memorial(memorial.id) == []


def test_http_tool_decision_restart_uses_supplied_version(storage) -> None:
    edict = Edict(id="edict-http-restart", goal="restart tool", submitter="user:owner")
    memorial = Memorial(id="memorial-http-restart", edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    _, _, first_service = _client(storage)
    requested = _request(first_service, memorial, "http-tool-restart")
    restarted_client, _, restarted_service = _client(storage)

    response = restarted_client.post(
        "/api/approvals/tool_decision",
        json={
            "decision_request_id": requested.decision_request_id,
            "action": "reject",
            "comment": "reviewed after restart",
            "expected_version": requested.version,
        },
    )

    assert response.status_code == 201
    record = restarted_service.get(requested.decision_request_id)
    assert record is not None and record.resolution is not None
    assert record.resolution.reason == "reviewed after restart"


def test_http_tool_decision_race_has_one_winner_and_one_conflict(storage) -> None:
    edict = Edict(id="edict-http-race", goal="race tool", submitter="user:owner")
    memorial = Memorial(id="memorial-http-race", edict_id=edict.id, status=TaskStatus.RUNNING)
    storage.save_edict(edict)
    storage.save_memorial(memorial)
    client, _, service = _client(storage)
    requested = _request(service, memorial, "http-tool-race")

    def resolve(action: str):
        with TestClient(client.app) as contender:
            return contender.post(
                "/api/approvals/tool_decision",
                json={
                    "decision_request_id": requested.decision_request_id,
                    "action": action,
                    "comment": f"race {action}",
                    "expected_version": requested.version,
                },
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(resolve, ("approve", "reject")))

    assert sorted(response.status_code for response in responses) == [201, 409]
    record = service.get(requested.decision_request_id)
    assert record is not None and record.resolution is not None
    assert record.resolution.action in {"approve", "reject"}
    assert len(storage.list_decrees_by_memorial(memorial.id)) == 1
