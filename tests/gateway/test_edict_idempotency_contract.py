"""HTTP Edict ingress exposes the durable idempotency contract."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.application.edicts import EdictApplicationService, SubmitEdictResult
from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.gateway.auth import SecurityBoundaryMiddleware
from tianshu.gateway.edicts_api import edicts_router
from tianshu.models import Memorial


def _app(storage, config_manager) -> FastAPI:
    settings = TianshuSettings(_env_file=None, host="127.0.0.1")
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.event_bus = EventBus()
    app.state.config_manager = config_manager
    app.state.persona_loader = None
    app.state.edict_application_service = EdictApplicationService(storage)
    app.state.public_webhook_paths = set()
    app.include_router(edicts_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app


def test_http_new_retry_conflict_and_header_body_mismatch(storage, config_manager) -> None:
    app = _app(storage, config_manager)
    headers = {"Idempotency-Key": "http-request-1"}

    with TestClient(app, client=("127.0.0.1", 41000)) as client:
        first = client.post("/api/edicts", headers=headers, json={"goal": "same request"})
        retry = client.post("/api/edicts", headers=headers, json={"goal": "same request"})
        conflict = client.post(
            "/api/edicts",
            headers=headers,
            json={"goal": "different request"},
        )
        mismatch = client.post(
            "/api/edicts",
            headers={"Idempotency-Key": "header-key"},
            json={"goal": "mismatch", "idempotency_key": "body-key"},
        )

    assert first.status_code == 202
    first_metadata = first.json()["metadata"]
    assert first_metadata == {
        "deduplicated": False,
        "idempotency_key": "http-request-1",
        "request_hash": first_metadata["request_hash"],
        "event_id": first_metadata["event_id"],
        "memorial_id": first_metadata["memorial_id"],
    }
    assert len(first_metadata["request_hash"]) == 64
    assert retry.status_code == 200
    assert retry.json()["metadata"] == {
        **first_metadata,
        "deduplicated": True,
    }
    assert retry.json()["data"]["id"] == first.json()["data"]["id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "idempotency_conflict",
        "idempotency_key": "http-request-1",
    }
    assert mismatch.status_code == 422
    assert mismatch.json()["detail"]["code"] == "idempotency_key_mismatch"


@pytest.mark.parametrize(
    ("headers", "payload", "expected_code", "expected_source"),
    [
        ({}, {"goal": "missing"}, "idempotency_key_required", None),
        (
            {"Idempotency-Key": ""},
            {"goal": "empty header"},
            "invalid_idempotency_key",
            "header",
        ),
        (
            {"Idempotency-Key": "x" * 201},
            {"goal": "long header"},
            "invalid_idempotency_key",
            "header",
        ),
        (
            {},
            {"goal": "empty body", "idempotency_key": ""},
            "invalid_idempotency_key",
            "body",
        ),
        (
            {},
            {"goal": "long body", "idempotency_key": "x" * 201},
            "invalid_idempotency_key",
            "body",
        ),
        (
            {},
            {"goal": "control body", "idempotency_key": "bad\u0001key"},
            "invalid_idempotency_key",
            "body",
        ),
    ],
)
def test_http_rejects_missing_or_invalid_idempotency_key(
    storage,
    config_manager,
    headers,
    payload,
    expected_code,
    expected_source,
) -> None:
    app = _app(storage, config_manager)

    with TestClient(
        app,
        client=("127.0.0.1", 41000),
        raise_server_exceptions=False,
    ) as client:
        response = client.post("/api/edicts", headers=headers, json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == expected_code
    if expected_source is not None:
        assert response.json()["detail"]["source"] == expected_source


def test_http_body_key_remains_a_compatible_stable_source(storage, config_manager) -> None:
    app = _app(storage, config_manager)
    payload = {"goal": "legacy web", "idempotency_key": "body-request-1"}

    with TestClient(app, client=("127.0.0.1", 41000)) as client:
        first = client.post("/api/edicts", json=payload)
        retry = client.post("/api/edicts", json=payload)

    assert first.status_code == 202
    assert retry.status_code == 200
    assert first.json()["metadata"]["idempotency_key"] == "body-request-1"
    assert first.json()["metadata"]["deduplicated"] is False
    assert retry.json()["metadata"] == {
        **first.json()["metadata"],
        "deduplicated": True,
    }


def test_http_rejects_invalid_allowed_path_without_partial_submission(
    storage,
    config_manager,
) -> None:
    app = _app(storage, config_manager)

    with TestClient(app, client=("127.0.0.1", 41000)) as client:
        response = client.post(
            "/api/edicts",
            headers={"Idempotency-Key": "invalid-allowed-path"},
            json={
                "goal": "reject dangerous path",
                "runtime": {"policy_profile": {"allowed_paths": ["/**"]}},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_allowed_path_glob",
        "path_glob": "/**",
        "message": "首段不能是通配符，会放行整个文件系统",
    }
    assert {
        table: storage._conn.execute(  # noqa: SLF001 - HTTP atomicity proof
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        for table in (
            "edicts",
            "requested_governance_contracts",
            "memorials",
            "outbox_events",
            "submission_idempotency",
        )
    } == dict.fromkeys(
        (
            "edicts",
            "requested_governance_contracts",
            "memorials",
            "outbox_events",
            "submission_idempotency",
        ),
        0,
    )


def test_http_idempotency_precedes_allowed_path_admission(storage, config_manager) -> None:
    app = _app(storage, config_manager)
    headers = {"Idempotency-Key": "allowed-path-idempotency"}
    valid_payload = {
        "goal": "stable path request",
        "runtime": {"policy_profile": {"allowed_paths": ["/tmp/shared/**"]}},
    }
    invalid_conflict_payload = {
        "goal": "changed path request",
        "runtime": {"policy_profile": {"allowed_paths": ["/**"]}},
    }

    with TestClient(app, client=("127.0.0.1", 41000)) as client:
        first = client.post("/api/edicts", headers=headers, json=valid_payload)
        replay = client.post("/api/edicts", headers=headers, json=valid_payload)
        conflict = client.post(
            "/api/edicts",
            headers=headers,
            json=invalid_conflict_payload,
        )

    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["metadata"]["deduplicated"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "idempotency_conflict",
        "idempotency_key": "allowed-path-idempotency",
    }
    assert {
        table: storage._conn.execute(  # noqa: SLF001 - idempotency durability proof
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        for table in (
            "edicts",
            "requested_governance_contracts",
            "memorials",
            "outbox_events",
            "submission_idempotency",
        )
    } == dict.fromkeys(
        (
            "edicts",
            "requested_governance_contracts",
            "memorials",
            "outbox_events",
            "submission_idempotency",
        ),
        1,
    )


def test_http_constructs_one_command_and_calls_application_service_once(
    storage,
    config_manager,
) -> None:
    app = _app(storage, config_manager)
    calls = []

    class RecordingService:
        def submit(self, command, **kwargs):
            calls.append((command, kwargs))
            return SubmitEdictResult(
                edict=command.edict,
                memorial=Memorial(edict_id=command.edict.id),
                event_id="http-recording-event",
                request_hash="a" * 64,
                deduplicated=False,
            )

    app.state.edict_application_service = RecordingService()

    with TestClient(app, client=("127.0.0.1", 41000)) as client:
        response = client.post(
            "/api/edicts",
            headers={"Idempotency-Key": "http-once-1"},
            json={"goal": "one service call"},
        )

    assert response.status_code == 202
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command.idempotency_key == "http-once-1"
    assert kwargs["correlation_id"]
