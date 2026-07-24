"""客卿适配器 —— 外部 coding agent 的 headless CLI 契约与注册表。

每个适配器声明:①如何拼 argv(headless 非交互模式);②该 CLI 用哪些环境
变量做自身鉴权(客卿用**自己的**凭证,天枢 clean-env 只放行这些);③如何
把 CLI 的 JSONL 流解析成统一的 KeqingRunResult(最终文本 + usage/cost + 工具
事件)。注册表复用鸿胪寺 EngineRegistry 的可插拔模式。

解析刻意做得**宽容**:不同 CLI(乃至同 CLI 不同版本)的 JSONL 字段名有差异,
逐行尽力抽取 text/usage/cost/tool,抽不到就跳过——评估失败安全,宁可信息
少也不崩。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass
class KeqingRunResult:
    """一次客卿运行的归一化结果。"""

    final_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None  # CLI 若自报成本(Claude Code total_cost_usd)则填
    tool_events: list[dict] = field(default_factory=list)
    error: str | None = None
    is_error: bool = False


class KeqingAdapter(Protocol):
    name: str
    auth_env_vars: tuple[str, ...]

    def build_argv(self, prompt: str, *, model: str | None = None) -> list[str]: ...

    def is_canonical_argv(self, argv: Sequence[str]) -> bool: ...

    def parse_stream(self, lines: list[str]) -> KeqingRunResult: ...


def _as_int(v: object) -> int:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


class ClaudeCodeAdapter:
    """Claude Code headless:`claude -p <prompt> --output-format stream-json --verbose`。

    stream-json 是 JSONL:每行一个事件对象。关注两类:
    - ``type=assistant`` 的 message.content 里 text/tool_use 块(过程);
    - ``type=result``(终态):``result`` 文本 + ``usage`` + ``total_cost_usd``。
    """

    name = "claude-code"
    # 客卿用自身 Anthropic 凭证;天枢不透传自己的 TIANSHU_* secrets
    auth_env_vars = (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
    )

    def build_argv(self, prompt: str, *, model: str | None = None) -> list[str]:
        argv = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        if model:
            argv += ["--model", model]
        return argv

    def is_canonical_argv(self, argv: Sequence[str]) -> bool:
        values = tuple(argv)
        return (
            len(values) in {6, 8}
            and Path(values[0]).name == "claude"
            and values[1] == "-p"
            and bool(values[2])
            and values[3:6] == ("--output-format", "stream-json", "--verbose")
            and (len(values) == 6 or (values[6] == "--model" and bool(values[7])))
        )

    def parse_stream(self, lines: list[str]) -> KeqingRunResult:
        r = KeqingRunResult()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "assistant":
                for block in (evt.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        r.tool_events.append(
                            {"type": "tool.called", "tool": block.get("name", "?")}
                        )
            elif etype == "result":
                # 终态:最终文本 + usage + cost
                if isinstance(evt.get("result"), str):
                    r.final_text = evt["result"]
                usage = evt.get("usage") or {}
                r.input_tokens = _as_int(usage.get("input_tokens"))
                r.output_tokens = _as_int(usage.get("output_tokens"))
                cost = evt.get("total_cost_usd")
                if isinstance(cost, int | float):
                    r.cost_usd = float(cost)
                if evt.get("subtype") not in (None, "success") or evt.get("is_error"):
                    r.is_error = True
                    r.error = evt.get("subtype") or "keqing run reported error"
        return r


class CodexAdapter:
    """Codex headless:`codex exec --json <prompt>`。

    Codex 的 JSONL 事件结构与 Claude Code 不同且随版本演进;这里做宽容解析
    (抽 text/usage 尽力而为),字段对不上时降级为纯文本拼接。生产使用前建议
    按实际 CLI 版本校准字段名。
    """

    name = "codex"
    auth_env_vars = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME")

    def build_argv(self, prompt: str, *, model: str | None = None) -> list[str]:
        argv = ["codex", "exec", "--json", prompt]
        if model:
            argv += ["--model", model]
        return argv

    def is_canonical_argv(self, argv: Sequence[str]) -> bool:
        values = tuple(argv)
        return (
            len(values) in {4, 6}
            and Path(values[0]).name == "codex"
            and values[1:3] == ("exec", "--json")
            and bool(values[3])
            and (len(values) == 4 or (values[4] == "--model" and bool(values[5])))
        )

    def parse_stream(self, lines: list[str]) -> KeqingRunResult:
        r = KeqingRunResult()
        texts: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            # 宽容抽取:任何带 text/message/content 字符串的行都并入正文
            for key in ("text", "message", "content", "delta"):
                val = evt.get(key)
                if isinstance(val, str) and val:
                    texts.append(val)
            usage = evt.get("usage") or evt.get("token_usage") or {}
            if isinstance(usage, dict):
                r.input_tokens = (
                    _as_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
                    or r.input_tokens
                )
                r.output_tokens = (
                    _as_int(usage.get("output_tokens") or usage.get("completion_tokens"))
                    or r.output_tokens
                )
            if evt.get("type") in ("tool_call", "function_call"):
                r.tool_events.append({"type": "tool.called", "tool": evt.get("name", "?")})
        r.final_text = "\n".join(texts).strip()
        return r


class OpenCodeAdapter:
    """opencode headless:`opencode run --format json <prompt>`。

    json 模式逐行输出 `{type, timestamp, sessionID, ...data}`:
    - ``message.part.updated`` 带 ``part``:type=text(终态 part.time.end)取文本;
      type=tool(state.status=completed/error)记工具事件。
    - ``message.updated``(info.role=assistant):取 info.tokens / info.cost。
    宽容解析——opencode 事件 schema 随版本演进,字段对不上时降级不崩(同 codex 惯例);
    生产使用前建议按实际 opencode 版本校准字段名。
    """

    name = "opencode"
    # opencode 自管凭证(自己的 provider 配置/登录);天枢 clean-env 只放行这些候选变量。
    auth_env_vars = (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
    )

    def build_argv(self, prompt: str, *, model: str | None = None) -> list[str]:
        argv = ["opencode", "run", "--format", "json", prompt]
        if model:
            argv += ["--model", model]
        return argv

    def is_canonical_argv(self, argv: Sequence[str]) -> bool:
        values = tuple(argv)
        return (
            len(values) in {5, 7}
            and Path(values[0]).name == "opencode"
            and values[1:4] == ("run", "--format", "json")
            and bool(values[4])
            and (len(values) == 5 or (values[5] == "--model" and bool(values[6])))
        )

    def parse_stream(self, lines: list[str]) -> KeqingRunResult:
        r = KeqingRunResult()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = evt.get("type")
            if etype == "message.part.updated":
                part = evt.get("part") or {}
                ptype = part.get("type")
                if ptype == "text" and (part.get("time") or {}).get("end"):
                    text = (part.get("text") or "").strip()
                    if text:
                        r.final_text = text
                elif ptype == "tool":
                    state = part.get("state") or {}
                    if state.get("status") in ("completed", "error"):
                        r.tool_events.append(
                            {"type": "tool.called", "tool": part.get("tool", "?")}
                        )
                        if state.get("status") == "error":
                            r.is_error = True
            elif etype == "message.updated":
                info = evt.get("info") or {}
                if info.get("role") == "assistant":
                    tokens = info.get("tokens") or {}
                    r.input_tokens = _as_int(tokens.get("input")) or r.input_tokens
                    r.output_tokens = _as_int(tokens.get("output")) or r.output_tokens
                    cost = info.get("cost")
                    if isinstance(cost, int | float):
                        r.cost_usd = float(cost)
        return r


_REGISTRY: dict[str, KeqingAdapter] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter(),
    CodexAdapter.name: CodexAdapter(),
    OpenCodeAdapter.name: OpenCodeAdapter(),
}


def _register_session_backends() -> None:
    """会话/多档 backend(pi 等)在独立模块;延迟 import 破循环后注册进 _REGISTRY。

    pi_adapter 反向 import 本模块的 KeqingRunResult/_as_int,故在此末尾延迟注册,
    避免模块级循环 import。
    """
    from tianshu.executor.keqing.pi_adapter import PiAdapter

    _REGISTRY.setdefault(PiAdapter.name, PiAdapter())


_register_session_backends()


def get_adapter(name: str) -> KeqingAdapter | None:
    return _REGISTRY.get(name)


def is_canonical_adapter_argv(name: str, argv: Sequence[str]) -> bool:
    adapter = get_adapter(name)
    return adapter is not None and adapter.is_canonical_argv(argv)


def list_adapters() -> list[str]:
    return sorted(_REGISTRY.keys())
