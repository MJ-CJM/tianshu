"""OpenCodeAdapter(单发客卿)测试:argv 规范 + json 事件流归一。"""

import json

from tianshu.executor.keqing import get_adapter, list_adapters
from tianshu.executor.keqing.adapter import OpenCodeAdapter


def _stream(*events: dict) -> list[str]:
    return [json.dumps(e) for e in events]


class TestRegistration:
    def test_opencode_registered(self):
        assert "opencode" in list_adapters()
        assert isinstance(get_adapter("opencode"), OpenCodeAdapter)


class TestBuildArgv:
    def test_basic(self):
        assert OpenCodeAdapter().build_argv("do X") == [
            "opencode",
            "run",
            "--format",
            "json",
            "do X",
        ]

    def test_with_model(self):
        assert OpenCodeAdapter().build_argv("p", model="anthropic/opus")[-2:] == [
            "--model",
            "anthropic/opus",
        ]

    def test_is_canonical(self):
        a = OpenCodeAdapter()
        assert a.is_canonical_argv(a.build_argv("p"))
        assert a.is_canonical_argv(a.build_argv("p", model="m"))
        assert not a.is_canonical_argv(["opencode", "run", "--format", "text", "p"])
        assert not a.is_canonical_argv(["opencode", "run", "--format", "json", ""])


class TestParseStream:
    def test_extracts_text_tokens_cost_tools(self):
        r = OpenCodeAdapter().parse_stream(
            _stream(
                {
                    "type": "message.part.updated",
                    "part": {"type": "tool", "tool": "bash", "state": {"status": "completed"}},
                },
                {
                    "type": "message.part.updated",
                    "part": {"type": "text", "text": "Done.", "time": {"end": 123}},
                },
                {
                    "type": "message.updated",
                    "info": {
                        "role": "assistant",
                        "tokens": {"input": 100, "output": 20},
                        "cost": 0.003,
                    },
                },
            )
        )
        assert r.final_text == "Done."
        assert r.input_tokens == 100 and r.output_tokens == 20
        assert r.cost_usd == 0.003
        assert r.tool_events == [{"type": "tool.called", "tool": "bash"}]
        assert r.is_error is False

    def test_unfinished_text_part_ignored(self):
        # 未 end 的流式 text part 不当终态文本
        r = OpenCodeAdapter().parse_stream(
            _stream({"type": "message.part.updated", "part": {"type": "text", "text": "partial"}})
        )
        assert r.final_text == ""

    def test_tool_error_flags_error(self):
        r = OpenCodeAdapter().parse_stream(
            _stream(
                {
                    "type": "message.part.updated",
                    "part": {"type": "tool", "tool": "bash", "state": {"status": "error"}},
                }
            )
        )
        assert r.is_error is True

    def test_tolerates_malformed(self):
        r = OpenCodeAdapter().parse_stream(["not json", "", '{"type":"message.updated"}'])
        assert r.final_text == ""
