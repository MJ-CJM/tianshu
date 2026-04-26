"""critic agent 测试 —— mock LLM。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.executor.orchestrator.critic import (
    CriticUnavailable,
    review,
)
from tianshu.models.acceptance import AcceptanceCriteria
from tianshu.models.edict import Edict


def _edict() -> Edict:
    return Edict(goal="write a poem", acceptance=AcceptanceCriteria())


@pytest.mark.unit
async def test_critic_pass():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(
        content='{"verdict": "pass", "feedback": "great"}',
    ))
    r = await review("poem text", _edict(), AcceptanceCriteria(), llm)
    assert r.verdict == "pass"
    assert r.issue_class is None


@pytest.mark.unit
async def test_critic_fail_with_issue_class():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(
        content='{"verdict": "fail", "issue_class": "tone_mismatch", "feedback": "too dry"}',
    ))
    r = await review("dry text", _edict(), AcceptanceCriteria(), llm)
    assert r.verdict == "fail"
    assert r.issue_class == "tone_mismatch"


@pytest.mark.unit
async def test_critic_invalid_issue_class_normalized_to_other():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(
        content='{"verdict": "fail", "issue_class": "made_up_class", "feedback": "x"}',
    ))
    r = await review("text", _edict(), AcceptanceCriteria(), llm)
    assert r.issue_class == "other"


@pytest.mark.unit
async def test_critic_unavailable_after_retries():
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(CriticUnavailable):
        await review("text", _edict(), AcceptanceCriteria(), llm, max_retries=2)


@pytest.mark.unit
async def test_critic_fallback_used_after_primary_fails():
    primary = MagicMock()
    primary.chat = AsyncMock(side_effect=RuntimeError("primary down"))
    fallback = MagicMock()
    fallback.chat = AsyncMock(return_value=MagicMock(
        content='{"verdict": "pass", "feedback": "ok"}',
    ))
    r = await review("text", _edict(), AcceptanceCriteria(),
                     primary, fallback_llm=fallback, max_retries=2)
    assert r.verdict == "pass"
