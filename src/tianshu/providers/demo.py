"""确定性零网络 demo LLM 后端（G1.5 Slice B1）。

精确 opt-in：``startup_profile=demo`` 使装配层（wiring_llm）把解析路径掩蔽为
``mock/tianshu-demo``；``LLMClient`` 按该精确模型名在任何 LiteLLM/Router 调用
之前分派到本模块（分派本身只看模型名——live 档位手工保存该保留名亦会得到
带 ``[DEMO]`` 标记的零成本响应，可见且无害）。live provider 的错误永远保持
为错误，绝不回退到这里。

确定性：响应只依赖 messages/tools 内容（稳定短哈希区分多轮），不使用任何
可变计数器；全部 usage/cost 恒为零；所有可见输出携带 ``[DEMO]`` 标记。
本模块自身不直接 import LiteLLM（零网络由运行时负向测试锁定），不建立任何 socket。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from tianshu.config_manager import LLMConfigState
from tianshu.models import UsageSummary

if TYPE_CHECKING:
    from tianshu.llm import LLMResponse

DEMO_MODEL = "mock/tianshu-demo"
DEMO_PROVIDER = "demo"
DEMO_MARK = "[DEMO]"

# 请求形状识别标记（与生产 prompt 模板的固定文本绑定；模板变更会由
# 对应形状测试的解析断言失败暴露，而不是静默错配）。
_PLANNER_MARKERS = ("规划职责", "敕令旨意：")
_AUDIT_MARKER = "You are a quality reviewer"


def demo_llm_config_state() -> LLMConfigState:
    """demo profile 的 runtime-only 配置状态；绝不持久化到 llm_configs。"""
    return LLMConfigState(
        name=DEMO_PROVIDER,
        model=DEMO_MODEL,
        api_key="",
        api_base="",
        max_retries=0,
        temperature=0.0,
        top_p=1.0,
        max_tokens=4096,
    )


def _zero_usage() -> UsageSummary:
    return UsageSummary(
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_cny=0.0,
        actual_model=DEMO_MODEL,
        upstream_provider=DEMO_PROVIDER,
    )


def _stable_digest(messages: list[dict], tools: list[dict] | None) -> str:
    payload = json.dumps(
        {"messages": messages, "tools": [t.get("function", {}).get("name") for t in tools or []]},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _messages_text(messages: list[dict]) -> str:
    chunks: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    chunks.append(block["text"])
    return "\n".join(chunks)


def _has_tool_result(messages: list[dict]) -> bool:
    return any(message.get("role") == "tool" for message in messages)


def _tool_names(tools: list[dict] | None) -> set[str]:
    names: set[str] = set()
    for tool in tools or []:
        function = tool.get("function")
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            names.add(function["name"])
    return names


def _planner_payload(digest: str) -> str:
    # 字段与 tianshu.models.plan.PlanTask 对齐（生产解析器直接消费）
    return json.dumps(
        {
            "complexity": "simple",
            "reasoning": f"{DEMO_MARK} deterministic demo plan {digest}",
            "tasks": [
                {
                    "task_id": "main",
                    "description": f"{DEMO_MARK} execute the demo goal deterministically",
                    "depends_on": [],
                    "assigned_official": "bingbu",
                }
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _audit_payload(digest: str) -> str:
    return json.dumps(
        {"verdict": "pass", "reasons": [f"{DEMO_MARK} deterministic demo audit {digest}"]},
        ensure_ascii=False,
        sort_keys=True,
    )


def _build_response(messages: list[dict], tools: list[dict] | None) -> LLMResponse:
    # 局部导入：llm.py 在其分派点导入本模块，顶层互引会形成 import 环。
    from tianshu.llm import LLMResponse

    digest = _stable_digest(messages, tools)
    text = _messages_text(messages)

    if any(marker in text for marker in _PLANNER_MARKERS):
        return LLMResponse(
            content=_planner_payload(digest),
            tool_calls=None,
            usage=_zero_usage(),
            finish_reason="stop",
        )
    if _AUDIT_MARKER in text:
        return LLMResponse(
            content=_audit_payload(digest),
            tool_calls=None,
            usage=_zero_usage(),
            finish_reason="stop",
        )
    if "write_file" in _tool_names(tools) and not _has_tool_result(messages):
        args = json.dumps(
            {
                "path": "DEMO.md",
                "content": f"{DEMO_MARK} deterministic demo artifact {digest}\n",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return LLMResponse(
            content=None,
            tool_calls=[{"id": f"demo-call-{digest}", "name": "write_file", "args": args}],
            usage=_zero_usage(),
            finish_reason="tool_calls",
        )
    if _has_tool_result(messages):
        return LLMResponse(
            content=(
                f"{DEMO_MARK} demo task completed deterministically ({digest}). "
                "Artifact DEMO.md was written through the governed tool path."
            ),
            tool_calls=None,
            usage=_zero_usage(),
            finish_reason="stop",
        )
    return LLMResponse(
        content=f"{DEMO_MARK} deterministic demo response ({digest}).",
        tool_calls=None,
        usage=_zero_usage(),
        finish_reason="stop",
    )


async def demo_chat(messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
    await asyncio.sleep(0)  # 真实 await：提供取消点，不吞 CancelledError
    return _build_response(messages, tools)


async def demo_chat_stream(
    messages: list[dict], tools: list[dict] | None = None
) -> AsyncIterator[LLMResponse]:
    from tianshu.llm import LLMResponse

    final = _build_response(messages, tools)
    if final.content:
        midpoint = max(1, len(final.content) // 2)
        for piece in (final.content[:midpoint], final.content[midpoint:]):
            await asyncio.sleep(0)
            yield LLMResponse(content=piece, tool_calls=None, usage=UsageSummary())
    await asyncio.sleep(0)
    # 对齐 live chat_stream 契约：最终 chunk 携带全量累计内容（消费方把
    # 最后一个 chunk 当权威响应，content=None 会丢失 [DEMO] 证据）。
    yield LLMResponse(
        content=final.content,
        tool_calls=final.tool_calls,
        usage=final.usage,
        finish_reason=final.finish_reason,
    )
