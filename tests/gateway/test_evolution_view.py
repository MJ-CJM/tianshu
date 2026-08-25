from __future__ import annotations

import hashlib
import importlib
import inspect
from typing import Any, get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tianshu.app import create_app
from tianshu.config import TianshuSettings
from tianshu.gateway.auth import AuthService, SecurityBoundaryMiddleware
from tianshu.gateway.evolution_api import RunEvolutionAssignmentResponseV1
from tianshu.models import Edict, Memorial
from tianshu.models.canonical import canonical_sha256
from tianshu.models.principal import Principal, PrincipalKind
from tianshu.models.system_snapshot import SystemSnapshotV1
from tianshu.storage import Storage
from tianshu.storage.system_snapshot_repo import SystemSnapshotRepository
from tianshu.universe.router import ChallengerRouter

TOKEN = "evolution-view-bootstrap-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE_URL = "https://tianshu.example.com"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
NOT_ENABLED_REASON = "s5_governed_evolution_not_enabled"
S5_CANDIDATE_LIFECYCLES = (
    "proposed",
    "staged",
    "evaluating",
    "blocked",
    "ready",
    "canary",
    "promoted",
    "rejected",
    "rollback_pending",
    "rolled_back",
    "archived",
)


def _symbols() -> dict[str, Any]:
    try:
        models = importlib.import_module("tianshu.models.evolution_view")
        service = importlib.import_module("tianshu.application.evolution_view")
        gateway = importlib.import_module("tianshu.gateway.evolution_api")
    except ModuleNotFoundError:
        pytest.fail("Evolution Center read contract is not implemented")
    return {
        "Candidate": models.EvolutionCandidateSummaryV1,
        "Gate": models.EvolutionGateSummaryV1,
        "Routing": models.EvolutionRoutingSummaryV1,
        "Snapshot": models.EvolutionCenterSnapshotV1,
        "Service": service.EvolutionCenterQueryService,
        "Unavailable": service.EvolutionCenterUnavailable,
        "router": gateway.evolution_router,
    }


def _future_fixture(
    symbols: dict[str, Any],
    *,
    status: str = "enabled",
    lifecycle: str = "canary",
):
    gate = symbols["Gate"](
        code="minimum_samples",
        status="failed",
        blocking=True,
        current=18,
        required=50,
        evidence_bundle_id="evidence:gate-samples",
        evidence_hash=HASH_B,
    )
    candidate = symbols["Candidate"](
        candidate_id="candidate-skill-7",
        kind="skill",
        version=7,
        lifecycle=lifecycle,
        artifact_hash=HASH_A,
        promotion_allowed=False,
        rollback_state="ready",
        gates=(gate,),
    )
    routing = symbols["Routing"](
        candidate_id=candidate.candidate_id,
        routing_version=3,
        allocation_percent=10,
        champion_assignment_count=82,
        challenger_assignment_count=18,
    )
    return symbols["Snapshot"](
        status=status,
        reason_code="minimum_samples_blocking",
        candidates=(candidate,),
        routing=(routing,),
        last_gate_hash=HASH_C,
    )


@pytest.mark.parametrize("lifecycle", ["proposed", "blocked"])
def test_future_fixture_accepts_pre_canary_s5_lifecycles(lifecycle: str) -> None:
    snapshot = _future_fixture(_symbols(), lifecycle=lifecycle)

    assert snapshot.candidates[0].lifecycle == lifecycle


def test_read_lifecycle_is_the_exact_s5_cross_handoff_contract() -> None:
    symbols = _symbols()
    lifecycle_annotation = symbols["Candidate"].model_fields["lifecycle"].annotation

    assert get_args(lifecycle_annotation) == S5_CANDIDATE_LIFECYCLES
    with pytest.raises(ValidationError, match="lifecycle"):
        symbols["Candidate"](
            **{
                **_future_fixture(symbols).candidates[0].model_dump(mode="python"),
                "lifecycle": "draft",
            }
        )


def test_contract_forbids_fabricated_pre_s5_data() -> None:
    symbols = _symbols()
    snapshot = symbols["Snapshot"](
        status="not_enabled",
        reason_code=NOT_ENABLED_REASON,
        candidates=(),
        routing=(),
        last_gate_hash=None,
    )

    assert snapshot.model_dump(mode="json") == {
        "schema_version": 1,
        "status": "not_enabled",
        "reason_code": NOT_ENABLED_REASON,
        "candidates": [],
        "routing": [],
        "last_gate_hash": None,
    }
    with pytest.raises(ValidationError, match="not_enabled"):
        symbols["Snapshot"](
            status="not_enabled",
            reason_code=NOT_ENABLED_REASON,
            candidates=_future_fixture(symbols).candidates,
            routing=(),
            last_gate_hash=None,
        )
    with pytest.raises(ValidationError, match="not_enabled"):
        symbols["Snapshot"](
            status="not_enabled",
            reason_code=NOT_ENABLED_REASON,
            candidates=(),
            routing=_future_fixture(symbols).routing,
            last_gate_hash=HASH_C,
        )


@pytest.mark.parametrize("status", ["enabled", "degraded"])
def test_future_fixture_shapes_preserve_gate_routing_and_rollback_truth(status: str) -> None:
    snapshot = _future_fixture(_symbols(), status=status)

    assert snapshot.candidates[0].gates[0].model_dump(mode="json") == {
        "code": "minimum_samples",
        "status": "failed",
        "blocking": True,
        "current": 18.0,
        "required": 50.0,
        "evidence_bundle_id": "evidence:gate-samples",
        "evidence_hash": HASH_B,
    }
    assert snapshot.candidates[0].rollback_state == "ready"
    assert snapshot.routing[0].champion_assignment_count == 82
    assert snapshot.routing[0].challenger_assignment_count == 18
    assert snapshot.last_gate_hash == HASH_C


def test_contract_is_strict_and_blocks_inconsistent_future_claims() -> None:
    symbols = _symbols()
    fixture = _future_fixture(symbols)
    with pytest.raises(ValidationError):
        symbols["Snapshot"].model_validate(
            {**fixture.model_dump(mode="python"), "invented_metric": 99}
        )
    with pytest.raises(ValidationError, match="promotion"):
        symbols["Candidate"](
            **{
                **fixture.candidates[0].model_dump(mode="python"),
                "promotion_allowed": True,
            }
        )
    with pytest.raises(ValidationError, match="candidate"):
        symbols["Snapshot"](
            **{
                **fixture.model_dump(mode="python"),
                "routing": (
                    fixture.routing[0].model_copy(update={"candidate_id": "candidate-unknown"}),
                ),
            }
        )


@pytest.mark.parametrize(
    ("evidence_bundle_id", "evidence_hash"),
    [("evidence:gate-samples", None), (None, HASH_B)],
)
def test_gate_requires_evidence_identity_and_hash_as_a_pair(
    evidence_bundle_id: str | None,
    evidence_hash: str | None,
) -> None:
    symbols = _symbols()

    with pytest.raises(ValidationError, match="evidence_bundle_id and evidence_hash"):
        symbols["Gate"](
            code="minimum_samples",
            status="failed",
            blocking=True,
            current=18,
            required=50,
            evidence_bundle_id=evidence_bundle_id,
            evidence_hash=evidence_hash,
        )


@pytest.mark.parametrize(
    "unsafe_target",
    ["javascript:alert(1)", "https://evil.example/evidence", "/api/evidence/other/download"],
)
def test_gate_rejects_caller_supplied_evidence_urls(unsafe_target: str) -> None:
    symbols = _symbols()

    with pytest.raises(ValidationError, match="evidence_uri"):
        symbols["Gate"](
            code="minimum_samples",
            status="failed",
            blocking=True,
            current=18,
            required=50,
            evidence_hash=HASH_B,
            evidence_uri=unsafe_target,
        )


def test_candidate_rejects_duplicate_gate_codes() -> None:
    symbols = _symbols()
    candidate = _future_fixture(symbols).candidates[0]
    gate = candidate.gates[0]

    with pytest.raises(ValidationError, match="gate codes must be unique"):
        symbols["Candidate"](
            **{
                **candidate.model_dump(mode="python"),
                "gates": (gate, gate.model_copy(update={"status": "error"})),
            }
        )


def test_snapshot_rejects_duplicate_routing_candidate_ids() -> None:
    symbols = _symbols()
    snapshot = _future_fixture(symbols)
    routing = snapshot.routing[0]

    with pytest.raises(ValidationError, match="routing candidate ids must be unique"):
        symbols["Snapshot"](
            **{
                **snapshot.model_dump(mode="python"),
                "routing": (
                    routing,
                    routing.model_copy(update={"routing_version": 4}),
                ),
            }
        )


class SnapshotService:
    def __init__(self, symbols: dict[str, Any], *, fail: bool = False) -> None:
        self._symbols = symbols
        self.fail = fail
        self.principals: list[str] = []

    def get_snapshot(self, auth):
        self.principals.append(auth.principal.id)
        if self.fail:
            raise self._symbols["Unavailable"]("source unavailable")
        return self._symbols["Snapshot"](
            status="not_enabled",
            reason_code=NOT_ENABLED_REASON,
            candidates=(),
            routing=(),
            last_gate_hash=None,
        )


def _app(tmp_path, symbols: dict[str, Any], service: SnapshotService):
    settings = TianshuSettings(
        _env_file=None,
        security_mode="secure-remote",
        public_base_url=BASE_URL,
        allowed_hosts="tianshu.example.com",
        allowed_origins=BASE_URL,
        trusted_proxy_cidrs="127.0.0.1/32",
        auth_bootstrap_token_hash="sha256:" + hashlib.sha256(TOKEN.encode()).hexdigest(),
    )
    storage = Storage(str(tmp_path / "evolution-view.db"))
    storage.init_db()
    app = FastAPI()
    app.state.settings = settings
    app.state.storage = storage
    app.state.auth_service = AuthService(storage, settings)
    app.state.evolution_center_service = service
    app.state.public_webhook_paths = set()
    app.add_middleware(SecurityBoundaryMiddleware, settings=settings)
    app.include_router(symbols["router"], prefix="/api")
    return app, storage


def test_endpoint_is_authenticated_principal_scoped_and_correlated(tmp_path) -> None:
    symbols = _symbols()
    service = SnapshotService(symbols)
    app, storage = _app(tmp_path, symbols, service)
    try:
        with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000)) as client:
            anonymous = client.get("/api/evolution")
            response = client.get("/api/evolution", headers=HEADERS)
        assert anonymous.status_code == 401
        assert response.status_code == 200
        assert service.principals == ["user:owner"]
        assert response.json()["data"] == {
            "schema_version": 1,
            "status": "not_enabled",
            "reason_code": NOT_ENABLED_REASON,
            "candidates": [],
            "routing": [],
            "last_gate_hash": None,
        }
        assert response.json()["correlation_id"] == response.headers["x-correlation-id"]
    finally:
        storage.close()


def test_endpoint_source_failure_is_an_explicit_correlated_503(tmp_path) -> None:
    symbols = _symbols()
    app, storage = _app(tmp_path, symbols, SnapshotService(symbols, fail=True))
    try:
        with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000)) as client:
            response = client.get("/api/evolution", headers=HEADERS)
        assert response.status_code == 503
        assert response.json()["detail"] == {
            "code": "evolution_center_unavailable",
            "message": "evolution center source is unavailable",
            "correlation_id": response.headers["x-correlation-id"],
        }
        assert "candidates" not in response.text
    finally:
        storage.close()


def test_assignment_endpoint_is_owner_scoped_and_disclosure_safe(tmp_path) -> None:
    symbols = _symbols()
    app, storage = _app(tmp_path, symbols, SnapshotService(symbols))
    try:
        owner_edict = Edict(id="edict-owner", goal="owner", submitter="user:owner")
        owner_memorial = Memorial(id="memorial-owner", edict_id=owner_edict.id)
        other_edict = Edict(id="edict-other", goal="other", submitter="user:other")
        other_memorial = Memorial(id="memorial-other", edict_id=other_edict.id)
        storage.save_edict(owner_edict)
        storage.save_memorial(owner_memorial)
        storage.save_edict(other_edict)
        storage.save_memorial(other_memorial)
        assignment = ChallengerRouter(storage).assign(owner_memorial.id)
        ChallengerRouter(storage).assign(other_memorial.id)
        snapshot_components = {"kernel": HASH_A}
        system_snapshot = SystemSnapshotV1(
            components=snapshot_components,
            digest=canonical_sha256(snapshot_components),
        )
        with storage.unit_of_work() as unit_of_work:
            SystemSnapshotRepository().insert_binding(
                unit_of_work.connection,
                memorial_id=owner_memorial.id,
                attempt_id="attempt-owner",
                snapshot=system_snapshot,
            )
            unit_of_work.commit()

        owner_token = app.state.auth_service.issue_pat(
            Principal(
                id="user:owner",
                kind=PrincipalKind.HUMAN,
                display_name="Owner",
                scopes=frozenset({"api"}),
            ),
            label="evolution-owner",
            scopes=frozenset({"api"}),
        ).raw_token
        admin_token = app.state.auth_service.issue_pat(
            Principal(
                id="service:evolution-admin",
                kind=PrincipalKind.SERVICE,
                display_name="Evolution Admin",
                scopes=frozenset({"admin"}),
            ),
            label="evolution-admin",
            scopes=frozenset({"admin"}),
        ).raw_token
        with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 41000)) as client:
            owner = client.get(
                f"/api/evolution/runs/{owner_memorial.id}/assignment",
                headers={"Authorization": f"Bearer {owner_token}"},
            )
            outsider = client.get(
                f"/api/evolution/runs/{other_memorial.id}/assignment",
                headers={"Authorization": f"Bearer {owner_token}"},
            )
            admin = client.get(
                f"/api/evolution/runs/{other_memorial.id}/assignment",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            admin_missing = client.get(
                "/api/evolution/runs/memorial-missing/assignment",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

        assert owner.status_code == 200
        assert owner.json()["data"]["assignment"] == assignment.model_dump(mode="json")
        assert owner.json()["data"]["effective_overlay"] is None
        assert owner.json()["data"]["system_snapshot"] == {
            "digest": system_snapshot.digest,
            "components": snapshot_components,
            "generation_ids": [],
        }
        assert "secret" not in owner.text.lower()
        assert "host_path" not in owner.text
        assert outsider.status_code == 404
        assert outsider.json()["detail"]["code"] == "run_assignment_not_found"
        assert admin.status_code == 200
        assert admin.json()["data"]["system_snapshot"] is None
        assert admin_missing.status_code == 404
        assert admin_missing.json()["detail"]["code"] == outsider.json()["detail"]["code"]
        route_schema = app.openapi()["paths"]["/api/evolution/runs/{memorial_id}/assignment"][
            "get"
        ]["responses"]["200"]["content"]["application/json"]["schema"]
        assert route_schema["$ref"].endswith(RunEvolutionAssignmentResponseV1.__name__)
    finally:
        storage.close()


def test_production_service_is_truthfully_disabled_and_has_no_s5_data_dependency() -> None:
    symbols = _symbols()
    service = symbols["Service"]()
    source = inspect.getsource(symbols["Service"])

    assert "tianshu.storage" not in source
    assert "tianshu.evolution" not in source
    assert "tianshu.universe" not in source
    snapshot = service.get_snapshot(
        type("Auth", (), {"principal": type("Principal", (), {"id": "user:owner"})()})()
    )
    assert snapshot.status == "not_enabled"
    assert snapshot.reason_code == NOT_ENABLED_REASON
    assert snapshot.candidates == snapshot.routing == ()
    assert snapshot.last_gate_hash is None


def test_composition_root_registers_evolution_without_replacing_universes() -> None:
    _symbols()
    app = create_app(TianshuSettings(_env_file=None))
    paths = {route.path for route in app.routes}
    assert "/api/evolution" in paths
    assert "/api/universes" in paths
