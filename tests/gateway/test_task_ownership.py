"""Cross-principal task resources are owner-scoped for ordinary API tokens."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.application.edict_detail import EdictDetailQueryService
from tianshu.bus.event_bus import EventBus
from tianshu.config import TianshuSettings
from tianshu.executor.approvals import ApprovalManager
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.edicts_api import edicts_router
from tianshu.gateway.execution_api import execution_router
from tianshu.governance.decision_service import DecisionService
from tianshu.models import Edict, Memorial
from tianshu.models.dag import DAGExecution
from tianshu.models.decision import DecisionKind, RequestDecisionCommand
from tianshu.models.edict import EdictSchedule
from tianshu.models.principal import (
    AuthContext,
    AuthenticationSource,
    ClientKind,
    Principal,
    PrincipalKind,
)
from tianshu.storage import Storage

BASE_URL = "https://tianshu.example.com"


class _Scheduler:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.calls: list[tuple[str, str]] = []

    async def list_jobs(self) -> list[dict]:
        return self.storage.list_scheduler_jobs(statuses=("active", "paused"))

    async def cancel(self, job_id: str) -> bool:
        self.calls.append(("cancel", job_id))
        return True

    async def pause(self, job_id: str) -> bool:
        self.calls.append(("pause", job_id))
        return True

    async def resume(self, job_id: str) -> bool:
        self.calls.append(("resume", job_id))
        return True

    async def run_now(self, job_id: str, *, idempotency_key: str) -> bool:
        self.calls.append(("run-now", job_id))
        assert idempotency_key
        return True

    async def reschedule(self, job_id: str, _schedule: EdictSchedule) -> bool:
        self.calls.append(("reschedule", job_id))
        return True

    def list_job_runs(self, job_id: str, *, limit: int) -> list[dict]:
        self.calls.append(("runs", job_id))
        assert limit > 0
        return []


def _settings(*, security_mode: str = "secure-remote") -> TianshuSettings:
    values: dict[str, object] = {
        "_env_file": None,
        "security_mode": security_mode,
    }
    if security_mode == "secure-remote":
        values.update(
            {
                "public_base_url": BASE_URL,
                "allowed_hosts": "tianshu.example.com",
                "allowed_origins": BASE_URL,
                "trusted_proxy_cidrs": "127.0.0.1/32",
                "auth_bootstrap_token_hash": (
                    "sha256:f507a4c72e9a36ac57fb7c7b0b55c517896c1147e15f3de195d5688499f9c33f"
                ),
            }
        )
    return TianshuSettings(**values)


def _app(storage: Storage, settings: TianshuSettings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.edict_detail_service = EdictDetailQueryService(storage)
    app.state.scheduler = _Scheduler(storage)
    app.state.event_bus = EventBus()
    app.state.decision_service = DecisionService(storage)
    app.state.approval_manager = ApprovalManager(
        event_bus=app.state.event_bus,
        storage=storage,
        decision_service=app.state.decision_service,
    )
    app.state.public_webhook_paths = set()
    app.include_router(edicts_router, prefix="/api")
    app.include_router(execution_router, prefix="/api")
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    return app


def _issue(app: FastAPI, principal_id: str, *, admin: bool = False) -> dict[str, str]:
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


@pytest.fixture
def ownership_api(tmp_path):
    storage = Storage(str(tmp_path / "ownership.db"))
    storage.init_db()
    schedule = EdictSchedule(
        type="once",
        at=datetime.now(UTC) + timedelta(hours=1),
    )
    owner = Edict(id="edict-owner", title="Owner", goal="owner task", submitter="user:owner")
    other = Edict(id="edict-other", title="Other", goal="other task", submitter="user:other")
    legacy = Edict(id="edict-legacy", title="Legacy", goal="legacy task", submitter=None)
    for edict in (owner, other, legacy):
        edict.schedule = schedule
        storage.save_edict(edict)
        storage.save_memorial(Memorial(id=f"memorial-{edict.id}", edict_id=edict.id))
        storage.save_scheduler_job(
            f"job-{edict.id}",
            edict.id,
            "once",
            next_run=schedule.at,
        )
    storage.save_dag_execution(DAGExecution(id="dag-owner", edict_id=owner.id))

    app = _app(storage, _settings())
    decision_requester = AuthContext(
        principal=Principal(
            id="system:outer-loop",
            kind=PrincipalKind.SERVICE,
            display_name="Outer Loop",
            scopes=frozenset(),
        ),
        source=AuthenticationSource.TRUSTED_LOCAL,
        client_kind=ClientKind.SYSTEM,
        correlation_id="test-request",
    )
    for edict in (owner, other, legacy):
        app.state.decision_service.request(
            RequestDecisionCommand(
                kind=DecisionKind.OUTER_LOOP,
                edict_id=edict.id,
                memorial_id=f"memorial-{edict.id}",
                request_key=f"outer-loop:{edict.id}",
                payload={"schema_version": 1},
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
            auth=decision_requester,
        )
    try:
        yield (
            app,
            storage,
            _issue(app, "user:owner"),
            _issue(app, "user:other"),
            _issue(app, "user:admin", admin=True),
        )
    finally:
        storage.close()


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000))


def test_edict_lists_details_and_legacy_rows_follow_one_owner_policy(ownership_api) -> None:
    app, _, owner_headers, other_headers, admin_headers = ownership_api

    with _client(app) as client:
        owner_list = client.get("/api/edicts", headers=owner_headers)
        owner_detail = client.get("/api/edicts/edict-owner/detail", headers=owner_headers)
        hidden_detail = client.get("/api/edicts/edict-owner/detail", headers=other_headers)
        hidden_legacy = client.get("/api/edicts/edict-legacy", headers=owner_headers)
        admin_list = client.get("/api/edicts", headers=admin_headers)
        admin_legacy = client.get("/api/edicts/edict-legacy/detail", headers=admin_headers)

    assert [item["id"] for item in owner_list.json()["data"]] == ["edict-owner"]
    assert owner_list.json()["metadata"]["total"] == 1
    assert owner_detail.status_code == 200
    assert hidden_detail.status_code == hidden_legacy.status_code == 404
    assert {item["id"] for item in admin_list.json()["data"]} == {
        "edict-owner",
        "edict-other",
        "edict-legacy",
    }
    assert admin_legacy.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/api/edicts/edict-owner", {}),
        ("PATCH", "/api/edicts/edict-owner", {"json": {"title": "stolen"}}),
        ("DELETE", "/api/edicts/edict-owner", {}),
        ("POST", "/api/edicts/edict-owner/pause", {}),
        ("POST", "/api/edicts/edict-owner/resume", {}),
        ("POST", "/api/edicts/edict-owner/steer", {"json": {"note": "stolen"}}),
        (
            "POST",
            "/api/edicts/edict-owner/follow-up",
            {
                "headers": {"Idempotency-Key": "other-follow-up"},
                "json": {"instruction": "stolen"},
            },
        ),
        (
            "PATCH",
            "/api/edicts/edict-owner/status",
            {"json": {"status": "cancelled"}},
        ),
        ("GET", "/api/edicts/edict-owner/events", {}),
        ("GET", "/api/edicts/edict-owner/iterations", {}),
        ("GET", "/api/edicts/edict-owner/memorial", {}),
        ("GET", "/api/edicts/edict-owner/memorials", {}),
        ("GET", "/api/edicts/edict-owner/supervision-reports", {}),
        ("GET", "/api/edicts/edict-owner/supervision-report", {}),
        ("GET", "/api/edicts/edict-owner/policy_events", {}),
        (
            "POST",
            "/api/edicts/edict-owner/outer-loop/decide",
            {"json": {"action": "abort"}},
        ),
    ],
)
def test_other_pat_cannot_read_or_mutate_owner_edict(
    ownership_api,
    method: str,
    path: str,
    kwargs: dict,
) -> None:
    app, storage, _, other_headers, _ = ownership_api
    request_headers = {**other_headers, **kwargs.get("headers", {})}
    request_kwargs = {key: value for key, value in kwargs.items() if key != "headers"}

    with _client(app) as client:
        response = client.request(method, path, headers=request_headers, **request_kwargs)

    assert response.status_code == 404
    assert storage.get_edict("edict-owner").title == "Owner"


def test_memorial_dag_and_planner_views_are_principal_scoped(ownership_api) -> None:
    app, _, owner_headers, other_headers, _ = ownership_api

    with _client(app) as client:
        memorials = client.get("/api/memorials", headers=owner_headers)
        hidden_memorial = client.get(
            "/api/memorials/memorial-edict-owner",
            headers=other_headers,
        )
        hidden_dag = client.get("/api/dag/dag-owner", headers=other_headers)
        planner = client.get("/api/planner/stats", headers=owner_headers)
        by_persona = client.get(
            "/api/memorials/by-persona/bingbu",
            headers=owner_headers,
        )
        batch_owned = client.post(
            "/api/edicts/latest-memorials",
            headers=owner_headers,
            json={"edict_ids": ["edict-owner"]},
        )
        batch_hidden = client.post(
            "/api/edicts/latest-memorials",
            headers=other_headers,
            json={"edict_ids": ["edict-owner"]},
        )

    assert [item["edict_id"] for item in memorials.json()["data"]] == ["edict-owner"]
    assert memorials.json()["metadata"]["total"] == 1
    assert hidden_memorial.status_code == hidden_dag.status_code == 404
    assert batch_hidden.status_code == 404
    assert batch_owned.json()["data"]["edict-owner"]["edict_id"] == "edict-owner"
    assert [item["edict_id"] for item in by_persona.json()["data"]] == ["edict-owner"]
    assert by_persona.json()["metadata"]["total"] == 1
    assert planner.json()["data"]["total_edicts"] == 1
    assert [item["edict_id"] for item in planner.json()["data"]["recent_history"]] == [
        "edict-owner"
    ]


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("DELETE", "/api/scheduler/jobs/job-edict-owner", {}),
        ("POST", "/api/scheduler/jobs/job-edict-owner/pause", {}),
        ("POST", "/api/scheduler/jobs/job-edict-owner/resume", {}),
        (
            "POST",
            "/api/scheduler/jobs/job-edict-owner/run-now",
            {"headers": {"Idempotency-Key": "other-run-now"}},
        ),
        (
            "PATCH",
            "/api/scheduler/jobs/job-edict-owner",
            {
                "json": {
                    "schedule": {
                        "type": "interval",
                        "interval_seconds": 60,
                    }
                }
            },
        ),
        ("GET", "/api/scheduler/jobs/job-edict-owner/runs", {}),
    ],
)
def test_other_pat_cannot_control_owner_scheduler_job(
    ownership_api,
    method: str,
    path: str,
    kwargs: dict,
) -> None:
    app, _, _, other_headers, _ = ownership_api
    scheduler: _Scheduler = app.state.scheduler
    headers = {**other_headers, **kwargs.get("headers", {})}
    request_kwargs = {key: value for key, value in kwargs.items() if key != "headers"}

    with _client(app) as client:
        response = client.request(method, path, headers=headers, **request_kwargs)

    assert response.status_code == 404
    assert scheduler.calls == []


def test_scheduler_list_control_and_history_are_owner_scoped(ownership_api) -> None:
    app, _, owner_headers, _, admin_headers = ownership_api
    scheduler: _Scheduler = app.state.scheduler

    with _client(app) as client:
        listed = client.get("/api/scheduler/jobs", headers=owner_headers)
        owner_pause = client.post(
            "/api/scheduler/jobs/job-edict-owner/pause",
            headers=owner_headers,
        )
        admin_list = client.get("/api/scheduler/jobs", headers=admin_headers)

    assert [item["job_id"] for item in listed.json()["data"]] == ["job-edict-owner"]
    assert scheduler.calls == [("pause", "job-edict-owner")]
    assert owner_pause.status_code == 200
    assert {item["job_id"] for item in admin_list.json()["data"]} == {
        "job-edict-owner",
        "job-edict-other",
        "job-edict-legacy",
    }


def test_outer_loop_pending_list_is_owner_scoped(ownership_api) -> None:
    app, _, owner_headers, _, admin_headers = ownership_api

    with _client(app) as client:
        owner_pending = client.get("/api/edicts/outer-loop/pending", headers=owner_headers)
        admin_pending = client.get("/api/edicts/outer-loop/pending", headers=admin_headers)

    assert [item["edict_id"] for item in owner_pending.json()["data"]] == ["edict-owner"]
    assert {item["edict_id"] for item in admin_pending.json()["data"]} == {
        "edict-owner",
        "edict-other",
        "edict-legacy",
    }


def test_trusted_local_owner_can_manage_historical_unattributed_tasks(tmp_path) -> None:
    storage = Storage(str(tmp_path / "trusted-local.db"))
    storage.init_db()
    storage.save_edict(Edict(id="legacy", goal="legacy", submitter=None))
    app = _app(storage, _settings(security_mode="trusted-local"))
    try:
        with TestClient(
            app,
            base_url="http://testserver",
            client=("127.0.0.1", 41000),
        ) as client:
            listed = client.get("/api/edicts")
            loaded = client.get("/api/edicts/legacy")
        assert listed.status_code == loaded.status_code == 200
        assert [item["id"] for item in listed.json()["data"]] == ["legacy"]
    finally:
        storage.close()
