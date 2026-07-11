"""Governance Contract v1 schema and canonicalization contracts."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tianshu.models.governance_contract import (
    AcceptancePolicyV1,
    BudgetPolicyV1,
    CapabilityRequirementsV1,
    ExecutorSelectionV1,
    NetworkPolicyV1,
    ObjectiveV1,
    PermissionPolicyV1,
    RecoveryPolicyV1,
    RequestedGovernanceContractV1,
    WorkspacePolicyV1,
)


def _requested(**overrides) -> RequestedGovernanceContractV1:
    data = {
        "objective": ObjectiveV1(
            goal="produce a verified report",
            context="use the checked-in data",
            output_format="markdown",
            constraints=("no external upload",),
        ),
        "executor": ExecutorSelectionV1(adapter_id="native"),
        "capabilities": CapabilityRequirementsV1(
            mandatory=("action_interception",),
            advisory=("artifact_export", "event_fidelity"),
        ),
        "permissions": PermissionPolicyV1(
            approval_required_tools=("shell_exec",),
            allowed_paths=("reports",),
            allowed_bash_prefixes=("pytest",),
            tier_overrides=(("shell_exec", 2),),
        ),
        "network": NetworkPolicyV1(
            mode="allowlist",
            allowed_hosts=("api.example.com",),
            write_hosts=("api.example.com",),
        ),
        "workspace": WorkspacePolicyV1(
            source_id="workspace-main",
            base_revision="HEAD",
            staging_mode="legacy_shared",
            apply_mode="none",
        ),
        "budget": BudgetPolicyV1(
            token_limit=4000,
            cost_limit_cny=Decimal("2.50"),
            wall_clock_seconds=300,
            max_iterations=10,
            max_concurrency=1,
            retry_limit=0,
        ),
        "recovery": RecoveryPolicyV1(
            require_restore_point=False,
            failure_cleanup="best_effort",
            rollback_on_apply_failure=True,
        ),
    }
    data.update(overrides)
    return RequestedGovernanceContractV1(**data)


def test_requested_contract_is_frozen_and_forbids_unknown_fields() -> None:
    contract = _requested()

    with pytest.raises(ValidationError, match="frozen"):
        contract.objective = ObjectiveV1(goal="changed")
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RequestedGovernanceContractV1(
            **contract.model_dump(),
            silent_downgrade=True,
        )


def test_nested_contract_values_are_immutable_tuples() -> None:
    contract = _requested()

    assert isinstance(contract.capabilities.mandatory, tuple)
    assert isinstance(contract.network.allowed_hosts, tuple)
    assert isinstance(contract.permissions.tier_overrides, tuple)
    with pytest.raises(AttributeError):
        contract.network.allowed_hosts.append("other.example.com")  # type: ignore[attr-defined]


def test_canonical_json_and_hash_are_stable_for_semantic_set_order() -> None:
    left = _requested(
        capabilities=CapabilityRequirementsV1(
            mandatory=("action_interception",),
            advisory=("event_fidelity", "artifact_export"),
        )
    )
    right = _requested(
        capabilities=CapabilityRequirementsV1(
            mandatory=("action_interception",),
            advisory=("artifact_export", "event_fidelity"),
        )
    )

    assert left.canonical_json() == right.canonical_json()
    assert left.content_hash == right.content_hash
    assert len(left.content_hash) == 64
    assert json.loads(left.canonical_json())["schema_version"] == "1"


@pytest.mark.parametrize(
    "budget",
    [
        BudgetPolicyV1.model_construct(token_limit=0),
        BudgetPolicyV1.model_construct(cost_limit_cny=Decimal("0")),
        BudgetPolicyV1.model_construct(wall_clock_seconds=-1),
    ],
)
def test_requested_contract_revalidates_invalid_nested_budget(budget: BudgetPolicyV1) -> None:
    with pytest.raises(ValidationError):
        RequestedGovernanceContractV1.model_validate(
            {**_requested().model_dump(), "budget": budget.model_dump()}
        )


def test_acceptance_deadlines_and_iteration_bounds_are_positive() -> None:
    with pytest.raises(ValidationError):
        AcceptancePolicyV1(deadline_seconds=0)
    with pytest.raises(ValidationError):
        AcceptancePolicyV1(min_outer_iterations=3, max_outer_iterations=2)


def test_mandatory_and_advisory_capabilities_cannot_overlap() -> None:
    with pytest.raises(ValidationError, match="overlap"):
        CapabilityRequirementsV1(
            mandatory=("workspace_control",),
            advisory=("workspace_control",),
        )
