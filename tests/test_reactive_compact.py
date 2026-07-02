"""Tests for reactive compact strategy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.executor.compaction.reactive import reactive_compact
from tianshu.executor.loop_state import LoopState


class TestReactiveCompact:
    def _make_state_with_tools(self, tool_count: int, content_size: int) -> LoopState:
        msgs: list[dict] = [{"role": "system", "content": "sys"}]
        for i in range(tool_count):
            msgs.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"tc_{i}", "type": "function", "function": {"name": "grep", "arguments": "{}"}}],
            })
            msgs.append({
                "role": "tool",
                "tool_call_id": f"tc_{i}",
                "content": "X" * content_size,
            })
        return LoopState(messages=tuple(msgs), iteration=5)

    @pytest.mark.asyncio
    async def test_aggressive_micro_sufficient(self):
        state = self._make_state_with_tools(10, 1000)
        result = await reactive_compact(state, llm=AsyncMock(), context_limit=50000)
        assert result is not None
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        truncated = [m for m in tool_msgs if "[已压缩]" in m.get("content", "")]
        assert len(truncated) > 0

    @pytest.mark.asyncio
    async def test_falls_back_to_auto_compact(self):
        state = self._make_state_with_tools(20, 2000)
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="Summary")
        result = await reactive_compact(state, llm=mock_llm, context_limit=100)
        assert result is not None
        assert result.compact_attempted is True

    @pytest.mark.asyncio
    async def test_returns_none_on_total_failure(self):
        state = self._make_state_with_tools(5, 500)
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = Exception("LLM down")
        result = await reactive_compact(state, llm=mock_llm, context_limit=10)
        assert result is None
