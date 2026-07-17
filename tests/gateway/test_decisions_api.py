"""Authenticated, disclosure-safe Decision API contracts for Slice 3B."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.application.outbox import OutboxDispatcher
from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.governance.decision_service import DecisionService
from tianshu.models.decision import DecisionKind, RequestDecisionCommand
from tianshu.models.principal import AuthContext, Principal
from tianshu.storage import Storage
from tianshu.storage.outbox_repo import OutboxRepository
from tianshu.tools.policy_store import InMemorySessionRuleStore

_TOKEN = "decision-api-bootstrap-token"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}
_BASE_URL = "https://tianshu.example.com"
_NOW = datetime(2026, 7, 15, 10, tzinfo=UTC)


def _settings() -> TianshuSettings:
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=_BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=_BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=("sha256:" + hashlib.sha256(_TOKEN.encode()).hexdigest()),
    )


def _requester() -> AuthContext:
    return AuthContext(
        principal=Principal(
            id="user:requester",
            kind="human",
            display_name="Requester",
            scopes=frozenset({"api"}),
        ),
        source="bearer",
        client_kind="api",
        correlation_id="decision-api-seed",
    )


def _seed(storage: Storage) -> None:
    storage._conn.execute(  # noqa: SLF001 - API fixture
        "INSERT INTO edicts (id, goal, created_at) VALUES (?, ?, ?)",
        ("edict-1", "goal", _NOW.isoformat()),
    )
    storage._conn.execute(  # noqa: SLF001 - API fixture
        "INSERT INTO memorials (id, edict_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("memorial-1", "edict-1", "submitted", _NOW.isoformat()),
    )
    storage._conn.commit()  # noqa: SLF001


def _request_command(
    *,
    request_key: str = "tool-call:api",
    expires_at: datetime | None = None,
) -> RequestDecisionCommand:
    return RequestDecisionCommand(
        kind=DecisionKind.TOOL,
        edict_id="edict-1",
        memorial_id="memorial-1",
        request_key=request_key,
        payload={"tool_name": "read_file", "arguments": {"path": "README.md"}},
        expires_at=expires_at or (_NOW + timedelta(minutes=10)),
    )


def _resolve_body(**updates: object) -> dict[str, object]:
    body: dict[str, object] = {
        "action": "approve",
        "reason": "reviewed",
        "payload": {"schema_version": 1, "grant_scope": "once", "grant_reason": None},
        "expected_version": 1,
    }
    body.update(updates)
    return body


@pytest.fixture
def decision_api(tmp_path):
    from tianshu.gateway.decisions_api import decisions_router

    storage = Storage(str(tmp_path / "decisions-api.db"))
    storage.init_db()
    _seed(storage)
    clock = [_NOW]
    service = DecisionService(storage, clock=lambda: clock[0])
    settings = _settings()
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.decision_service = service
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(decisions_router, prefix="/api")
    try:
        yield storage, service, clock, app
    finally:
        storage.close()


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=_BASE_URL, client=("127.0.0.1", 41000))


def test_list_and_get_are_authenticated_filtered_and_correlated(decision_api) -> None:
    _, service, _, app = decision_api
    tool = service.request(_request_command(), auth=_requester())
    service.request(
        RequestDecisionCommand(
            kind=DecisionKind.PLAN_REVIEW,
            edict_id="edict-1",
            memorial_id="memorial-1",
            request_key="plan:api",
            payload={"revision": 1},
            expires_at=_NOW + timedelta(minutes=10),
        ),
        auth=_requester(),
    )

    with _client(app) as client:
        anonymous = client.get("/api/decisions")
        listed = client.get("/api/decisions", headers=_HEADERS, params={"kind": "tool"})
        fetched = client.get(f"/api/decisions/{tool.decision_request_id}", headers=_HEADERS)

    assert anonymous.status_code == 401
    assert listed.status_code == fetched.status_code == 200
    assert [item["decision_request_id"] for item in listed.json()["items"]] == [
        tool.decision_request_id
    ]
    assert fetched.json()["data"]["request"]["decision_request_id"] == tool.decision_request_id
    for response in (listed, fetched):
        assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
        assert "credential_id" not in response.text
        assert "remote_addr" not in response.text
        assert "127.0.0.1" not in response.text


def test_resolve_rejects_body_identity_and_derives_actor_from_auth_context(decision_api) -> None:
    storage, service, clock, app = decision_api
    requested = service.request(_request_command(), auth=_requester())
    clock[0] += timedelta(minutes=1)
    sentinel = "FORGED_ACTOR_MUST_NOT_BE_ACCEPTED"

    with _client(app) as client:
        forged = [
            client.post(
                f"/api/decisions/{requested.decision_request_id}/resolve",
                headers=_HEADERS,
                json={**_resolve_body(), field: sentinel},
            )
            for field in (
                "actor",
                "actor_display_name",
                "actor_principal_id",
                "principal",
                "remote_addr",
                "requested_by",
                "source_ip",
            )
        ]
        resolved = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers=_HEADERS,
            json=_resolve_body(),
        )

    for response in forged:
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_decision_resolution"
        assert sentinel not in response.text
    assert resolved.status_code == 200
    assert resolved.json()["correlation_id"] == resolved.headers["x-correlation-id"]
    assert resolved.json()["data"]["actor_principal_id"] == "user:owner"
    assert resolved.json()["data"]["actor_display_name"] == "Owner"
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["version"] == 2
    assert resolved.json()["record"]["request"]["status"] == "resolved"
    assert resolved.json()["record"]["request"]["version"] == 2
    assert resolved.json()["record"]["resolution"]["actor_principal_id"] == "user:owner"
    record = service.get(requested.decision_request_id)
    assert record is not None and record.resolution is not None
    assert record.resolution.actor_principal_id == "user:owner"
    [outbox] = storage._conn.execute(  # noqa: SLF001
        "SELECT payload_json FROM outbox_events WHERE event_type = 'decision.resolved'"
    ).fetchall()
    assert json.loads(outbox["payload_json"])["correlation_id"] == resolved.json()["correlation_id"]
    assert sentinel not in outbox["payload_json"]


def test_governed_apply_resolution_with_api_only_scope_is_403_and_audited(
    decision_api,
) -> None:
    storage, service, _, app = decision_api
    requested = service.request(
        RequestDecisionCommand(
            kind=DecisionKind.GOVERNED_APPLY,
            edict_id="edict-1",
            memorial_id="memorial-1",
            request_key="workspace-apply:lease-api-scope",
            payload={"schema_version": 1, "run_id": "memorial-1"},
            expires_at=_NOW + timedelta(minutes=10),
        ),
        auth=_requester(),
    )
    api_only = app.state.auth_service.issue_pat(
        Principal(
            id="service:api-only-reviewer",
            kind="service",
            display_name="API-only reviewer",
            scopes=frozenset({"api"}),
        ),
        label="api-only-reviewer",
        scopes=frozenset({"api"}),
    )

    with _client(app) as client:
        response = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers={"Authorization": f"Bearer {api_only.raw_token}"},
            json={
                "action": "approve",
                "reason": "reviewed",
                "payload": {"schema_version": 1},
                "expected_version": 1,
            },
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "workspace_apply_scope_required"
    record = service.get(requested.decision_request_id)
    assert record is not None and record.resolution is None
    [denial] = [
        event for event in storage.list_system_audit() if event.action == "decision.resolve.denied"
    ]
    assert denial.reason_code == "workspace_apply_scope_required"
    assert denial.correlation_id == response.headers["x-correlation-id"]


def test_not_found_conflict_and_validation_use_stable_disclosure_safe_mapping(decision_api) -> None:
    _, service, clock, app = decision_api
    requested = service.request(_request_command(), auth=_requester())
    secret = "SECRET_ERROR_DETAIL_MUST_NOT_LEAK"

    with _client(app) as client:
        missing_get = client.get(f"/api/decisions/{secret}", headers=_HEADERS)
        missing_resolve = client.post(
            f"/api/decisions/{secret}/resolve",
            headers=_HEADERS,
            json=_resolve_body(),
        )
        stale = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers=_HEADERS,
            json=_resolve_body(expected_version=2),
        )
        malformed = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers=_HEADERS,
            json=_resolve_body(action="amend", payload={"schema_version": 1}),
        )
        blank_reason = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers=_HEADERS,
            json=_resolve_body(reason="  "),
        )
        invalid_kind = client.get(
            "/api/decisions",
            headers=_HEADERS,
            params={"kind": "unknown"},
        )
        clock[0] = requested.expires_at
        late = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers=_HEADERS,
            json=_resolve_body(),
        )

    cases = (
        (missing_get, 404, "decision_not_found"),
        (missing_resolve, 404, "decision_not_found"),
        (stale, 409, "decision_stale"),
        (malformed, 422, "invalid_decision_resolution"),
        (blank_reason, 422, "invalid_decision_resolution"),
        (invalid_kind, 422, "invalid_decision_kind"),
        (late, 409, "decision_expired"),
    )
    for response, expected_status, expected_code in cases:
        assert response.status_code == expected_status
        assert response.json()["detail"]["code"] == expected_code
        assert response.json()["detail"]["correlation_id"] == response.headers["x-correlation-id"]
        assert secret not in response.text
        assert "127.0.0.1" not in response.text
        assert "bootstrap" not in response.text


def test_unparsed_resolution_denials_are_audited_via_service_without_body_disclosure(
    decision_api,
) -> None:
    storage, service, _, app = decision_api
    requested = service.request(
        _request_command(request_key="tool-call:malformed-api"),
        auth=_requester(),
    )
    sentinel = "MALFORMED_BODY_SECRET_MUST_NOT_REACH_AUDIT"

    with _client(app) as client:
        bad_json = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers={**_HEADERS, "content-type": "application/json"},
            content="{",
        )
        blank_reason = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers=_HEADERS,
            json=_resolve_body(reason="  "),
        )
        forged_actor = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers=_HEADERS,
            json={**_resolve_body(), "requested_by": sentinel, "source_ip": sentinel},
        )

    denied_responses = (bad_json, blank_reason, forged_actor)
    assert [response.status_code for response in denied_responses] == [422, 422, 422]
    denials = [
        event for event in storage.list_system_audit() if event.action == "decision.resolve.denied"
    ]
    assert len(denials) == 3
    assert {event.reason_code for event in denials} == {"invalid_decision_resolution"}
    assert {event.correlation_id for event in denials} == {
        response.headers["x-correlation-id"] for response in denied_responses
    }
    assert {event.actor_digest for event in denials} == {hashlib.sha256(b"user:owner").hexdigest()}
    assert {event.subject_digest for event in denials} == {
        hashlib.sha256(requested.decision_request_id.encode()).hexdigest()
    }
    assert sentinel not in repr(denials)
    record = service.get(requested.decision_request_id)
    assert record is not None
    assert record.request.status.value == "pending"
    assert record.resolution is None
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events"
        ).fetchone()[0]
        == 0
    )


def test_resolution_json_parser_failures_are_safe_audited_422_without_authority_change(
    decision_api,
) -> None:
    storage, service, _, app = decision_api
    requested = service.request(
        _request_command(request_key="tool-call:json-parser-failures"),
        auth=_requester(),
    )
    secret = "DEEP_JSON_SECRET_MUST_NOT_REACH_AUDIT"
    oversized_integer = (
        '{"action":"approve","reason":"reviewed","payload":{},"expected_version":'
        + ("9" * 5000)
        + "}"
    )
    deeply_nested = ("[" * 10000) + json.dumps(secret) + ("]" * 10000)

    with TestClient(
        app,
        base_url=_BASE_URL,
        client=("127.0.0.1", 41000),
        raise_server_exceptions=False,
    ) as client:
        responses = [
            client.post(
                f"/api/decisions/{requested.decision_request_id}/resolve",
                headers={**_HEADERS, "content-type": "application/json"},
                content=body,
            )
            for body in (oversized_integer, deeply_nested)
        ]

    assert [response.status_code for response in responses] == [422, 422]
    assert {response.json()["detail"]["code"] for response in responses} == {
        "invalid_decision_resolution"
    }
    assert all(secret not in response.text for response in responses)

    denials = [
        event for event in storage.list_system_audit() if event.action == "decision.resolve.denied"
    ]
    assert len(denials) == 2
    assert {event.reason_code for event in denials} == {"invalid_decision_resolution"}
    assert {event.correlation_id for event in denials} == {
        response.headers["x-correlation-id"] for response in responses
    }
    assert {event.actor_digest for event in denials} == {hashlib.sha256(b"user:owner").hexdigest()}
    assert {event.subject_digest for event in denials} == {
        hashlib.sha256(requested.decision_request_id.encode()).hexdigest()
    }
    assert {tuple(sorted(event.metadata.items())) for event in denials} == {
        (("actual_version", 1), ("kind", "tool"), ("status", "pending"))
    }
    assert secret not in repr(denials)

    record = service.get(requested.decision_request_id)
    assert record is not None
    assert record.request == requested
    assert record.resolution is None
    assert (
        storage._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM outbox_events"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.parametrize("tool_name", ("shell_exec", "bash"))
async def test_generic_tool_resolution_normalizes_dangerous_scope_before_outbox_projection(
    decision_api,
    tool_name: str,
) -> None:
    storage, service, _, app = decision_api
    requested = service.request(
        _request_command(request_key=f"tool-call:dangerous-api:{tool_name}").model_copy(
            update={
                "payload": {
                    "tool_name": tool_name,
                    "arguments": {"command": "git status"},
                }
            }
        ),
        auth=_requester(),
    )
    bus = EventBus()
    rules = InMemorySessionRuleStore()
    manager = ApprovalManager(
        bus,
        storage,
        session_rule_store=rules,
        decision_service=service,
        clock=lambda: _NOW,
    )
    bus.on(
        "decision.resolved",
        manager.handle_decision_resolved,
        consumer_name="approval_manager.tool_decree_projection.v1",
    )

    with _client(app) as client:
        response = client.post(
            f"/api/decisions/{requested.decision_request_id}/resolve",
            headers=_HEADERS,
            json=_resolve_body(
                payload={
                    "schema_version": 1,
                    "grant_scope": "always",
                    "grant_reason": "reviewed in Web",
                    "requested_grant_scope": "once",
                    "grant_downgraded": False,
                    "grant_downgrade_reason": "forged client metadata",
                }
            ),
        )

    assert response.status_code == 200
    resolution = response.json()["data"]
    assert resolution["payload"] == {
        "schema_version": 1,
        "grant_scope": "once",
        "grant_reason": "reviewed in Web",
        "requested_grant_scope": "always",
        "grant_downgraded": True,
        "grant_downgrade_reason": (
            f"Cannot grant 'always' scope to bash-family tool '{tool_name}'"
        ),
    }
    record = service.get(requested.decision_request_id)
    assert record is not None and record.resolution is not None
    assert record.resolution.model_dump(mode="json") == resolution

    dispatcher = OutboxDispatcher(
        OutboxRepository(storage.unit_of_work),
        bus,
        owner_id="dangerous-tool-api-projection",
        clock=lambda: _NOW,
    )
    assert await dispatcher.drain_once() == 1

    [decree] = storage.list_decrees_by_memorial("memorial-1")
    assert decree.action == "approve"
    assert decree.actor == "user:owner"
    [projection] = [
        event for event in storage.get_events("edict-1") if event["event_type"] == "decree.approved"
    ]
    assert projection["payload"]["grant_scope"] == "once"
    assert projection["payload"]["requested_grant_scope"] == "always"
    assert projection["payload"]["grant_downgraded"] is True
    assert projection["payload"]["grant_downgrade_reason"] == (
        f"Cannot grant 'always' scope to bash-family tool '{tool_name}'"
    )
    assert await rules.list_by_scope("always") == []
