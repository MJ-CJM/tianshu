"""PiAdapter —— pi(pi-mono, MIT, 多 provider)客卿的 headless 单发契约。

天枢默认客卿(开源 + 多 provider 不锁定)。单发档(P1):
`pi --mode json --no-session <prompt>` 输出 JSONL 事件流,逐行抽 assistant 文本、
累加 usage/cost、收集工具事件,归一成 KeqingRunResult(与 claude-code/codex 同外观)。

会话档(RPC 长连接 + follow_up 验收回灌)见 P2 的 keqing/session.py + session_executor.py;
本单发档保留为会话档不可用时的降级路径,并先钉死注册/grant/argv 校验/成本口径。

pi 是多 provider:直连档放行常见 provider 自身鉴权变量(P3 改为天枢网关 scoped token,
raw key 不再进客卿进程)。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from tianshu.executor.keqing.adapter import KeqingRunResult, _as_int
from tianshu.executor.keqing.pi_wire import (
    EVT_AGENT_END,
    EVT_MESSAGE_END,
    EVT_SESSION,
    EVT_TOOL_EXEC_START,
    EVT_TURN_END,
    VERIFIED_SESSION_VERSION,
)
from tianshu.executor.keqing.session import (
    KIND_MESSAGE,
    KIND_RUN_END,
    KIND_RUN_SETTLED,
    KIND_RUN_START,
    KIND_TOOL_END,
    KIND_TOOL_START,
    KIND_TURN_END,
    KIND_UI_REQUEST,
    KIND_UNKNOWN,
    AgentCapabilities,
    CanonicalAgentEvent,
    SessionRunStats,
)

logger = logging.getLogger(__name__)


class PiAdapter:
    """pi 单发 headless 适配器。与 KeqingAdapter Protocol 兼容。"""

    name = "pi"
    # 直连档:放行常见 provider 自身鉴权变量(实际透传仍受 contract.secret_refs 门控);
    # P3 网关就位后切为 PI_GATEWAY_TOKEN + 网关 baseUrl,raw provider key 一律不列。
    auth_env_vars = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
    )

    def build_argv(self, prompt: str, *, model: str | None = None) -> list[str]:
        argv = ["pi", "--mode", "json", "--no-session", prompt]
        if model:
            argv += ["--model", model]
        return argv

    def is_canonical_argv(self, argv: Sequence[str]) -> bool:
        # 接受两种规范 pi 调用:单发(--mode json)与会话(--mode rpc)。grant 校验
        # (gateway.py:548 → is_canonical_adapter_argv(backend='pi'))对两条执行路径共用
        # 本方法,故会话档的 `pi --mode rpc ...` 也须在此放行。
        return _is_canonical_pi_single_shot(argv) or _is_canonical_pi_session(argv)

    def parse_stream(self, lines: list[str]) -> KeqingRunResult:
        r = KeqingRunResult()
        saw_message_end = False
        agent_end_messages: list | None = None
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == EVT_SESSION:
                # 首行 header:探测协议版本漂移,不静默出错(支持 pi 演进的可见性关卡)。
                ver = evt.get("version")
                if ver is not None and ver != VERIFIED_SESSION_VERSION:
                    logger.warning(
                        "pi session 格式版本 %s 与天枢已验证的 %s 不符;宽容解析继续,"
                        "但建议重跑 pi 契约测试确认兼容后再更新钉死版本",
                        ver,
                        VERIFIED_SESSION_VERSION,
                    )
            elif etype == EVT_TOOL_EXEC_START:
                r.tool_events.append({"type": "tool.called", "tool": evt.get("toolName", "?")})
            elif etype == EVT_MESSAGE_END:
                # 流式逐消息终态:usage 累加的唯一来源(避免与 turn_end/agent_end 重复计)
                saw_message_end = True
                self._absorb_message(r, evt.get("message"))
            elif etype == EVT_TURN_END:
                # turn_end.message 与 message_end 是同一条,只用于工具结果错误位,不再累加
                pass
            elif etype == EVT_AGENT_END:
                agent_end_messages = evt.get("messages")
        # 兜底:若整流没有 message_end(异常/极短运行),从 agent_end.messages 归一一次
        if not saw_message_end and agent_end_messages:
            for msg in agent_end_messages:
                self._absorb_message(r, msg)
        return r

    def _absorb_message(self, r: KeqingRunResult, message: object) -> None:
        """把一条 assistant 消息并入结果:文本取最后一条、usage/cost 逐条累加。"""
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return
        texts = [
            block.get("text", "")
            for block in message.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = "".join(texts).strip()
        if joined:
            r.final_text = joined
        usage = message.get("usage") or {}
        if isinstance(usage, dict):
            r.input_tokens += _as_int(usage.get("input"))
            r.output_tokens += _as_int(usage.get("output"))
            cost = (usage.get("cost") or {}).get("total")
            if isinstance(cost, int | float):
                r.cost_usd = (r.cost_usd or 0.0) + float(cost)
        stop = message.get("stopReason")
        if stop in ("error", "aborted") or message.get("errorMessage"):
            r.is_error = True
            r.error = message.get("errorMessage") or stop or "pi run reported error"


def _is_canonical_pi_single_shot(argv: Sequence[str]) -> bool:
    """`pi --mode json --no-session <prompt> [--model M]`。"""
    values = tuple(argv)
    return (
        len(values) in {5, 7}
        and Path(values[0]).name == "pi"
        and values[1:4] == ("--mode", "json", "--no-session")
        and bool(values[4])
        and (len(values) == 5 or (values[5] == "--model" and bool(values[6])))
    )


def _is_canonical_pi_session(argv: Sequence[str]) -> bool:
    """`pi --mode rpc [--session-dir DIR | --no-session] [--model M]`(P2 会话档)。

    严格白名单:仅允许 --session-dir/--no-session/--model 这几个 flag 的规范排列,
    杜绝借 keqing:pi grant 夹带任意参数(与单发档同等 argv 治理强度)。
    """
    values = tuple(argv)
    if not values or Path(values[0]).name != "pi" or values[1:3] != ("--mode", "rpc"):
        return False
    rest = list(values[3:])
    # 会话存储:--session-dir <path> 或 --no-session(二选一或都不给)
    if rest[:1] == ["--session-dir"]:
        if len(rest) < 2 or not rest[1]:
            return False
        rest = rest[2:]
        # 可选 --continue(follow_up 续同会话上下文)——仅 --session-dir 后合法
        if rest[:1] == ["--continue"]:
            rest = rest[1:]
    elif rest[:1] == ["--no-session"]:
        rest = rest[1:]
    # 可选 --model <value>
    if rest[:1] == ["--model"]:
        if len(rest) != 2 or not rest[1]:
            return False
        rest = rest[2:]
    return rest == []


class PiSessionAdapter:
    """pi RPC 会话档适配器(实现 KeqingSessionAdapter Protocol)。

    契约来自 pi 0.81.1 docs/rpc.md:LF-only JSONL、命令带 id、结算只认 agent_settled、
    follow_up 同会话回灌、get_session_stats 聚合成本。
    """

    name = "pi"
    dialect = "pi"
    auth_env_vars = PiAdapter.auth_env_vars
    capabilities = AgentCapabilities(
        permission_shaping="none",  # P4 tianshu-guard 就位后升为 "full"
        hooks="none",  # P4 后升为 "in_process"
        stop_gate=True,  # settled 后跑 acceptance checks
        session_resume=True,  # follow_up 同会话回灌整改(免重启)
        interject=True,  # steer/follow_up
        usage_reporting="full",  # get_session_stats
    )

    def build_session_argv(
        self, *, session_dir: str | None = None, model: str | None = None, resume: bool = False
    ) -> list[str]:
        argv = ["pi", "--mode", "rpc"]
        if session_dir:
            argv += ["--session-dir", session_dir]
            # follow_up 续同一会话:--continue 让 pi 加载上次对话上下文(连续对话有记忆)。
            if resume:
                argv += ["--continue"]
        else:
            argv += ["--no-session"]
        if model:
            argv += ["--model", model]
        return argv

    def encode_command(
        self,
        cmd_type: str,
        *,
        cmd_id: str | None = None,
        message: str | None = None,
        **fields: object,
    ) -> bytes:
        frame: dict[str, object] = {"type": cmd_type}
        if cmd_id is not None:
            frame["id"] = cmd_id
        if message is not None:
            frame["message"] = message
        for key, val in fields.items():
            if val is not None:
                frame[key] = val
        # LF-only JSONL(pi jsonl.ts:剥 CR,只按 \n 切);json.dumps 默认无内嵌换行。
        return (json.dumps(frame, ensure_ascii=False) + "\n").encode("utf-8")

    def is_response(self, frame: dict) -> bool:
        return frame.get("type") == "response"

    def parse_event(self, frame: dict) -> CanonicalAgentEvent:
        etype = frame.get("type")
        if etype == "agent_start":
            return CanonicalAgentEvent(KIND_RUN_START, self.dialect, raw_type=etype)
        if etype == "agent_end":
            return CanonicalAgentEvent(
                KIND_RUN_END,
                self.dialect,
                raw_type=etype,
                will_retry=bool(frame.get("willRetry")),
            )
        if etype == "agent_settled":
            return CanonicalAgentEvent(KIND_RUN_SETTLED, self.dialect, raw_type=etype)
        if etype == "turn_end":
            return CanonicalAgentEvent(KIND_TURN_END, self.dialect, raw_type=etype)
        if etype == "tool_execution_start":
            return CanonicalAgentEvent(
                KIND_TOOL_START,
                self.dialect,
                raw_type=etype,
                tool_name=frame.get("toolName"),
            )
        if etype == "tool_execution_end":
            return CanonicalAgentEvent(
                KIND_TOOL_END,
                self.dialect,
                raw_type=etype,
                tool_name=frame.get("toolName"),
                is_error=bool(frame.get("isError")),
            )
        if etype == "message_end":
            msg = frame.get("message") or {}
            text = None
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                text = (
                    "".join(
                        b.get("text", "")
                        for b in msg.get("content") or []
                        if isinstance(b, dict) and b.get("type") == "text"
                    ).strip()
                    or None
                )
            return CanonicalAgentEvent(KIND_MESSAGE, self.dialect, raw_type=etype, text=text)
        if etype == "extension_ui_request":
            return CanonicalAgentEvent(KIND_UI_REQUEST, self.dialect, raw_type=etype)
        return CanonicalAgentEvent(KIND_UNKNOWN, self.dialect, raw_type=etype)

    def is_settled(self, event: CanonicalAgentEvent) -> bool:
        # pi 0.81.1 用 agent_settled(run_settled)作结算锚;0.79.3 无此事件,以
        # agent_end(will_retry=False)为完成信号。兼容两版本:任一即视为 settle,
        # 否则装 0.79.3 的用户会在 send prompt 后一直等 agent_settled 直到 session timeout。
        return event.kind == KIND_RUN_SETTLED or (
            event.kind == KIND_RUN_END and not event.will_retry
        )

    def extract_stats(self, data: dict) -> SessionRunStats:
        tokens = data.get("tokens") or {}
        cost = data.get("cost")
        return SessionRunStats(
            input_tokens=_as_int(tokens.get("input")),
            output_tokens=_as_int(tokens.get("output")),
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
        )
