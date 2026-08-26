"""Admin-only CAS contracts for durable evolution policies."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tianshu.gateway.evolution_api as evolution_api_module
from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.evolution_api import evolution_router
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.storage.facade import Storage

_BASE_URL = "https://tianshu.example.com"
_BOOTSTRAP_TOKEN = "evolution-policy-bootstrap-token"


def _app(tmp_path: Path) -> tuple[FastAPI, Storage]:
    settings = TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=_BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=_BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=(
            "sha256:" + hashlib.sha256(_BOOTSTRAP_TOKEN.encode()).hexdigest()
        ),
    )
    storage = Storage(str(tmp_path / "evolution-policy-api.db"))
    storage.init_db()
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.public_webhook_paths = set()
    app.include_router(evolution_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app, storage


def _pat(app: FastAPI, *, principal_id: str, admin: bool) -> dict[str, str]:
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


def _policy_body(
    *,
    kind: str = "skill",
    mode: str = "canary",
    max_canary_basis_points: int = 500,
    expected_version: int | None = None,
) -> dict[str, object]:
    return {
        "kind": kind,
        "mode": mode,
        "max_canary_basis_points": max_canary_basis_points,
        "expected_version": expected_version,
    }


def test_policy_api_is_admin_only_and_exposes_strict_cas_envelopes(tmp_path: Path) -> None:
    app, storage = _app(tmp_path)
    api_headers = _pat(app, principal_id="user:api", admin=False)
    admin_headers = _pat(app, principal_id="user:admin", admin=True)
    path = "/api/evolution/policies/skill:api-contract"
    try:
        with TestClient(
            app,
            base_url=_BASE_URL,
            client=("127.0.0.1", 41000),
        ) as client:
            anonymous = client.get(path)
            api_only = client.get(path, headers=api_headers)
            missing = client.get(path, headers=admin_headers)
            created = client.put(
                path,
                headers=admin_headers,
                json=_policy_body(),
            )
            loaded = client.get(path, headers=admin_headers)
            stale_create_retry = client.put(
                path,
                headers=admin_headers,
                json=_policy_body(),
            )
            wrong_kind = client.put(
                path,
                headers=admin_headers,
                json=_policy_body(kind="persona", expected_version=1),
            )
            updated = client.put(
                path,
                headers=admin_headers,
                json=_policy_body(
                    mode="frozen",
                    max_canary_basis_points=0,
                    expected_version=1,
                ),
            )
            stale_update_retry = client.put(
                path,
                headers=admin_headers,
                json=_policy_body(
                    mode="frozen",
                    max_canary_basis_points=0,
                    expected_version=1,
                ),
            )
            missing_expected_field = client.put(
                "/api/evolution/policies/skill:missing-field",
                headers=admin_headers,
                json={
                    "kind": "skill",
                    "mode": "frozen",
                    "max_canary_basis_points": 0,
                },
            )
            invalid_auto = client.put(
                "/api/evolution/policies/skill:auto",
                headers=admin_headers,
                json=_policy_body(mode="auto", max_canary_basis_points=0),
            )
            invalid_zero_canary = client.put(
                "/api/evolution/policies/skill:zero",
                headers=admin_headers,
                json=_policy_body(max_canary_basis_points=0),
            )

        assert anonymous.status_code == 401
        assert api_only.status_code == 403
        assert api_only.json()["error"]["code"] == "insufficient_scope"
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "evolution_policy_not_found"

        assert created.status_code == 200
        assert set(created.json()) == {"data", "correlation_id"}
        assert "success" not in created.json()
        assert created.json()["data"] == loaded.json()["data"]
        assert created.json()["data"]["version"] == 1
        assert created.json()["correlation_id"] == created.headers["x-correlation-id"]

        assert stale_create_retry.status_code == 409
        assert stale_create_retry.json()["detail"]["code"] == "evolution_policy_version_conflict"
        assert wrong_kind.status_code == 409
        assert wrong_kind.json()["detail"]["code"] == "evolution_policy_kind_conflict"
        assert updated.status_code == 200
        assert updated.json()["data"]["version"] == 2
        assert updated.json()["data"]["mode"] == "frozen"
        assert stale_update_retry.status_code == 409
        assert stale_update_retry.json()["detail"]["code"] == "evolution_policy_version_conflict"
        assert missing_expected_field.status_code == 422
        assert invalid_auto.status_code == 422
        assert invalid_zero_canary.status_code == 422
    finally:
        storage.close()


def test_policy_put_atomically_persists_policy_audit_and_outbox(tmp_path: Path) -> None:
    app, storage = _app(tmp_path)
    headers = _pat(app, principal_id="user:policy-owner", admin=True)
    subject_key = "skill:atomic-policy"
    try:
        with TestClient(
            app,
            base_url=_BASE_URL,
            client=("127.0.0.1", 41000),
        ) as client:
            response = client.put(
                f"/api/evolution/policies/{subject_key}",
                headers=headers,
                json=_policy_body(mode="frozen", max_canary_basis_points=0),
            )

        assert response.status_code == 200
        policy = storage._conn.execute(
            "SELECT * FROM evolution_policies WHERE subject_key=?",
            (subject_key,),
        ).fetchone()
        audit = storage._conn.execute(
            "SELECT * FROM system_audit_events WHERE action='evolution_policy_updated'"
        ).fetchone()
        outbox = storage._conn.execute(
            "SELECT * FROM outbox_events WHERE event_type='evolution_policy_updated'"
        ).fetchone()
        assert policy is not None
        assert audit is not None
        assert audit["actor_digest"] == hashlib.sha256(b"user:policy-owner").hexdigest()
        assert audit["correlation_id"] == response.json()["correlation_id"]
        assert outbox is not None
        assert outbox["correlation_id"] == response.json()["correlation_id"]
    finally:
        storage.close()


def test_policy_list_is_admin_only_ordered_and_uses_the_strict_envelope(
    tmp_path: Path,
) -> None:
    app, storage = _app(tmp_path)
    api_headers = _pat(app, principal_id="user:api", admin=False)
    admin_headers = _pat(app, principal_id="user:admin", admin=True)
    try:
        with TestClient(
            app,
            base_url=_BASE_URL,
            client=("127.0.0.1", 41000),
        ) as client:
            denied = client.get("/api/evolution/policies", headers=api_headers)
            first = client.put(
                "/api/evolution/policies/skill:zeta",
                headers=admin_headers,
                json=_policy_body(mode="manual", max_canary_basis_points=250),
            )
            second = client.put(
                "/api/evolution/policies/skill:alpha",
                headers=admin_headers,
                json=_policy_body(mode="frozen", max_canary_basis_points=0),
            )
            listed = client.get("/api/evolution/policies", headers=admin_headers)

        assert denied.status_code == 403
        assert first.status_code == second.status_code == 200
        assert listed.status_code == 200
        assert set(listed.json()) == {"data", "correlation_id"}
        assert [item["subject_key"] for item in listed.json()["data"]] == [
            "skill:alpha",
            "skill:zeta",
        ]
        route_schema = app.openapi()["paths"]["/api/evolution/policies"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        assert route_schema["$ref"].endswith("EvolutionPolicyListResponseV1")
    finally:
        storage.close()


def test_policy_put_rolls_back_row_and_audit_when_outbox_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, storage = _app(tmp_path)
    headers = _pat(app, principal_id="user:policy-owner", admin=True)
    subject_key = "skill:rollback-policy"

    def fail_outbox(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected outbox failure")

    monkeypatch.setattr(evolution_api_module.OutboxRepository, "add", fail_outbox)
    try:
        with TestClient(
            app,
            base_url=_BASE_URL,
            client=("127.0.0.1", 41000),
            raise_server_exceptions=False,
        ) as client:
            response = client.put(
                f"/api/evolution/policies/{subject_key}",
                headers=headers,
                json=_policy_body(mode="frozen", max_canary_basis_points=0),
            )

        assert response.status_code == 500
        assert (
            storage._conn.execute(
                "SELECT 1 FROM evolution_policies WHERE subject_key=?",
                (subject_key,),
            ).fetchone()
            is None
        )
        assert (
            storage._conn.execute(
                """SELECT 1 FROM system_audit_events
                   WHERE action='evolution_policy_updated'
                     AND subject_digest=?""",
                (hashlib.sha256(subject_key.encode()).hexdigest(),),
            ).fetchone()
            is None
        )
    finally:
        storage.close()
