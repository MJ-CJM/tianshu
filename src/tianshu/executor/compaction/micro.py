"""Micro compact — per-turn tool result cleanup, zero LLM cost."""

from __future__ import annotations

from tianshu.executor.loop_state import LoopState

_TRUNCATE_MIN_CHARS = 200


def micro_compact(state: LoopState, keep_recent: int = 4) -> LoopState:
    """Truncate old tool results, keeping the most recent ones intact.

    Args:
        state: Current loop state.
        keep_recent: Number of most recent tool-role messages to preserve.

    Returns:
        New LoopState with truncated old tool results.
    """
    messages = list(state.messages)
    tool_indices = [
        i for i, m in enumerate(messages) if m.get("role") == "tool"
    ]

    if not tool_indices:
        return state

    indices_to_truncate = tool_indices[:-keep_recent] if keep_recent > 0 else tool_indices

    changed = False
    for idx in indices_to_truncate:
        original = messages[idx].get("content", "")
        if len(original) <= _TRUNCATE_MIN_CHARS:
            continue
        messages[idx] = {
            **messages[idx],
            "content": _truncate(original),
        }
        changed = True

    if not changed:
        return state

    return state.with_recovery("micro_compact", messages)


def _truncate(content: str) -> str:
    preview = content[:_TRUNCATE_MIN_CHARS]
    return f"[已压缩] (原始 {len(content)} 字符)\n{preview}..."
