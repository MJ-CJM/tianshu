"""PiSessionAdapter(pi RPC 会话档)单元测试:argv/命令编码/事件归一/结算/统计。"""

import json

from tianshu.executor.keqing.adapter import get_adapter
from tianshu.executor.keqing.pi_adapter import PiSessionAdapter
from tianshu.executor.keqing.session import (
    KIND_MESSAGE,
    KIND_RUN_END,
    KIND_RUN_SETTLED,
    KIND_RUN_START,
    KIND_TOOL_END,
    KIND_TOOL_START,
    KIND_UNKNOWN,
)


class TestSessionArgv:
    def test_with_session_dir(self):
        argv = PiSessionAdapter().build_session_argv(session_dir="/w/.t/s")
        assert argv == ["pi", "--mode", "rpc", "--session-dir", "/w/.t/s"]

    def test_no_session_dir_uses_no_session(self):
        argv = PiSessionAdapter().build_session_argv()
        assert argv == ["pi", "--mode", "rpc", "--no-session"]

    def test_with_model(self):
        argv = PiSessionAdapter().build_session_argv(session_dir="/s", model="anthropic/x")
        assert argv == ["pi", "--mode", "rpc", "--session-dir", "/s", "--model", "anthropic/x"]

    def test_rpc_argv_passes_grant_validation(self):
        # grant 校验用单发 PiAdapter.is_canonical_argv(两执行路径共用),须放行 RPC form
        pi = get_adapter("pi")
        assert pi.is_canonical_argv(PiSessionAdapter().build_session_argv(session_dir="/s"))
        assert pi.is_canonical_argv(PiSessionAdapter().build_session_argv())
        assert pi.is_canonical_argv(
            PiSessionAdapter().build_session_argv(session_dir="/s", model="m")
        )

    def test_grant_rejects_tampered_rpc_argv(self):
        pi = get_adapter("pi")
        assert not pi.is_canonical_argv(["pi", "--mode", "rpc", "--evil-flag"])
        assert not pi.is_canonical_argv(["pi", "--mode", "rpc", "--session-dir"])  # 缺值
        assert not pi.is_canonical_argv(["pi", "--mode", "rpc", "--model"])  # 缺值


class TestEncodeCommand:
    def test_prompt_frame(self):
        raw = PiSessionAdapter().encode_command("prompt", cmd_id="c1", message="do X")
        assert raw.endswith(b"\n")  # LF 分帧
        frame = json.loads(raw)
        assert frame == {"type": "prompt", "id": "c1", "message": "do X"}

    def test_follow_up_frame(self):
        frame = json.loads(PiSessionAdapter().encode_command("follow_up", cmd_id="c2", message="fix it"))
        assert frame == {"type": "follow_up", "id": "c2", "message": "fix it"}

    def test_extra_fields_and_none_dropped(self):
        frame = json.loads(
            PiSessionAdapter().encode_command(
                "prompt", cmd_id="c3", message="m", streamingBehavior="steer", images=None
            )
        )
        assert frame == {"type": "prompt", "id": "c3", "message": "m", "streamingBehavior": "steer"}
        assert "images" not in frame  # None 被丢弃

    def test_ui_response_uses_request_id(self):
        frame = json.loads(
            PiSessionAdapter().encode_command("extension_ui_response", cmd_id="req-9", cancelled=True)
        )
        assert frame == {"type": "extension_ui_response", "id": "req-9", "cancelled": True}


class TestParseEvent:
    def test_lifecycle_events(self):
        a = PiSessionAdapter()
        assert a.parse_event({"type": "agent_start"}).kind == KIND_RUN_START
        end = a.parse_event({"type": "agent_end", "messages": [], "willRetry": True})
        assert end.kind == KIND_RUN_END and end.will_retry is True
        settled = a.parse_event({"type": "agent_settled"})
        assert settled.kind == KIND_RUN_SETTLED and a.is_settled(settled)

    def test_tool_events(self):
        a = PiSessionAdapter()
        start = a.parse_event({"type": "tool_execution_start", "toolName": "bash"})
        assert start.kind == KIND_TOOL_START and start.tool_name == "bash"
        end = a.parse_event({"type": "tool_execution_end", "toolName": "bash", "isError": True})
        assert end.kind == KIND_TOOL_END and end.is_error is True

    def test_message_text_extraction(self):
        a = PiSessionAdapter()
        ev = a.parse_event(
            {
                "type": "message_end",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
            }
        )
        assert ev.kind == KIND_MESSAGE and ev.text == "hi"

    def test_unknown_event_sinks_to_unknown(self):
        # pi 未来加新事件 → 沉降 unknown 不炸(向前兼容)
        ev = PiSessionAdapter().parse_event({"type": "future_event_2028"})
        assert ev.kind == KIND_UNKNOWN and ev.raw_type == "future_event_2028"

    def test_is_settled_only_for_settled(self):
        a = PiSessionAdapter()
        assert not a.is_settled(a.parse_event({"type": "agent_end", "willRetry": False}))


class TestStats:
    def test_extract_session_stats(self):
        stats = PiSessionAdapter().extract_stats(
            {"tokens": {"input": 50000, "output": 10000, "total": 60000}, "cost": 0.45}
        )
        assert stats.input_tokens == 50000
        assert stats.output_tokens == 10000
        assert stats.cost_usd == 0.45

    def test_extract_stats_tolerates_missing(self):
        stats = PiSessionAdapter().extract_stats({})
        assert stats.input_tokens == 0 and stats.cost_usd is None


class TestResponse:
    def test_is_response(self):
        a = PiSessionAdapter()
        assert a.is_response({"type": "response", "command": "prompt", "success": True})
        assert not a.is_response({"type": "agent_settled"})


class TestCapabilities:
    def test_p2_capabilities_honest(self):
        # P2 裸跑:permission_shaping/hooks 为 none(P4 guard 后升级);会话能力已生效
        caps = PiSessionAdapter().capabilities
        assert caps.permission_shaping == "none"
        assert caps.hooks == "none"
        assert caps.session_resume is True
        assert caps.interject is True
        assert caps.usage_reporting == "full"
        assert caps.stop_gate is True
