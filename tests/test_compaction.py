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
