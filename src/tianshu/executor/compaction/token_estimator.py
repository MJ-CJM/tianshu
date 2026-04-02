"""Estimate token count from messages without calling a tokenizer."""

from __future__ import annotations

from collections.abc import Sequence


def estimate_tokens(messages: Sequence[dict]) -> int:
    """Estimate token count: ~3 chars per token (conservative for mixed CJK/Latin).

    Uses len(content) // 3 which intentionally over-estimates to avoid
    under-counting that could cause context overflow.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 3
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    total += len(block["text"]) // 3
    return total
