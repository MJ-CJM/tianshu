"""PiAdapter(pi 默认客卿单发档)测试:argv 规范性 + JSONL 事件流归一。"""

import json

from tianshu.executor.keqing import get_adapter, list_adapters
from tianshu.executor.keqing.pi_adapter import PiAdapter


def _stream(*events: dict) -> list[str]:
    return [json.dumps(e) for e in events]


class TestRegistration:
    def test_pi_backend_registered(self):
        assert "pi" in list_adapters()
        assert isinstance(get_adapter("pi"), PiAdapter)


class TestBuildArgv:
    def test_basic(self):
        argv = PiAdapter().build_argv("do X")
        assert argv == ["pi", "--mode", "json", "--no-session", "do X"]

    def test_with_model(self):
        argv = PiAdapter().build_argv("do X", model="anthropic/claude-opus-4-5")
        assert argv == [
            "pi",
            "--mode",
            "json",
            "--no-session",
            "do X",
            "--model",
            "anthropic/claude-opus-4-5",
        ]


class TestIsCanonicalArgv:
    def test_accepts_canonical(self):
        a = PiAdapter()
        assert a.is_canonical_argv(a.build_argv("p"))
        assert a.is_canonical_argv(a.build_argv("p", model="m"))

    def test_rejects_tampered(self):
        a = PiAdapter()
        assert not a.is_canonical_argv(["pi", "--mode", "rpc", "--no-session", "p"])
        assert not a.is_canonical_argv(["pi", "--mode", "json", "p"])  # 缺 --no-session
        assert not a.is_canonical_argv(["pi", "--mode", "json", "--no-session", ""])  # 空 prompt
        assert not a.is_canonical_argv(
            ["pi", "--mode", "json", "--no-session", "p", "--model"]
        )  # 缺 model 值
        assert not a.is_canonical_argv(
            ["evil", "--mode", "json", "--no-session", "p"]
        )  # 非 pi


class TestParseStream:
    def test_extracts_text_usage_cost_and_tools(self):
        lines = _stream(
            {"type": "session", "version": 3, "id": "x", "cwd": "/w"},
            {"type": "agent_start"},
            {"type": "turn_start"},
            {"type": "tool_execution_start", "toolCallId": "c1", "toolName": "bash", "args": {}},
            {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "bash", "isError": False},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done."}],
                    "usage": {"input": 100, "output": 20, "cost": {"total": 0.003}},
                },
            },
            {
                "type": "turn_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done."}],
                    "usage": {"input": 100, "output": 20, "cost": {"total": 0.003}},
                },
                "toolResults": [],
            },
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Done."}],
                        "usage": {"input": 100, "output": 20, "cost": {"total": 0.003}},
                    }
                ],
            },
        )
        r = PiAdapter().parse_stream(lines)
        assert r.final_text == "Done."
        # usage 只从 message_end 累计一次,不因 turn_end/agent_end 重复计
        assert r.input_tokens == 100
        assert r.output_tokens == 20
        assert r.cost_usd == 0.003
        assert r.tool_events == [{"type": "tool.called", "tool": "bash"}]
        assert r.is_error is False

    def test_accumulates_usage_across_multiple_messages(self):
        lines = _stream(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "step 1"}],
                    "usage": {"input": 50, "output": 10, "cost": {"total": 0.001}},
                },
            },
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "step 2 final"}],
                    "usage": {"input": 60, "output": 15, "cost": {"total": 0.002}},
                },
            },
        )
        r = PiAdapter().parse_stream(lines)
        assert r.final_text == "step 2 final"  # 最后一条 assistant 文本
        assert r.input_tokens == 110
        assert r.output_tokens == 25
        assert abs(r.cost_usd - 0.003) < 1e-9

    def test_agent_end_fallback_when_no_message_end(self):
        lines = _stream(
            {"type": "agent_start"},
            {
                "type": "agent_end",
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "only via agent_end"}],
                        "usage": {"input": 30, "output": 5, "cost": {"total": 0.0005}},
                    }
                ],
            },
        )
        r = PiAdapter().parse_stream(lines)
        assert r.final_text == "only via agent_end"
        assert r.input_tokens == 30
        assert r.output_tokens == 5

    def test_flags_error_message(self):
        lines = _stream(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [],
                    "stopReason": "error",
                    "errorMessage": "context_length_exceeded",
                },
            },
        )
        r = PiAdapter().parse_stream(lines)
        assert r.is_error is True
        assert r.error == "context_length_exceeded"

    def test_tolerates_malformed_lines(self):
        lines = ["not json", "", "  ", '{"type":"agent_start"}']
        r = PiAdapter().parse_stream(lines)  # 不崩
        assert r.final_text == ""


class TestVersionEvolution:
    """回应"pi 持续演进天枢能否支持":协议漂移可见 + 非破坏性演进宽容吸收。"""

    def test_session_version_drift_warns_but_continues(self, caplog):
        import logging

        lines = _stream(
            {"type": "session", "version": 999, "id": "x", "cwd": "/w"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {"input": 1, "output": 1, "cost": {"total": 0.0}},
                },
            },
        )
        with caplog.at_level(logging.WARNING):
            r = PiAdapter().parse_stream(lines)
        assert any("session 格式版本 999" in rec.message for rec in caplog.records)
        assert r.final_text == "ok"  # 漂移仍宽容解析

    def test_verified_version_no_warning(self, caplog):
        import logging

        lines = _stream({"type": "session", "version": 3, "id": "x", "cwd": "/w"})
        with caplog.at_level(logging.WARNING):
            PiAdapter().parse_stream(lines)
        assert not any("session 格式版本" in rec.message for rec in caplog.records)

    def test_unknown_event_types_are_ignored(self):
        # pi 未来加新事件类型(非破坏性演进)→ 天枢跳过、不崩、零改动
        lines = _stream(
            {"type": "some_future_event_2027", "payload": {"x": 1}},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "still works"}],
                    "usage": {"input": 1, "output": 1},
                },
            },
        )
        r = PiAdapter().parse_stream(lines)
        assert r.final_text == "still works"
