"""Tests for compaction strategies."""

import pytest

from tianshu.executor.compaction.token_estimator import estimate_tokens


class TestTokenEstimator:
    def test_empty_messages(self):
        assert estimate_tokens(()) == 0

    def test_simple_text_message(self):
        msgs = ({"role": "user", "content": "hello world"},)
        result = estimate_tokens(msgs)
        assert result == 3  # 11 // 3 = 3

    def test_chinese_text(self):
        msgs = ({"role": "user", "content": "你好世界测试"},)
        result = estimate_tokens(msgs)
        assert result == 2  # 6 // 3 = 2

    def test_empty_content(self):
        msgs = ({"role": "user", "content": ""},)
        assert estimate_tokens(msgs) == 0

    def test_missing_content_key(self):
        msgs = ({"role": "system"},)
        assert estimate_tokens(msgs) == 0

    def test_multiple_messages(self):
        msgs = (
            {"role": "system", "content": "abc"},
            {"role": "user", "content": "defghi"},
            {"role": "assistant", "content": "jklmnopqr"},
        )
        assert estimate_tokens(msgs) == 6  # 1+2+3

    def test_list_content_blocks(self):
        msgs = (
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "text", "text": "world"},
                ],
            },
        )
        assert estimate_tokens(msgs) == 2  # 1+1


from tianshu.executor.loop_state import LoopState
from tianshu.executor.compaction.micro import micro_compact


class TestMicroCompact:
    def _make_state(self, messages: list[dict]) -> LoopState:
        return LoopState(messages=tuple(messages), iteration=0)

    def test_no_tool_messages_unchanged(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=4)
        assert list(result.messages) == msgs

    def test_recent_tool_results_preserved(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "tool_call_id": "1", "content": "result1"},
            {"role": "tool", "tool_call_id": "2", "content": "result2"},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=4)
        assert result.messages[1]["content"] == "result1"
        assert result.messages[2]["content"] == "result2"

    def test_old_tool_results_truncated(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "tool_call_id": "1", "content": "A" * 500},
            {"role": "tool", "tool_call_id": "2", "content": "B" * 500},
            {"role": "tool", "tool_call_id": "3", "content": "C" * 500},
            {"role": "tool", "tool_call_id": "4", "content": "D" * 500},
            {"role": "tool", "tool_call_id": "5", "content": "E" * 500},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=2)

        assert "[已压缩]" in result.messages[1]["content"]
        assert "[已压缩]" in result.messages[2]["content"]
        assert "[已压缩]" in result.messages[3]["content"]
        assert result.messages[4]["content"] == "D" * 500
        assert result.messages[5]["content"] == "E" * 500

    def test_tool_call_id_preserved(self):
        msgs = [
            {"role": "tool", "tool_call_id": "abc", "content": "X" * 1000},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=0)
        assert result.messages[0]["tool_call_id"] == "abc"

    def test_short_tool_results_not_truncated(self):
        msgs = [
            {"role": "tool", "tool_call_id": "1", "content": "short"},
            {"role": "tool", "tool_call_id": "2", "content": "also short"},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=0)
        assert result.messages[0]["content"] == "short"

    def test_transition_reason(self):
        state = self._make_state([{"role": "tool", "tool_call_id": "1", "content": "X" * 500}])
        result = micro_compact(state, keep_recent=0)
        assert result.transition_reason == "micro_compact"
