"""Governance preview exposes effective controls before dispatch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.gateway.edicts_api import _runtime_from_request, edicts_router
from tianshu.models import Edict
from tianshu.models.api import EdictCreateRequest
from tianshu.models.edict import EdictRuntime
from tianshu.models.governance_contract import (
    CapabilityRequirementsV1,
    LegacyEdictGovernanceMapper,
)


def _app(config_manager) -> FastAPI:
    app = FastAPI()
    app.include_router(edicts_router)
    app.state.config_manager = config_manager
    app.state.settings = SimpleNamespace(workspace_dir="/private/example/workspace")
    return app


def test_preview_maps_legacy_request_and_exposes_advisory_gaps(config_manager) -> None:
    with TestClient(_app(config_manager)) as client:
        response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "preview this",
                "runtime": {
                    "executor": "keqing:codex",
                    "policy_profile": {
                        "template_name": "safe",
                        "allowed_paths": ["reports"],
                    },
                },
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["compatible"] is True
    assert data["requested_contract"]["executor"]["adapter_id"] == "keqing:codex"
    assert data["requested_contract"]["permissions"]["policy_profile_name"] == "safe"
    assert data["effective_contract"]["executor_manifest_id"] == "tianshu.keqing.codex.v1"
    assert "action_interception" in data["advisory_gaps"]
    assert data["executor_level"] == "contained"
    assert data["experimental"] is True
    assert "/private/example/workspace" not in str(data)


def test_preview_returns_structured_mismatch_without_dispatch(config_manager) -> None:
    base = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="must restore", runtime=EdictRuntime(executor="native")),
        default_workspace_id="workspace-main",
    )
    contract = base.model_copy(
        update={
            "capabilities": CapabilityRequirementsV1(
                mandatory=("pre_run_restore_point",),
                advisory=(),
            )
        }
    )

    with TestClient(_app(config_manager)) as client:
        response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "must restore",
                "governance_contract": contract.model_dump(mode="json"),
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["compatible"] is False
    assert data["effective_contract"] is None
    assert data["mandatory_mismatches"] == [
        {
            "schema_version": "1",
            "capability": "pre_run_restore_point",
            "required_state": "enforced",
            "available_state": "unsupported",
            "manifest_id": "tianshu.native.v1",
            "reason": "mandatory capabilities accept only enforced controls",
        }
    ]


def test_preview_rejects_contained_cli_for_outer_loop_before_dispatch(config_manager) -> None:
    with TestClient(_app(config_manager)) as client:
        response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "long task",
                "runtime": {"executor": "keqing:codex"},
                "acceptance": {},
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["compatible"] is False
    assert data["execution_mode"] == "outer_loop"
    assert data["execution_mode_mismatches"] == [
        {
            "adapter_id": "keqing:codex",
            "requested_mode": "outer_loop",
            "supported_modes": ["single"],
        }
    ]


def test_conflicting_new_and_legacy_payload_returns_422(config_manager) -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="conflict", runtime=EdictRuntime(executor="native")),
        default_workspace_id="workspace-main",
    )

    with TestClient(_app(config_manager)) as client:
        response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "conflict",
                "runtime": {"executor": "keqing:claude-code"},
                "governance_contract": contract.model_dump(mode="json"),
            },
        )

    assert response.status_code == 422
    assert "conflicting legacy" in response.text


def test_security_relevant_legacy_conflicts_return_422(config_manager) -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="conflict", runtime=EdictRuntime(executor="native")),
        default_workspace_id="workspace-main",
    )

    payloads = (
        {"review_policy": "always"},
        {"runtime": {"approval_required_tools": ["shell"]}},
        {
            "runtime": {
                "policy_profile": {
                    "auto_approve_max_tier": 2,
                    "expires_after_seconds": 30,
                }
            }
        },
        {"runtime": {"fetch_engine_override": "jina"}},
    )
    with TestClient(_app(config_manager)) as client:
        for legacy_fields in payloads:
            response = client.post(
                "/edicts/governance/preview",
                json={
                    "goal": "conflict",
                    "governance_contract": contract.model_dump(mode="json"),
                    **legacy_fields,
                },
            )
            assert response.status_code == 422, response.text
            assert "conflicting legacy" in response.text


def test_previewed_contract_preserves_runtime_provider_pins(config_manager) -> None:
    with TestClient(_app(config_manager)) as client:
        preview_response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "pinned providers",
                "runtime": {
                    "fetch_engine_override": "jina",
                    "search_provider_override": "tavily",
                },
            },
        )
        contract = preview_response.json()["data"]["requested_contract"]
        exact_response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "pinned providers",
                "runtime": {
                    "fetch_engine_override": "jina",
                    "search_provider_override": "tavily",
                },
                "governance_contract": contract,
            },
        )

    assert preview_response.status_code == 200
    assert exact_response.status_code == 200, exact_response.text
    assert {(item["name"], item["value"]) for item in contract["executor"]["config"]} == {
        ("fetch_engine_override", "jina"),
        ("search_provider_override", "tavily"),
    }


def test_contract_permissions_materialize_non_default_policy_profile(config_manager) -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(
            goal="profile",
            runtime=EdictRuntime(executor="native", fetch_engine_override="jina"),
        ),
        default_workspace_id="workspace-main",
    )
    contract = contract.model_copy(
        update={
            "permissions": contract.permissions.model_copy(
                update={"auto_approve_max_tier": 2, "expires_after_seconds": 30}
            )
        }
    )

    app = _app(config_manager)
    body = EdictCreateRequest(
        goal="profile",
        governance_contract=contract,
    )
    runtime = _runtime_from_request(body, SimpleNamespace(app=app))

    assert runtime.policy_profile is not None
    assert runtime.policy_profile.auto_approve_max_tier == 2
    assert runtime.policy_profile.expires_after_seconds == 30
    assert runtime.fetch_engine_override == "jina"


def test_executor_only_follow_up_preserves_unspecified_grants(config_manager, storage) -> None:
    edict = Edict(
        goal="follow-up",
        runtime=EdictRuntime(
            approval_required_tools=["shell"],
            api_request_hosts=("api.example.com",),
            api_request_write_hosts=("api.example.com",),
        ),
    )
    storage.save_edict(edict)
    executor = SimpleNamespace(execute_edict=AsyncMock(), running_tasks=set())
    app = _app(config_manager)
    app.state.storage = storage
    app.state.executor = executor

    with TestClient(app) as client:
        response = client.post(
            f"/edicts/{edict.id}/follow-up",
            json={
                "instruction": "delegate this one",
                "runtime_override": {"executor": "keqing:codex"},
            },
        )

    assert response.status_code == 202, response.text
    memorial = storage.get_memorial(response.json()["data"]["id"])
    assert memorial.runtime_override == {"executor": "keqing:codex"}


def test_frozen_governance_objective_cannot_be_edited(config_manager, storage) -> None:
    edict = Edict(goal="frozen objective", context="frozen context")
    storage.save_edict(edict)
    app = _app(config_manager)
    app.state.storage = storage

    with TestClient(app) as client:
        response = client.patch(
            f"/edicts/{edict.id}",
            json={"goal": "different objective"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "governance_contract_frozen"
    assert storage.get_edict(edict.id).goal == "frozen objective"
