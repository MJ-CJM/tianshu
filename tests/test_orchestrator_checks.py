"""checks runner 测试 —— bash 真跑，rubric mock LLM。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.executor.capabilities import (
    native_manifest,
    probe_host_capabilities,
    resolve_governance_contract,
)
from tianshu.executor.execution_gateway import (
    ExecutionContext,
    ExecutionGateway,
    bind_execution_context,
)
from tianshu.executor.orchestrator.checks import (
    ChecksConfigError,
    run_checks,
)
from tianshu.models.acceptance import CheckSpec
from tianshu.models.governance_contract import (
    AcceptanceCheckV1,
    AcceptancePolicyV1,
    ObjectiveV1,
    RequestedGovernanceContractV1,
)
from tianshu.models.principal import Principal, PrincipalKind


async def _run_governed_bash_checks(specs: list[CheckSpec], tmp_path):
    effective = resolve_governance_contract(
        RequestedGovernanceContractV1(
            objective=ObjectiveV1(goal="test acceptance checks"),
            acceptance=AcceptancePolicyV1(
                checks=tuple(
                    AcceptanceCheckV1(
                        kind=spec.kind,
                        name=spec.name,
                        command=spec.command,
                        timeout_seconds=spec.timeout_seconds,
                    )
                    for spec in specs
                )
            ),
        ),
        native_manifest(),
        probe_host_capabilities(),
    )
    context = ExecutionContext(
        correlation_id="checks-test",
        actor=Principal(
            id="checks-principal",
            kind=PrincipalKind.SERVICE,
            display_name="Checks Test",
        ),
        effective_contract=effective,
        workspace_lease_id="checks-workspace",
    )
    with bind_execution_context(context):
        return await run_checks(
            specs,
            actor_output="",
            llm=None,
            execution_gateway=ExecutionGateway(),
            workspace_root=tmp_path,
        )


@pytest.mark.integration
async def test_bash_pass(tmp_path):
    r = await _run_governed_bash_checks(
        [CheckSpec(kind="bash", name="ok", command="echo ok")], tmp_path
    )
    assert r.all_passed
    assert r.outcomes[0].name == "ok"


@pytest.mark.integration
async def test_bash_fail(tmp_path):
    r = await _run_governed_bash_checks(
        [CheckSpec(kind="bash", name="bad", command="exit 1")], tmp_path
    )
    assert not r.all_passed


@pytest.mark.integration
async def test_bash_timeout(tmp_path):
    r = await _run_governed_bash_checks(
        [CheckSpec(kind="bash", name="slow", command="sleep 10", timeout_seconds=1)],
        tmp_path,
    )
    assert not r.all_passed
    assert "timeout" in r.outcomes[0].detail


@pytest.mark.integration
async def test_rubric_pass():
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=MagicMock(
            content='{"score": 0.9, "reasoning": "looks good"}',
        )
    )
    r = await run_checks(
        [CheckSpec(kind="rubric", name="tone", rubric="be friendly")],
        actor_output="hello!",
        llm=llm,
    )
    assert r.all_passed
    assert r.outcomes[0].score == 0.9


@pytest.mark.integration
async def test_rubric_fail_on_low_score():
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=MagicMock(
            content='{"score": 0.3, "reasoning": "meh"}',
        )
    )
    r = await run_checks(
        [CheckSpec(kind="rubric", name="tone", rubric="be friendly", pass_threshold=0.8)],
        actor_output="meh.",
        llm=llm,
    )
    assert not r.all_passed


@pytest.mark.integration
async def test_bash_missing_command_raises():
    with pytest.raises(ChecksConfigError):
        await run_checks(
            [CheckSpec(kind="bash", name="nocmd")],  # 缺 command
            actor_output="",
            llm=None,
        )
