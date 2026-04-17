"""Tests for LLMResponse finish_reason field."""

from tianshu.llm import LLMResponse
from tianshu.models import UsageSummary


class TestLLMResponseFinishReason:
    def test_finish_reason_field_exists(self):
        resp = LLMResponse(
            content="hello",
            tool_calls=None,
            usage=UsageSummary(),
            finish_reason="stop",
        )
        assert resp.finish_reason == "stop"

    def test_finish_reason_default_none(self):
        resp = LLMResponse(
            content="hello",
            tool_calls=None,
            usage=UsageSummary(),
        )
        assert resp.finish_reason is None

    def test_finish_reason_length(self):
        resp = LLMResponse(
            content="partial...",
            tool_calls=None,
            usage=UsageSummary(),
            finish_reason="length",
        )
        assert resp.finish_reason == "length"
