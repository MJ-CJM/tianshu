"""Tests for auto compact strategy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.executor.compaction.auto import (
    auto_compact,
    should_auto_compact,
)
from tianshu.executor.loop_state import LoopState


class TestAutoCompact:
    def test_should_auto_compact_below_threshold(self):
        msgs = ({"role": "user", "content": "hi there!"},)
        assert should_auto_compact(msgs, context_limit=128000) is False

    def test_should_auto_compact_above_threshold(self):
        big = "x" * 300
        msgs = tuple(
            [{"role": "system", "content": "s"}]
            + [{"role": "user", "content": big}]
            + [{"role": "assistant", "content": f"r{i}"} for i in range(8)]
        )
        assert should_auto_compact(msgs, context_limit=100) is True

    def test_should_auto_compact_too_few_messages(self):
        big = "x" * 300
        msgs = (
            {"role": "system", "content": "s"},
            {"role": "user", "content": big},
        )
        assert should_auto_compact(msgs, context_limit=100) is False

    @pytest.mark.asyncio
    async def test_auto_compact_summarizes(self):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(12):
            msgs.append({"role": "user", "content": f"msg {i}"})

        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="Summary of conversation")

        state = LoopState(messages=tuple(msgs), iteration=3)
        result = await auto_compact(state, mock_llm, context_limit=128000)

        assert len(result.messages) == 8  # system + compact_msg + last 6
        assert result.messages[0]["role"] == "system"
        assert "摘要" in result.messages[1]["content"] or "Summary" in result.messages[1]["content"]
        assert result.compact_attempted is True
        assert result.total_compact_count == 1
        assert result.transition_reason == "auto_compact"
        mock_llm.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_compact_skips_when_few_messages(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        mock_llm = AsyncMock()
        state = LoopState(messages=tuple(msgs), iteration=0)
        result = await auto_compact(state, mock_llm, context_limit=128000)
        assert result is state
        mock_llm.chat.assert_not_called()
