"""客卿适配器(迭代 3.5)——注册表 + stream-json 解析。"""

from __future__ import annotations

import json

from tianshu.executor.keqing.adapter import (
    ClaudeCodeAdapter,
    CodexAdapter,
    get_adapter,
    list_adapters,
)


class TestRegistry:
    def test_list_and_get(self):
        assert list_adapters() == ["claude-code", "codex"]
        assert isinstance(get_adapter("claude-code"), ClaudeCodeAdapter)
        assert isinstance(get_adapter("codex"), CodexAdapter)
        assert get_adapter("ghost") is None


class TestClaudeCodeAdapter:
    def test_build_argv(self):
        ad = ClaudeCodeAdapter()
        argv = ad.build_argv("do it", model="claude-sonnet-5")
        assert argv[0] == "claude" and "-p" in argv and "do it" in argv
        assert "stream-json" in argv and "--model" in argv

    def test_auth_env_isolation(self):
        # 客卿用自身 Anthropic 凭证;不含天枢 TIANSHU_* 变量
        assert "ANTHROPIC_API_KEY" in ClaudeCodeAdapter.auth_env_vars
        assert not any(v.startswith("TIANSHU_") for v in ClaudeCodeAdapter.auth_env_vars)

    def test_parse_stream_full(self):
        lines = [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "read_file"}]},
                }
            ),
            json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "..."}]}}
            ),
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "result": "done",
                    "usage": {"input_tokens": 100, "output_tokens": 30},
                    "total_cost_usd": 0.015,
                }
            ),
        ]
        r = ClaudeCodeAdapter().parse_stream(lines)
        assert r.final_text == "done"
        assert r.input_tokens == 100 and r.output_tokens == 30
        assert abs(r.cost_usd - 0.015) < 1e-9
        assert len(r.tool_events) == 1 and r.tool_events[0]["tool"] == "read_file"
        assert not r.is_error

    def test_parse_stream_error(self):
        lines = [json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True})]
        r = ClaudeCodeAdapter().parse_stream(lines)
        assert r.is_error

    def test_parse_tolerates_garbage(self):
        r = ClaudeCodeAdapter().parse_stream(["not json", "", "{bad"])
        assert r.final_text == "" and not r.tool_events


class TestCodexAdapter:
    def test_build_argv(self):
        argv = CodexAdapter().build_argv("task")
        assert argv[:2] == ["codex", "exec"] and "task" in argv

    def test_parse_lenient_text_and_usage(self):
        lines = [
            json.dumps({"text": "hello"}),
            json.dumps({"message": "world"}),
            json.dumps({"usage": {"input_tokens": 5, "output_tokens": 7}}),
        ]
        r = CodexAdapter().parse_stream(lines)
        assert "hello" in r.final_text and "world" in r.final_text
        assert r.input_tokens == 5 and r.output_tokens == 7
