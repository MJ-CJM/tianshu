"""Automatic skill review must not spend tokens while activation is unavailable."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from tianshu.kernel.exit_reason import ExitReason
from tianshu.skills.loader import SkillsLoader
from tianshu.skills.reviewer import SkillReviewHandler


async def test_unwired_reviewer_skips_before_llm(tmp_path) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    config = SimpleNamespace(
        agent_config=SimpleNamespace(
            skill_review_enabled=True,
            skill_review_interval=1,
        )
    )
    reviewer = SkillReviewHandler(
        SkillsLoader(builtin_dir=builtin, user_dir=user),
        config,
    )
    reviewer._run_review = AsyncMock()  # type: ignore[method-assign]

    await reviewer.on_agent_end(
        exit_reason=ExitReason.COMPLETED,
        iteration_count=3,
        events=[],
    )

    reviewer._run_review.assert_not_awaited()
