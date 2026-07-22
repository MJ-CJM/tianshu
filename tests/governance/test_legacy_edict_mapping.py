"""Legacy Edict fields map losslessly into Governance Contract v1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tianshu.models.acceptance import AcceptanceCriteria, CheckSpec, CriticSpec
from tianshu.models.api import EdictCreateRequest, EdictRuntimeRequest
from tianshu.models.edict import Edict, EdictRuntime, PolicyProfilePayload
from tianshu.models.governance_contract import (
    ExecutorSelectionV1,
    LegacyEdictGovernanceMapper,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)


def _legacy_edict() -> Edict:
    return Edict(
        goal="ship the report",
        context="preserve evidence",
        constraints=["do not publish"],
        output_format="markdown",
        review_policy="always",
        runtime=EdictRuntime(
            executor="keqing:claude-code",
            timeout_seconds=120,
            max_iterations=7,
            max_concurrency=2,
            retry_limit=1,
            token_budget=3000,
            cost_budget_cny=4.5,
            fetch_engine_override="jina",
            search_provider_override="tavily",
            api_request_hosts=("read.example.com", "write.example.com"),
            api_request_write_hosts=("write.example.com",),
            approval_required_tools=["shell_exec"],
            policy_profile=PolicyProfilePayload(
                allowed_paths=["reports"],
                allowed_bash_prefixes=["pytest"],
                tier_overrides={"shell_exec": 2},
                auto_approve_max_tier=1,
                expires_after_seconds=600,
                template_name="reviewed",
            ),
        ),
        acceptance=AcceptanceCriteria(
            checks=[CheckSpec(kind="bash", name="tests", command="pytest -q")],
            critic=CriticSpec(persona_ids=["ducha"], strictness="strict"),
            min_outer_iterations=2,
            max_outer_iterations=4,
            deadline_seconds=900,
        ),
    )


def test_legacy_mapper_preserves_executor_policy_network_acceptance_and_budgets() -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        _legacy_edict(),
        default_workspace_id="workspace-main",
    )

    assert contract.executor.adapter_id == "keqing:claude-code"
    assert {(item.name, item.value) for item in contract.executor.config} == {
        ("fetch_engine_override", "jina"),
        ("search_provider_override", "tavily"),
    }
    assert contract.permissions.policy_profile_name == "reviewed"
    assert contract.permissions.allowed_paths == ("reports",)
    assert contract.permissions.tier_overrides == (("shell_exec", 2),)
    assert contract.network.allowed_hosts == ("read.example.com", "write.example.com")
    assert contract.network.write_hosts == ("write.example.com",)
    assert contract.acceptance.checks[0].command == "pytest -q"
    assert contract.acceptance.critic_persona_ids == ("ducha",)
    assert contract.budget.token_limit == 3000
    assert str(contract.budget.cost_limit_cny) == "4.5"
    assert contract.workspace.source_id == "workspace-main"


def test_runtime_request_keeps_executor_and_policy_profile_instead_of_dropping_them() -> None:
    runtime = EdictRuntimeRequest.model_validate(
        {
            "executor": "keqing:codex",
            "policy_profile": {
                "template_name": "safe",
                "allowed_paths": ["reports"],
            },
        }
    )

    assert runtime.executor == "keqing:codex"
    assert runtime.policy_profile is not None
    assert runtime.policy_profile.template_name == "safe"
    assert runtime.model_dump(exclude_none=True)["executor"] == "keqing:codex"


def test_executor_model_roundtrips_through_legacy_runtime_and_api_request() -> None:
    edict = Edict(
        goal="model pin",
        runtime=EdictRuntime(executor="keqing:codex", executor_model="gpt-5.1-codex"),
    )
    contract = LegacyEdictGovernanceMapper.from_edict(
        edict,
        default_workspace_id="workspace-main",
    )
    request = EdictRuntimeRequest(
        executor="keqing:codex",
        executor_model="gpt-5.1-codex",
    )

    assert contract.executor.model == "gpt-5.1-codex"
    assert request.executor_model == "gpt-5.1-codex"


def test_new_and_legacy_executor_conflict_is_rejected() -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="ship", runtime=EdictRuntime(executor="native")),
        default_workspace_id="workspace-main",
    )

    with pytest.raises(ValidationError, match="conflicting legacy"):
        EdictCreateRequest(
            goal="ship",
            runtime={"executor": "keqing:codex"},
            governance_contract=contract,
        )


def test_equivalent_new_and_legacy_executor_is_accepted() -> None:
    base = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="ship", runtime=EdictRuntime(executor="native")),
        default_workspace_id="workspace-main",
    )
    contract = RequestedGovernanceContractV1(
        **{
            **base.model_dump(),
            "objective": ObjectiveV1(goal="ship"),
            "executor": ExecutorSelectionV1(adapter_id="native"),
        }
    )

    request = EdictCreateRequest(
        goal="ship",
        runtime={"executor": "native"},
        governance_contract=contract,
    )

    assert request.governance_contract == contract


def test_edict_cannot_persist_runtime_that_conflicts_with_frozen_contract() -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(goal="ship", runtime=EdictRuntime(executor="native")),
        default_workspace_id="workspace-main",
    )

    with pytest.raises(ValidationError, match="runtime.executor"):
        Edict(
            goal="ship",
            runtime=EdictRuntime(executor="keqing:codex"),
            governance_contract=contract,
        )


def test_edict_cannot_persist_executor_model_that_conflicts_with_contract() -> None:
    contract = LegacyEdictGovernanceMapper.from_edict(
        Edict(
            goal="ship",
            runtime=EdictRuntime(executor="native", executor_model="governed-model"),
        ),
        default_workspace_id="workspace-main",
    )

    with pytest.raises(ValidationError, match="runtime.executor_model"):
        Edict(
            goal="ship",
            runtime=EdictRuntime(executor="native", executor_model="other-model"),
            governance_contract=contract,
        )
