"""Governance preview exposes effective controls before dispatch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianshu.app import create_app
from tianshu.config import TianshuSettings
from tianshu.gateway.edicts_api import _runtime_from_request, edicts_router
from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.models.api import EdictCreateRequest
from tianshu.models.edict import EdictRuntime
from tianshu.models.governance_contract import (
    CapabilityRequirementsV1,
    ExecutorSelectionV1,
    LegacyEdictGovernanceMapper,
    RecoveryPolicyV1,
    WorkspacePolicyV1,
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
    assert data["requested_contract"]["workspace"]["source_id"] == "workspace-main"
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
                mandatory=("durable_resume",),
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
            "capability": "durable_resume",
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


def test_preview_derives_workspace_capability_from_contract_semantics(config_manager) -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="isolated", runtime=EdictRuntime(executor="native")),
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "workspace": WorkspacePolicyV1(
                source_id="workspace-main",
                base_revision="HEAD",
                staging_mode="isolated",
                apply_mode="governed",
                require_clean_source=True,
            ),
            "recovery": RecoveryPolicyV1(require_restore_point=True),
        }
    )

    with TestClient(_app(config_manager)) as client:
        response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "isolated",
                "governance_contract": contract.model_dump(mode="json"),
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["compatible"] is True
    assert data["mandatory_mismatches"] == []
    workspace_control = next(
        item
        for item in data["effective_contract"]["effective_controls"]
        if item["capability"] == "workspace_control"
    )
    assert workspace_control["requested_mode"] == "advisory"
    assert workspace_control["state"] == "best_effort"
    controls = {
        item["capability"]: item for item in data["effective_contract"]["effective_controls"]
    }
    assert controls["pre_run_restore_point"]["requested_mode"] == "mandatory"
    assert controls["pre_run_restore_point"]["state"] == "enforced"
    assert controls["governed_apply_merge"]["requested_mode"] == "advisory"
    assert controls["governed_apply_merge"]["state"] == "enforced"


@pytest.mark.parametrize(
    ("workspace", "recovery", "expected_code"),
    [
        (
            WorkspacePolicyV1(
                source_id="workspace-main",
                staging_mode="legacy_shared",
            ),
            RecoveryPolicyV1(require_restore_point=True),
            "legacy_shared_policy_invalid",
        ),
        (
            WorkspacePolicyV1(
                source_id="workspace-main",
                staging_mode="ephemeral",
            ),
            RecoveryPolicyV1(),
            "ephemeral_policy_invalid",
        ),
        (
            WorkspacePolicyV1(
                source_id="workspace-main",
                base_revision="HEAD",
                staging_mode="isolated",
                apply_mode="governed",
                require_clean_source=True,
            ),
            RecoveryPolicyV1(require_restore_point=False),
            "isolated_policy_invalid",
        ),
    ],
)
def test_preview_marks_invalid_workspace_matrix_incompatible(
    config_manager,
    workspace: WorkspacePolicyV1,
    recovery: RecoveryPolicyV1,
    expected_code: str,
) -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="invalid workspace"),
        default_workspace_id="workspace-main",
    ).model_copy(update={"workspace": workspace, "recovery": recovery})

    with TestClient(_app(config_manager)) as client:
        response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "invalid workspace",
                "governance_contract": contract.model_dump(mode="json"),
            },
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["compatible"] is False
    assert data["effective_contract"] is None
    assert [item["code"] for item in data["workspace_policy_mismatches"]] == [expected_code]


def test_create_rejects_invalid_workspace_before_persist_or_dispatch(tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    settings = TianshuSettings(
        _env_file=None,
        db_path=str(tmp_path / "tianshu.sqlite3"),
        workspace_dir=str(source),
        workspace_staging_root=str(tmp_path / "staging"),
        memory_dir=str(tmp_path / "memory"),
        runtime_personas_dir=str(tmp_path / "personas"),
        log_dir=str(tmp_path / "logs"),
    )
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="reject invalid workspace"),
        default_workspace_id="workspace-main",
    ).model_copy(update={"recovery": RecoveryPolicyV1(require_restore_point=True)})
    app = create_app(settings)

    with TestClient(app) as client:
        app.state.storage.save_edict(
            Edict(
                goal="reject invalid workspace",
                submitter="local:owner",
                idempotency_key="invalid-workspace",
                governance_contract=contract,
            )
        )
        response = client.post(
            "/api/edicts",
            json={
                "goal": "reject invalid workspace",
                "idempotency_key": "invalid-workspace",
                "governance_contract": contract.model_dump(mode="json"),
            },
        )
        _edicts, total = app.state.storage.list_edicts()
        running = tuple(app.state.executor.running_tasks)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "governance_workspace_policy_mismatch"
    assert total == 1
    assert running == ()


def test_preview_does_not_claim_restore_enforcement_without_git(
    config_manager,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "tianshu.executor.capabilities.shutil.which",
        lambda _name, path=None: None,
    )
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="git unavailable"),
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "workspace": WorkspacePolicyV1(
                source_id="workspace-main",
                base_revision="HEAD",
                staging_mode="isolated",
                apply_mode="governed",
                require_clean_source=True,
            ),
            "recovery": RecoveryPolicyV1(require_restore_point=True),
        }
    )

    with TestClient(_app(config_manager)) as client:
        response = client.post(
            "/edicts/governance/preview",
            json={
                "goal": "git unavailable",
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
        {"runtime": {"executor_model": "different-model"}},
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


def test_contract_executor_model_materializes_into_runtime(config_manager) -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="model pin", runtime=EdictRuntime(executor="keqing:codex")),
        default_workspace_id="workspace-main",
    ).model_copy(
        update={
            "executor": ExecutorSelectionV1(
                adapter_id="keqing:codex",
                model="gpt-5.1-codex",
            )
        }
    )
    app = _app(config_manager)
    runtime = _runtime_from_request(
        EdictCreateRequest(goal="model pin", governance_contract=contract),
        SimpleNamespace(app=app),
    )

    assert runtime.executor_model == "gpt-5.1-codex"


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
    parent = Memorial(
        edict_id=edict.id,
        instruction="first turn",
        status=TaskStatus.COMPLETED,
    )
    storage.save_memorial(parent)
    storage.save_memorial(
        Memorial(
            edict_id=edict.id,
            instruction="DAG child",
            status=TaskStatus.COMPLETED,
            dag_node_id="child",
            parent_memorial_id=parent.id,
        )
    )
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
    assert memorial.parent_memorial_id == parent.id


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
