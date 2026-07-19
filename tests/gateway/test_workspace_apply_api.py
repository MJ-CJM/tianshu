"""Workspace REST surface must preserve apply authorization and token secrecy."""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.config import TianshuSettings
from tianshu.executor.workspace_service import WorkspaceApplyError
from tianshu.gateway.auth import SecurityBoundaryMiddleware
from tianshu.models.principal import AuthContext, Principal

_RAW_TOKEN = "workspace-apply-secret-once"
_HASH = "a" * 64
_OID = "b" * 40
_CREATED_AT = "2026-07-12T00:00:00Z"


class _Record:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return copy.deepcopy(self._payload)


class _WorkspaceApplyError(WorkspaceApplyError):
    pass


class _ForeignCodedError(RuntimeError):
    code = "token_invalid"


def _status_record() -> _Record:
    return _Record(
        {
            "schema_version": "1",
            "run_id": "run-1",
            "memorial_status": "completed",
            "effective_contract_hash": _HASH,
            "lease": {
                "id": "lease-1",
                "state": "active",
                "apply_mode": "governed",
                "base_revision": _OID,
                "source_root": "/private/source-root",
                "staging_root": "/private/staging-root",
                "source_git_dir": "/private/source-root/.git",
            },
            "restore_point": {
                "id": "restore-1",
                "base_revision": _OID,
                "created_at": _CREATED_AT,
                "source_root": "/private/source-root",
            },
            "change_set": {
                "id": "changes-1",
                "sequence": 1,
                "changes": [{"kind": "modify", "old_path": "old.txt", "new_path": "new.txt"}],
                "created_at": _CREATED_AT,
            },
            "latest_decision": {
                "id": "decision-old",
                "state": "pending",
                "expires_at": _CREATED_AT,
                "token_hash": "must-not-leak-token-hash",
                "principal_digest": "must-not-leak-principal-digest",
            },
            "latest_receipt": None,
            "host_crash_gap": False,
        }
    )


def _changes_record() -> _Record:
    return _Record(
        {
            "schema_version": "1",
            "id": "changes-1",
            "lease_id": "lease-1",
            "restore_point_id": "restore-1",
            "source_repository_id": "private-repository-id",
            "base_revision": _OID,
            "sequence": 1,
            "changes": [
                {
                    "schema_version": "1",
                    "kind": "modify",
                    "old_path": "notes.txt",
                    "new_path": "notes.txt",
                    "old_oid": "c" * 40,
                    "new_oid": "d" * 40,
                    "old_mode": "100644",
                    "new_mode": "100644",
                    "old_size": 3,
                    "new_size": 7,
                    "binary": False,
                }
            ],
            "created_at": _CREATED_AT,
        }
    )


def _decision_record() -> _Record:
    return _Record(
        {
            "schema_version": "1",
            "id": "decision-1",
            "lease_id": "lease-1",
            "change_set_id": "changes-1",
            "change_set_hash": _HASH,
            "base_revision": _OID,
            "apply_scope": "workspace",
            "reason": "reviewed",
            "state": "pending",
            "expires_at": _CREATED_AT,
            "created_at": _CREATED_AT,
            "source_root": "/private/source-root",
            "token_hash": "must-not-leak-token-hash",
            "principal_digest": "must-not-leak-principal-digest",
        }
    )


def _receipt_record() -> _Record:
    return _Record(
        {
            "schema_version": "1",
            "id": "receipt-1",
            "decision_id": "decision-1",
            "lease_id": "lease-1",
            "change_set_id": "changes-1",
            "change_set_hash": _HASH,
            "outcome": "succeeded",
            "pre_source_head": _OID,
            "post_source_head": "e" * 40,
            "rollback_status": "not_required",
            "failure_code": None,
            "detail": "/private/source-root must not leak",
            "evidence": ["/private/staging-root must not leak"],
            "created_at": _CREATED_AT,
        }
    )


class _WorkspaceService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.decision_error: Exception | None = None
        self.apply_error: Exception | None = None

    async def get_run_status(self, run_id: str) -> _Record:
        self.calls.append(("status", run_id))
        return _status_record()

    async def get_run_changes(self, run_id: str) -> _Record:
        self.calls.append(("changes", run_id))
        return _changes_record()

    async def issue_apply_decision(
        self,
        run_id: str,
        principal: Principal,
        reason: str,
        ttl: timedelta,
    ) -> tuple[_Record, str]:
        self.calls.append(("decision", run_id, principal, reason, ttl))
        if self.decision_error is not None:
            raise self.decision_error
        return _decision_record(), _RAW_TOKEN

    async def apply(
        self,
        run_id: str,
        decision_id: str,
        token: str,
        principal: Principal,
    ) -> _Record:
        self.calls.append(("apply", run_id, decision_id, token, principal))
        if self.apply_error is not None:
            raise self.apply_error
        return _receipt_record()


class _AuthService:
    _SCOPES = {
        "api-only": frozenset({"api"}),
        "workspace-only": frozenset({"workspace:apply"}),
        "api-and-workspace": frozenset({"api", "workspace:apply"}),
    }

    def authenticate_token(self, raw_token: str, **kwargs: Any) -> AuthContext | None:
        scopes = self._SCOPES.get(raw_token)
        if scopes is None:
            return None
        return AuthContext(
            principal=Principal(
                id=f"service:{raw_token}",
                kind="service",
                display_name=raw_token,
                scopes=scopes,
            ),
            source=kwargs["source"],
            credential_id=raw_token,
            client_kind=kwargs["client_kind"],
            correlation_id=kwargs["correlation_id"],
            remote_addr=kwargs.get("remote_addr"),
        )


def _settings(*, secure: bool) -> TianshuSettings:
    if not secure:
        return TianshuSettings(_env_file=None)
    return TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url="https://tianshu.example.com",
        allowed_hosts="tianshu.example.com",
        allowed_origins="https://tianshu.example.com",
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash=f"sha256:{'0' * 64}",
    )


def _app(service: _WorkspaceService, *, secure: bool) -> FastAPI:
    from tianshu.gateway.workspace_api import workspace_router

    settings = _settings(secure=secure)
    app = FastAPI()
    app.state.settings = settings
    app.state.workspace_service = service
    app.state.auth_service = _AuthService()
    app.state.public_webhook_paths = set()
    app.include_router(workspace_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app


def _client(app: FastAPI, *, secure: bool) -> TestClient:
    return TestClient(
        app,
        base_url=("https://tianshu.example.com" if secure else "http://localhost"),
        client=("127.0.0.1", 41000),
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_workspace_router_exposes_exactly_four_routes() -> None:
    app = _app(_WorkspaceService(), secure=False)

    routes = {
        (route.path, method)
        for route in app.routes
        if route.path.startswith("/api/workspace-runs")
        for method in route.methods or ()
    }

    assert routes == {
        ("/api/workspace-runs/{run_id}/status", "GET"),
        ("/api/workspace-runs/{run_id}/changes", "GET"),
        ("/api/workspace-runs/{run_id}/apply-decisions", "POST"),
        ("/api/workspace-runs/{run_id}/apply", "POST"),
    }


def test_create_app_registers_workspace_router() -> None:
    from tianshu.app import create_app

    app = create_app(TianshuSettings(_env_file=None))

    assert any(route.path == "/api/workspace-runs/{run_id}/status" for route in app.routes)


def test_workspace_run_bare_route_is_not_an_alias_for_status() -> None:
    service = _WorkspaceService()

    with _client(_app(service, secure=False), secure=False) as client:
        response = client.get("/api/workspace-runs/run-1")

    assert response.status_code == 404
    assert not service.calls


def test_trusted_local_four_routes_use_auth_context_principal_and_safe_views() -> None:
    service = _WorkspaceService()
    with _client(_app(service, secure=False), secure=False) as client:
        status = client.get("/api/workspace-runs/run-1/status")
        changes = client.get("/api/workspace-runs/run-1/changes")
        decision = client.post(
            "/api/workspace-runs/run-1/apply-decisions",
            json={"reason": "reviewed", "ttl_seconds": 120},
        )
        applied = client.post(
            "/api/workspace-runs/run-1/apply",
            json={"decision_id": "decision-1", "token": _RAW_TOKEN},
        )

    assert [response.status_code for response in (status, changes, decision, applied)] == [
        200,
        200,
        201,
        200,
    ]
    assert status.json()["data"]["run_id"] == "run-1"
    assert changes.json()["data"]["changes"][0]["new_path"] == "notes.txt"
    assert decision.json()["data"]["decision"]["id"] == "decision-1"
    assert decision.json()["data"]["token"] == _RAW_TOKEN
    assert decision.text.count(_RAW_TOKEN) == 1
    assert applied.json()["data"]["receipt"]["id"] == "receipt-1"
    assert _RAW_TOKEN not in applied.text
    assert service.calls[2][2].principal.id == "local:owner"
    assert service.calls[2][4] == timedelta(seconds=120)
    assert service.calls[3][4].principal.id == "local:owner"
    serialized = "\n".join(response.text for response in (status, changes, applied))
    for forbidden in (
        "/private/source-root",
        "/private/staging-root",
        "must-not-leak-token-hash",
        "must-not-leak-principal-digest",
        "private-repository-id",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("method", "path", "body", "allowed_tokens"),
    [
        (
            "GET",
            "/api/workspace-runs/run-1/status",
            None,
            {"api-only", "api-and-workspace"},
        ),
        (
            "GET",
            "/api/workspace-runs/run-1/changes",
            None,
            {"api-only", "api-and-workspace"},
        ),
        (
            "POST",
            "/api/workspace-runs/run-1/apply-decisions",
            {"reason": "reviewed", "ttl_seconds": 120},
            {"api-and-workspace"},
        ),
        (
            "POST",
            "/api/workspace-runs/run-1/apply",
            {"decision_id": "decision-1", "token": _RAW_TOKEN},
            {"api-and-workspace"},
        ),
    ],
    ids=("status-get", "changes-get", "decision-post", "apply-post"),
)
def test_secure_remote_enforces_the_four_route_scope_matrix(
    method: str,
    path: str,
    body: dict[str, object] | None,
    allowed_tokens: set[str],
) -> None:
    service = _WorkspaceService()
    with _client(_app(service, secure=True), secure=True) as client:
        responses = {
            token: client.request(method, path, headers=_auth(token), json=body)
            for token in ("api-only", "workspace-only", "api-and-workspace")
        }
        anonymous = client.request(method, path, json=body)

    assert anonymous.status_code == 401
    for token, response in responses.items():
        if token in allowed_tokens:
            assert response.status_code in {200, 201}
        else:
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "insufficient_scope"
        assert _RAW_TOKEN not in response.text or response.status_code == 201


def test_apply_rejects_body_actor_and_maps_errors_without_token_or_log_leak(caplog) -> None:
    service = _WorkspaceService()
    service.apply_error = _WorkspaceApplyError(
        "token_invalid",
        f"unsafe internal detail contained {_RAW_TOKEN} and /private/source-root",
    )
    with _client(_app(service, secure=False), secure=False) as client:
        forged = client.post(
            "/api/workspace-runs/run-1/apply",
            json={
                "decision_id": "decision-1",
                "token": _RAW_TOKEN,
                "actor": "forged:actor",
            },
        )
        rejected = client.post(
            "/api/workspace-runs/run-1/apply",
            json={"decision_id": "decision-1", "token": _RAW_TOKEN},
        )

    assert forged.status_code == 422
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == {
        "code": "token_invalid",
        "message": "apply authorization rejected",
    }
    assert _RAW_TOKEN not in forged.text
    assert _RAW_TOKEN not in rejected.text
    assert _RAW_TOKEN not in caplog.text
    assert "/private/source-root" not in rejected.text
    assert _RAW_TOKEN not in str(rejected.request.url)
    assert rejected.request.url.query == b""
    assert len([call for call in service.calls if call[0] == "apply"]) == 1


@pytest.mark.parametrize(
    ("code", "expected_status", "expected_message"),
    [
        ("scope_denied", 403, "workspace apply scope required"),
        ("capability_not_enforced", 409, "governed apply capability is not enforced"),
    ],
)
def test_new_service_authority_errors_have_fixed_safe_mapping(
    code: str,
    expected_status: int,
    expected_message: str,
) -> None:
    service = _WorkspaceService()
    service.decision_error = _WorkspaceApplyError(code, "unsafe internal authority detail")

    with _client(_app(service, secure=False), secure=False) as client:
        response = client.post(
            "/api/workspace-runs/run-1/apply-decisions",
            json={"reason": "reviewed", "ttl_seconds": 120},
        )

    assert response.status_code == expected_status
    assert response.json()["detail"] == {
        "code": code,
        "message": expected_message,
    }
    assert "unsafe internal authority detail" not in response.text


@pytest.mark.parametrize("reason", ["approved\nforged", "approved\rforged"])
def test_apply_decision_reason_rejects_line_breaks_with_fixed_error(reason: str) -> None:
    service = _WorkspaceService()

    with _client(_app(service, secure=False), secure=False) as client:
        response = client.post(
            "/api/workspace-runs/run-1/apply-decisions",
            json={"reason": reason, "ttl_seconds": 120},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_apply_decision_request",
        "message": "invalid apply decision request",
    }
    assert not [call for call in service.calls if call[0] == "decision"]


@pytest.mark.parametrize(
    ("payload", "secret"),
    [
        (
            {"reason": "reviewed", "token": "decision-body-secret-must-not-reflect"},
            "decision-body-secret-must-not-reflect",
        ),
        (
            {"reason": "oversized-secret-" + "x" * 2_000},
            "oversized-secret-",
        ),
    ],
    ids=("extra-token", "oversized-reason"),
)
def test_invalid_apply_decision_body_has_fixed_non_reflective_error(
    payload: dict[str, object],
    secret: str,
    caplog,
) -> None:
    service = _WorkspaceService()

    with _client(_app(service, secure=False), secure=False) as client:
        response = client.post(
            "/api/workspace-runs/run-1/apply-decisions",
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_apply_decision_request",
        "message": "invalid apply decision request",
    }
    assert secret not in response.text
    assert secret not in caplog.text
    assert not [call for call in service.calls if call[0] == "decision"]


@pytest.mark.parametrize(
    ("ttl_seconds", "expected_status", "expected_calls"),
    [(3_600, 201, 1), (3_601, 422, 0)],
)
def test_apply_decision_ttl_surface_has_a_one_hour_boundary(
    ttl_seconds: int,
    expected_status: int,
    expected_calls: int,
) -> None:
    service = _WorkspaceService()

    with _client(_app(service, secure=False), secure=False) as client:
        response = client.post(
            "/api/workspace-runs/run-1/apply-decisions",
            json={"reason": "reviewed", "ttl_seconds": ttl_seconds},
        )

    assert response.status_code == expected_status
    assert len([call for call in service.calls if call[0] == "decision"]) == expected_calls


@pytest.mark.parametrize(
    "unsafe_token",
    [
        "workspace-oversized-secret-" + "x" * 4_096,
        [_RAW_TOKEN],
    ],
)
def test_invalid_apply_token_shape_returns_fixed_error_without_reflection(
    unsafe_token: object,
    caplog,
) -> None:
    service = _WorkspaceService()
    with _client(_app(service, secure=False), secure=False) as client:
        response = client.post(
            "/api/workspace-runs/run-1/apply",
            json={"decision_id": "decision-1", "token": unsafe_token},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_apply_request",
        "message": "invalid apply request",
    }
    serialized_token = unsafe_token if isinstance(unsafe_token, str) else _RAW_TOKEN
    assert serialized_token not in response.text
    assert serialized_token not in caplog.text
    assert not [call for call in service.calls if call[0] == "apply"]


def test_invalid_apply_json_returns_fixed_error_without_reflection(caplog) -> None:
    service = _WorkspaceService()
    unsafe_body = f'{{"decision_id":"decision-1","token":"{_RAW_TOKEN}"'
    with _client(_app(service, secure=False), secure=False) as client:
        response = client.post(
            "/api/workspace-runs/run-1/apply",
            content=unsafe_body,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_apply_request",
        "message": "invalid apply request",
    }
    assert _RAW_TOKEN not in response.text
    assert _RAW_TOKEN not in caplog.text
    assert not [call for call in service.calls if call[0] == "apply"]


def test_foreign_exception_with_code_is_not_misclassified() -> None:
    service = _WorkspaceService()
    service.apply_error = _ForeignCodedError("foreign programming error")

    with (
        _client(_app(service, secure=False), secure=False) as client,
        pytest.raises(_ForeignCodedError, match="foreign programming error"),
    ):
        client.post(
            "/api/workspace-runs/run-1/apply",
            json={"decision_id": "decision-1", "token": _RAW_TOKEN},
        )


def test_unknown_workspace_error_code_is_not_reflected_or_swallowed() -> None:
    service = _WorkspaceService()
    service.apply_error = _WorkspaceApplyError("future_unsafe_code", "internal detail")

    with (
        _client(_app(service, secure=False), secure=False) as client,
        pytest.raises(_WorkspaceApplyError, match="internal detail"),
    ):
        client.post(
            "/api/workspace-runs/run-1/apply",
            json={"decision_id": "decision-1", "token": _RAW_TOKEN},
        )


def test_auth_scope_catalog_accepts_workspace_apply_tokens(storage) -> None:
    from tianshu.gateway.auth import ALL_AUTH_SCOPES, AuthService

    principal = Principal(
        id="service:workspace",
        kind="service",
        display_name="Workspace automation",
        scopes=frozenset({"api", "workspace:apply"}),
    )

    issued = AuthService(storage, _settings(secure=False)).issue_pat(
        principal,
        label="workspace",
        scopes=principal.scopes,
    )

    assert "workspace:apply" in ALL_AUTH_SCOPES
    assert issued.metadata.scopes == frozenset({"api", "workspace:apply"})
