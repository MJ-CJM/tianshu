"""Auto compact — threshold-triggered LLM summarization with circuit breaker."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from tianshu.executor.compaction.token_estimator import estimate_tokens
from tianshu.executor.loop_state import LoopState

if TYPE_CHECKING:
    from tianshu.llm import LLMClient

logger = logging.getLogger(__name__)

COMPACT_THRESHOLD_RATIO = 0.75
COMPACT_BUFFER_TOKENS = 20_000
MAX_CONSECUTIVE_FAILURES = 3  # TODO: implement circuit breaker to stop retrying after N failures
PRESERVE_TAIL = 6

_COMPACT_PROMPT = """你是一个对话压缩助手。请将以下对话历史压缩为简洁的摘要。

要求:
1. 保留所有关键决策、执行结果和错误信息
2. 保留文件路径、函数名等具体技术细节
3. 按时间顺序组织
4. 不要添加对话中没有的信息
5. 使用简洁的要点格式

对话内容:
{conversation}

请输出压缩摘要:"""


def should_auto_compact(
    messages: Sequence[dict],
    context_limit: int,
) -> bool:
    """Check whether auto compaction should trigger."""
    if len(messages) <= 8:
        return False
    estimated = estimate_tokens(messages)
    threshold = int(context_limit * COMPACT_THRESHOLD_RATIO)
    return estimated > threshold


async def auto_compact(
    state: LoopState,
    llm: "LLMClient",
    context_limit: int,
) -> LoopState:
    """Summarize middle messages via LLM, keeping head and tail."""
    messages = list(state.messages)
    if len(messages) <= 8:
        return state

    head = messages[:1]
    middle = messages[1:-PRESERVE_TAIL]
    tail = messages[-PRESERVE_TAIL:]

    if not middle:
        return state

    conversation = _format_for_summary(middle)
    prompt = _COMPACT_PROMPT.format(conversation=conversation)

    response = await llm.chat(
        messages=[{"role": "user", "content": prompt}],
    )
    summary = response.content or ""

    compact_msg = {
        "role": "user",
        "content": f"[以下是之前对话的压缩摘要，不要回复此消息]\n\n{summary}",
    }

    new_messages = head + [compact_msg] + tail
    return state.with_compacted(new_messages)


def _format_for_summary(messages: list[dict]) -> str:
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = str(msg.get("content", ""))
        if len(content) > 500:
            content = content[:500] + "..."
        parts.append(f"[{role}] {content}")
    return "\n\n".join(parts)
