"""Reactive compact — triggered by API context overflow errors."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tianshu.executor.compaction.auto import auto_compact
from tianshu.executor.compaction.micro import micro_compact
from tianshu.executor.compaction.token_estimator import estimate_tokens
from tianshu.executor.loop_state import LoopState

if TYPE_CHECKING:
    from tianshu.llm import LLMClient

logger = logging.getLogger(__name__)


async def reactive_compact(
    state: LoopState,
    llm: LLMClient,
    context_limit: int,
) -> LoopState | None:
    """Two-step recovery for context overflow errors.

    1. Aggressive micro compact (keep_recent=2)
    2. If still over limit, fall back to auto compact
    Returns None if both fail.
    """
    # Step 1: aggressive micro compact
    aggressive = micro_compact(state, keep_recent=2)
    if aggressive.messages != state.messages and estimate_tokens(aggressive.messages) <= int(
        context_limit * 0.9
    ):
        logger.info("Reactive compact: aggressive micro compact sufficient")
        return aggressive

    # Step 2: auto compact
    try:
        result = await auto_compact(aggressive, llm, context_limit)
    except Exception:
        logger.warning("Reactive compact: auto compact failed", exc_info=True)
        return None
    if result.messages == state.messages:
        # 消息一字未变（如纯 user/assistant 历史 + 条数低于 auto 阈值）：
        # 返回原 state 会让调用方原样重发同一批消息 → 立刻再次 overflow，
        # 形成无退避死循环直到超时。如实返回 None 走 CONTEXT_OVERFLOW 收工。
        logger.warning("Reactive compact: no-op (messages unchanged), giving up")
        return None
    logger.info("Reactive compact: auto compact succeeded")
    return result
