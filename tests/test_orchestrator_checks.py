"""checks runner 测试 —— bash 真跑，rubric mock LLM。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.executor.orchestrator.checks import (
    ChecksConfigError,
    run_checks,
)
from tianshu.models.acceptance import CheckSpec


@pytest.mark.integration
async def test_bash_pass():
    r = await run_checks(
        [CheckSpec(kind="bash", name="ok", command="echo ok")],
        actor_output="",
        llm=None,
    )
    assert r.all_passed
    assert r.outcomes[0].name == "ok"


@pytest.mark.integration
async def test_bash_fail():
    r = await run_checks(
        [CheckSpec(kind="bash", name="bad", command="exit 1")],
        actor_output="",
        llm=None,
    )
    assert not r.all_passed


@pytest.mark.integration
async def test_bash_timeout():
    r = await run_checks(
        [CheckSpec(kind="bash", name="slow", command="sleep 10", timeout_seconds=1)],
        actor_output="",
        llm=None,
    )
    assert not r.all_passed
    assert "timeout" in r.outcomes[0].detail


@pytest.mark.integration
async def test_rubric_pass():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(
        content='{"score": 0.9, "reasoning": "looks good"}',
    ))
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
    llm.chat = AsyncMock(return_value=MagicMock(
        content='{"score": 0.3, "reasoning": "meh"}',
    ))
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
