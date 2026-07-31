"""Global operational reads are restricted to administrators."""

from __future__ import annotations

import hashlib

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.config import TianshuSettings
from tianshu.executor.lanes import LaneManager
from tianshu.executor.worker_pool import WorkerPool
from tianshu.gateway.audit_api import audit_router
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.execution_api import execution_router
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.storage import Storage
from tianshu.tools.policy_store import InMemorySessionRuleStore

_BASE_URL = "https://tianshu.example.com"


def _settings() -> TianshuSettings:
    bootstrap_token = "bootstrap-token-for-global-read-tests"
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=_BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=_BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=(
            f"sha256:{hashlib.sha256(bootstrap_token.encode()).hexdigest()}"
        ),
    )


def _app(storage: Storage) -> FastAPI:
    settings = _settings()
    app = FastAPI()
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.worker_pool = WorkerPool(max_concurrency=2)
    app.state.lane_manager = LaneManager(max_global_concurrency=2)
    app.state.session_rule_store = InMemorySessionRuleStore()
    app.state.public_webhook_paths = set()
    app.include_router(audit_router, prefix="/api")
    app.include_router(execution_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app


def _issue_pat(app: FastAPI, principal_id: str, *, admin: bool = False) -> dict[str, str]:
    scopes = frozenset({"api", "admin"} if admin else {"api"})
    issued = app.state.auth_service.issue_pat(
        Principal(
            id=principal_id,
            kind=PrincipalKind.HUMAN,
            display_name=principal_id,
            scopes=scopes,
        ),
        label=principal_id,
        scopes=scopes,
    )
    return {"Authorization": f"Bearer {issued.raw_token}"}


def test_global_operational_reads_reject_two_api_pats_and_allow_admin(
    storage: Storage,
) -> None:
    app = _app(storage)
    ordinary_headers = (
        _issue_pat(app, "user:alice"),
        _issue_pat(app, "user:bob"),
    )
    admin_headers = _issue_pat(app, "user:admin", admin=True)
    paths = (
        "/api/audit/stats",
        "/api/audit/network-events",
        "/api/workers",
        "/api/workers/status",
        "/api/policy/session_rules",
    )

    with TestClient(
        app,
        base_url=_BASE_URL,
        client=("127.0.0.1", 41000),
    ) as client:
        denied = [
            client.get(path, headers=headers) for path in paths for headers in ordinary_headers
        ]
        allowed = [client.get(path, headers=admin_headers) for path in paths]

    assert all(response.status_code == 403 for response in denied)
    assert all(response.json()["error"]["code"] == "insufficient_scope" for response in denied)
    assert all(response.status_code == 200 for response in allowed)


def test_session_rule_create_and_revoke_are_system_audited(storage: Storage) -> None:
    app = _app(storage)
    admin_headers = _issue_pat(app, "user:session-rule-admin", admin=True)

    with TestClient(
        app,
        base_url=_BASE_URL,
        client=("127.0.0.1", 41000),
    ) as client:
        created = client.post(
            "/api/policy/session_rules",
            headers=admin_headers,
            json={
                "tool_name": "web_search",
                "scope": "always",
                "reason": "session rule audit regression",
            },
        )
        assert created.status_code == 201
        rule_id = created.json()["data"]["rule_id"]

        revoked = client.delete(
            f"/api/policy/session_rules/{rule_id}",
            headers=admin_headers,
        )
        assert revoked.status_code == 200

    events = [
        event
        for event in storage.list_system_audit()
        if event.action.startswith("policy.session_rule_")
    ]
    assert [event.action for event in events] == [
        "policy.session_rule_created",
        "policy.session_rule_revoked",
    ]
    actor_digest = hashlib.sha256(b"user:session-rule-admin").hexdigest()
    subject_digest = hashlib.sha256(rule_id.encode()).hexdigest()
    assert all(event.actor_digest == actor_digest for event in events)
    assert all(event.subject_kind == "session_rule" for event in events)
    assert all(event.subject_digest == subject_digest for event in events)
    assert all(event.outcome == "succeeded" for event in events)
    assert all(event.reason_code == "policy_allowed" for event in events)
