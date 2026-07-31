"""Admin-only SystemAudit read/export and request-correlation coverage."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.auth_api import auth_router
from tianshu.gateway.estop_api import estop_router
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.models.system_audit import AppendSystemAuditRequest
from tianshu.security.estop import EstopManager
from tianshu.storage import Storage

_ADMIN_TOKEN = "bootstrap-token-for-system-audit-api"
_ADMIN_HEADERS = {"Authorization": f"Bearer {_ADMIN_TOKEN}"}
_BASE_URL = "https://tianshu.example.com"


def _settings() -> TianshuSettings:
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=_BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=_BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=("sha256:" + hashlib.sha256(_ADMIN_TOKEN.encode()).hexdigest()),
    )


def _app(storage: Storage) -> FastAPI:
    from tianshu.gateway.system_audit_api import system_audit_router

    settings = _settings()
    app = FastAPI()
    app.state.settings = settings
    app.state.auth_service = AuthService(storage, settings)
    app.state.estop_manager = EstopManager(storage)
    app.state.storage = storage
    app.state.public_webhook_paths = set()
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(auth_router, prefix="/api")
    app.include_router(estop_router, prefix="/api")
    app.include_router(system_audit_router, prefix="/api")
    return app


def _client(app: FastAPI) -> TestClient:
    return TestClient(
        app,
        base_url=_BASE_URL,
        client=("127.0.0.1", 41000),
    )


def _event_request(*, correlation_id: str = "audit-api-seed") -> AppendSystemAuditRequest:
    return AppendSystemAuditRequest(
        correlation_id=correlation_id,
        actor_digest="a" * 64,
        action="auth.token.issued",
        outcome="succeeded",
        reason_code="policy_allowed",
        subject_kind="auth_token",
        subject_digest="b" * 64,
        metadata={"scope_count": 1, "token_type": "pat"},
    )


def _limited_token(app: FastAPI) -> str:
    issued = app.state.auth_service.issue_pat(
        Principal(
            id="service:api-reader",
            kind=PrincipalKind.SERVICE,
            display_name="API reader",
            scopes=frozenset({"api"}),
        ),
        label="api-only",
        scopes=frozenset({"api"}),
    )
    return issued.raw_token


def test_system_audit_list_and_export_are_admin_only(storage: Storage) -> None:
    storage.append_system_audit(_event_request())
    app = _app(storage)
    limited_token = _limited_token(app)

    with _client(app) as client:
        anonymous_list = client.get("/api/audit/system")
        anonymous_export = client.get("/api/audit/system/export")
        limited_list = client.get(
            "/api/audit/system",
            headers={"Authorization": f"Bearer {limited_token}"},
        )
        limited_export = client.get(
            "/api/audit/system/export",
            headers={"Authorization": f"Bearer {limited_token}"},
        )
        admin_list = client.get("/api/audit/system", headers=_ADMIN_HEADERS)
        admin_export = client.get("/api/audit/system/export", headers=_ADMIN_HEADERS)

    assert (anonymous_list.status_code, anonymous_export.status_code) == (401, 401)
    assert (limited_list.status_code, limited_export.status_code) == (403, 403)
    assert (admin_list.status_code, admin_export.status_code) == (200, 200)
    assert admin_list.json()["items"][0]["correlation_id"] == "audit-api-seed"
    assert admin_export.json()["events"][0]["correlation_id"] == "audit-api-seed"


@pytest.mark.parametrize("limit", [0, 501])
def test_system_audit_list_bounds_limit_to_admin_contract(
    storage: Storage,
    limit: int,
) -> None:
    app = _app(storage)
    with _client(app) as client:
        response = client.get(
            "/api/audit/system",
            headers=_ADMIN_HEADERS,
            params={"limit": limit},
        )

    assert response.status_code == 422


def test_system_audit_read_ignores_forged_actor_query(storage: Storage) -> None:
    sentinel = "FORGED_ACTOR_QUERY_MUST_NOT_BE_ACCEPTED"
    storage.append_system_audit(_event_request())
    app = _app(storage)

    with _client(app) as client:
        response = client.get(
            "/api/audit/system",
            headers=_ADMIN_HEADERS,
            params={"actor_id": sentinel, "display_name": sentinel},
        )

    assert response.status_code == 200
    assert sentinel not in response.text


def test_corrupt_chain_returns_stable_redacted_errors_and_no_partial_export(
    storage: Storage,
) -> None:
    storage.append_system_audit(_event_request(correlation_id="corruption-first"))
    storage.append_system_audit(_event_request(correlation_id="corruption-second"))
    storage._conn.execute("DROP TRIGGER system_audit_events_no_update")
    storage._conn.execute(
        "UPDATE system_audit_events SET event_hash = ? WHERE sequence = 1",
        ("f" * 64,),
    )
    storage._conn.commit()
    app = _app(storage)

    with _client(app) as client:
        listed = client.get("/api/audit/system", headers=_ADMIN_HEADERS)
        exported = client.get("/api/audit/system/export", headers=_ADMIN_HEADERS)

    for response in (listed, exported):
        assert response.status_code == 409
        assert response.json() == {
            "detail": {
                "code": "system_audit_integrity_failed",
                "reason_code": "event_hash_mismatch",
                "failure_sequence": 1,
            }
        }
        assert "system audit integrity check failed" not in response.text.lower()
        assert "events" not in response.text.lower()
        assert "corruption-first" not in response.text
        assert "corruption-second" not in response.text


def test_denied_session_uses_request_correlation_and_never_persists_secret(
    storage: Storage,
) -> None:
    secret_sentinel = "SECRET_SESSION_INPUT_MUST_NOT_REACH_AUDIT"
    app = _app(storage)

    with _client(app) as client:
        denied = client.post("/api/auth/session", json={"token": secret_sentinel})
        audit_response = client.get("/api/audit/system", headers=_ADMIN_HEADERS)

    assert denied.status_code == 401
    assert audit_response.status_code == 200
    denied_events = [
        event
        for event in audit_response.json()["items"]
        if event["action"] == "auth.session.denied"
    ]
    assert len(denied_events) == 1
    assert denied_events[0]["correlation_id"] == denied.headers["x-correlation-id"]
    assert secret_sentinel not in audit_response.text
    assert secret_sentinel not in denied.text

    database_path = Path(storage._conn.execute("PRAGMA database_list").fetchone()["file"])
    for path in (database_path, database_path.with_name(f"{database_path.name}-wal")):
        if path.exists():
            assert secret_sentinel.encode() not in path.read_bytes()


def test_estop_mutations_use_request_actor_and_correlation_without_raw_fields(
    storage: Storage,
) -> None:
    app = _app(storage)

    with _client(app) as client:
        engaged = client.post(
            "/api/estop/engage",
            headers=_ADMIN_HEADERS,
            json={"kill_all": True, "reason": "security drill"},
        )
        resumed = client.post(
            "/api/estop/resume",
            headers=_ADMIN_HEADERS,
            json={"all_clear": True},
        )
        audit_response = client.get("/api/audit/system", headers=_ADMIN_HEADERS)

    assert (engaged.status_code, resumed.status_code, audit_response.status_code) == (200, 200, 200)
    estop_events = [
        event for event in audit_response.json()["items"] if event["action"].startswith("estop.")
    ]
    assert [event["action"] for event in estop_events] == ["estop.engaged", "estop.resumed"]
    assert [event["correlation_id"] for event in estop_events] == [
        engaged.headers["x-correlation-id"],
        resumed.headers["x-correlation-id"],
    ]
    for event in estop_events:
        assert event["actor_digest"] == hashlib.sha256(b"user:owner").hexdigest()
        assert "user:owner" not in repr(event)
        assert "Owner" not in repr(event)
        assert "127.0.0.1" not in repr(event)
        assert "security drill" not in repr(event)


def test_estop_mutations_reject_api_only_pat(storage: Storage) -> None:
    app = _app(storage)
    limited_headers = {"Authorization": f"Bearer {_limited_token(app)}"}

    with _client(app) as client:
        engaged = client.post(
            "/api/estop/engage",
            headers=limited_headers,
            json={"kill_all": True, "reason": "must not apply"},
        )
        resumed = client.post(
            "/api/estop/resume",
            headers=limited_headers,
            json={"all_clear": True},
        )
        status = client.get("/api/estop", headers=limited_headers)

    assert engaged.status_code == resumed.status_code == 403
    assert status.status_code == 200
    assert status.json()["data"]["engaged"] is False
