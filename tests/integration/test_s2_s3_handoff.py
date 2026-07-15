"""Freeze the live S2 contracts consumed by S3 Core Governance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from tianshu.app import create_app
from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.executor.workspace_service import WorkspaceService
from tianshu.gateway.workspace_api import WorkspaceApplySurface
from tianshu.models.governance_contract import (
    CanonicalContractModel,
    EffectiveGovernanceContractV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import AuthContext
from tianshu.models.system_audit import SystemAuditEventV1
from tianshu.storage.migrations import MIGRATIONS
from tianshu.tools.mcp.config import MCPServerConfig
from tianshu.tools.mcp.manager import AdmissionDecision, MCPManager
from tianshu.tools.registry import ToolRegistry

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _assert_s2_report_green(report: str) -> None:
    lines = report.splitlines()
    assert lines[:2] == ["# S2 Lean Security Gate Report", ""]

    metadata_end = lines.index("", 2)
    metadata_status = [line for line in lines[2:metadata_end] if line.startswith("- status:")]
    all_status_lines = [line for line in lines if line.startswith("- status:")]
    assert metadata_status == ["- status: passed"]
    assert all_status_lines == ["- status: passed"]

    section_starts = [index for index, line in enumerate(lines) if line.startswith("## ")]
    assert section_starts and lines[section_starts[-1]] == "## Exit"
    exit_body = "\n".join(lines[section_starts[-1] + 1 :]).strip()
    assert exit_body == (
        "S2 Lean Security is passed at the boundary above. "
        "The next stage is **S3 Core Governance**."
    )


def test_s2_lean_security_report_is_green() -> None:
    report = (_REPOSITORY_ROOT / "docs/cc-fable-v1/reports/s2-lean-security-report.md").read_text(
        encoding="utf-8"
    )

    _assert_s2_report_green(report)


def test_s2_report_guard_rejects_failed_status_with_historical_pass() -> None:
    report = """# S2 Lean Security Gate Report

- status: failed

## Gate history

- status: passed
- S2 Lean Security is passed in an earlier attempt.

## Exit

S2 Lean Security is passed at the boundary above. The next stage is **S3 Core Governance**.
"""

    with pytest.raises(AssertionError):
        _assert_s2_report_green(report)


def test_live_migration_versions_are_contiguous_through_v9() -> None:
    assert tuple(migration.version for migration in MIGRATIONS[:9]) == tuple(range(1, 10))
    assert (MIGRATIONS[8].version, MIGRATIONS[8].name) == (
        9,
        "0009_durable_edict_ingress",
    )


def test_consumed_s2_runtime_interfaces_remain_importable() -> None:
    assert issubclass(SystemAuditEventV1, BaseModel)
    assert issubclass(AuthContext, BaseModel)
    assert {"run", "start"} <= set(vars(ExecutionGateway))
    assert {
        "get_run_status",
        "get_run_changes",
        "issue_apply_decision",
        "apply",
    } <= set(vars(WorkspaceService))

    routes = {route.path: route for route in create_app().routes}
    assert routes["/health/live"].methods == {"GET", "HEAD"}
    assert routes["/health/ready"].methods == {"GET", "HEAD"}


def test_secure_remote_remote_mcp_remains_denied() -> None:
    manager = MCPManager(
        ToolRegistry(),
        ExecutionGateway(),
        security_mode="secure-remote",
    )
    remote = MCPServerConfig(
        name="remote",
        transport="streamable_http",
        url="https://mcp.example.com/service",
    )

    assert manager.admission_for(remote) == AdmissionDecision(
        allowed=False,
        reason_code="trusted_egress_unavailable",
    )


def test_governance_contract_remains_frozen_and_canonical() -> None:
    assert issubclass(RequestedGovernanceContractV1, CanonicalContractModel)
    assert issubclass(EffectiveGovernanceContractV1, CanonicalContractModel)
    assert RequestedGovernanceContractV1.model_config["frozen"] is True
    assert EffectiveGovernanceContractV1.model_config["frozen"] is True

    requested = RequestedGovernanceContractV1(
        objective=ObjectiveV1(goal="freeze the S2 to S3 handoff", context=None)
    )
    requested_json = json.dumps(
        requested.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    assert requested.canonical_json() == requested_json
    assert requested.content_hash == hashlib.sha256(requested_json.encode("utf-8")).hexdigest()
    with pytest.raises(ValidationError, match="frozen"):
        requested.objective = ObjectiveV1(goal="mutated")

    effective = resolve_governance_contract(
        requested,
        native_manifest(),
        probe_host_capabilities(),
    )
    effective_json = json.dumps(
        effective.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    assert isinstance(effective, EffectiveGovernanceContractV1)
    assert effective.canonical_json() == effective_json
    assert effective.content_hash == hashlib.sha256(effective_json.encode("utf-8")).hexdigest()
    with pytest.raises(ValidationError, match="frozen"):
        effective.runtime_probe_id = "mutated"


def test_g1_governed_apply_is_only_a_projection_binding() -> None:
    plan = (_REPOSITORY_ROOT / "docs/cc-fable-v1/01-master-plan.md").read_text(encoding="utf-8")
    public_operations = {
        name
        for name, value in vars(WorkspaceApplySurface).items()
        if not name.startswith("_") and callable(value)
    }

    assert "generic `DecisionRequest`/`Resolution` 是唯一治理权威" in plan
    assert "G1 workspace apply authorization 只作为已裁决请求的不可变单向 projection" in plan
    assert "禁止第二套批准权威" in plan
    assert public_operations == {
        "get_run_status",
        "get_run_changes",
        "issue_apply_decision",
        "apply",
    }
