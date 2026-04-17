# Phase 1: Agent Core Loop Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed `for iteration in range(max_iterations)` agent loop with a `while` loop featuring explicit exit reasons, immutable state, multi-strategy context compaction, and layered error recovery.

**Architecture:** The agent loop becomes a `while state.iteration < max_iterations` loop where `LoopState` is an immutable frozen dataclass replaced each turn. Three compaction strategies (micro/auto/reactive) run in a pipeline, and a recovery chain handles output truncation and context overflow errors before falling back to failure.

**Tech Stack:** Python 3.12+, Pydantic v2, LiteLLM, pytest, asyncio

**Spec:** `docs/superpowers/specs/2026-04-02-agent-core-optimization-design.md` (Phase 1)

---

## File Structure

| Operation | File | Responsibility |
|-----------|------|----------------|
| Create | `src/tianshu/executor/exit_reason.py` | ExitReason enum — all possible loop exit causes |
| Create | `src/tianshu/executor/loop_state.py` | Immutable LoopState dataclass with transition methods |
| Create | `src/tianshu/executor/compaction/__init__.py` | Package init, re-exports |
| Create | `src/tianshu/executor/compaction/token_estimator.py` | Token estimation from messages |
| Create | `src/tianshu/executor/compaction/micro.py` | Per-turn tool result cleanup |
| Create | `src/tianshu/executor/compaction/auto.py` | Threshold-triggered LLM summarization |
| Create | `src/tianshu/executor/compaction/reactive.py` | API error recovery compaction |
| Modify | `src/tianshu/llm.py:26-27` | Add `finish_reason` field to LLMResponse |
| Modify | `src/tianshu/executor/agent.py` | Rewrite Agent.execute with new loop model |
| Create | `tests/test_exit_reason.py` | ExitReason tests |
| Create | `tests/test_loop_state.py` | LoopState tests |
| Create | `tests/test_compaction.py` | All compaction strategy tests |
| Modify | `tests/test_agent.py` | Update existing tests + add new loop behavior tests |

---

## Task 1: ExitReason Enum

**Files:**
- Create: `src/tianshu/executor/exit_reason.py`
- Create: `tests/test_exit_reason.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_exit_reason.py
"""Tests for ExitReason enum."""

from tianshu.executor.exit_reason import ExitReason


class TestExitReason:
    def test_all_reasons_are_strings(self):
        for reason in ExitReason:
            assert isinstance(reason.value, str)

    def test_expected_members(self):
        expected = {
            "completed",
            "max_iterations",
            "context_overflow",
            "timeout",
            "cancelled",
            "hook_blocked",
            "budget_exhausted",
            "llm_error",
            "output_truncated",
        }
        actual = {r.value for r in ExitReason}
        assert actual == expected

    def test_string_comparison(self):
        assert ExitReason.COMPLETED == "completed"
        assert ExitReason.MAX_ITERATIONS == "max_iterations"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_exit_reason.py -v`
Expected: `ModuleNotFoundError: No module named 'tianshu.executor.exit_reason'`

- [ ] **Step 3: Write the implementation**

```python
# src/tianshu/executor/exit_reason.py
"""Agent loop exit reasons — each maps to a distinct post-exit handling strategy."""

from __future__ import annotations

from enum import StrEnum


class ExitReason(StrEnum):
    """Why the agent loop terminated."""

    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"
    CONTEXT_OVERFLOW = "context_overflow"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    HOOK_BLOCKED = "hook_blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LLM_ERROR = "llm_error"
    OUTPUT_TRUNCATED = "output_truncated"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_exit_reason.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/executor/exit_reason.py tests/test_exit_reason.py
git commit -m "feat: add ExitReason enum for agent loop exit semantics"
```

---

## Task 2: LoopState Immutable Dataclass

**Files:**
- Create: `src/tianshu/executor/loop_state.py`
- Create: `tests/test_loop_state.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_loop_state.py
"""Tests for LoopState immutable state management."""

import pytest

from tianshu.executor.loop_state import LoopState


class TestLoopState:
    def test_create_initial_state(self):
        state = LoopState(
            messages=({"role": "system", "content": "hello"},),
            iteration=0,
        )
        assert state.iteration == 0
        assert state.transition_reason == "initial"
        assert state.compact_attempted is False
        assert state.output_recovery_count == 0
        assert state.total_compact_count == 0

    def test_frozen_raises_on_mutation(self):
        state = LoopState(messages=(), iteration=0)
        with pytest.raises(AttributeError):
            state.iteration = 1  # type: ignore[misc]

    def test_next_turn_increments_iteration(self):
        state = LoopState(
            messages=({"role": "user", "content": "hi"},),
            iteration=2,
            compact_attempted=True,
            output_recovery_count=1,
            total_compact_count=3,
            total_prompt_tokens=100,
        )
        new_msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hey"}]
        next_state = state.next_turn(new_msgs)

        assert next_state.iteration == 3
        assert next_state.transition_reason == "next_turn"
        # Guards reset
        assert next_state.compact_attempted is False
        assert next_state.output_recovery_count == 0
        # Accumulators preserved
        assert next_state.total_compact_count == 3
        assert next_state.total_prompt_tokens == 100

    def test_with_recovery_preserves_iteration(self):
        state = LoopState(
            messages=({"role": "user", "content": "hi"},),
            iteration=5,
            compact_attempted=True,
            output_recovery_count=2,
        )
        recovered = state.with_recovery(
            "micro_compact",
            [{"role": "user", "content": "compacted"}],
        )

        assert recovered.iteration == 5  # unchanged
        assert recovered.transition_reason == "micro_compact"
        assert recovered.compact_attempted is True  # preserved
        assert recovered.output_recovery_count == 2  # preserved
        assert len(recovered.messages) == 1

    def test_with_compacted_increments_count(self):
        state = LoopState(messages=(), iteration=0, total_compact_count=1)
        compacted = state.with_compacted(
            [{"role": "user", "content": "summary"}]
        )
        assert compacted.total_compact_count == 2
        assert compacted.compact_attempted is True
        assert compacted.transition_reason == "auto_compact"

    def test_accumulate_usage(self):
        state = LoopState(
            messages=(), iteration=0,
            total_prompt_tokens=50, total_completion_tokens=30,
        )
        updated = state.accumulate_usage(prompt=100, completion=60)
        assert updated.total_prompt_tokens == 150
        assert updated.total_completion_tokens == 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_loop_state.py -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/tianshu/executor/loop_state.py
"""Immutable loop state — replaced (never mutated) each turn."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LoopState:
    """Snapshot of the agent loop state at a point in time."""

    messages: tuple[dict, ...]
    iteration: int
    transition_reason: str = "initial"

    # Per-turn guards (reset by next_turn)
    compact_attempted: bool = False
    output_recovery_count: int = 0

    # Session accumulators (never reset)
    total_compact_count: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0

    def next_turn(self, new_messages: list[dict]) -> LoopState:
        """Advance to next iteration: reset guards, keep accumulators."""
        return LoopState(
            messages=tuple(new_messages),
            iteration=self.iteration + 1,
            transition_reason="next_turn",
            compact_attempted=False,
            output_recovery_count=0,
            total_compact_count=self.total_compact_count,
            total_prompt_tokens=self.total_prompt_tokens,
            total_completion_tokens=self.total_completion_tokens,
        )

    def with_recovery(self, reason: str, messages: list[dict]) -> LoopState:
        """Create recovery state: preserve guards, same iteration."""
        return LoopState(
            messages=tuple(messages),
            iteration=self.iteration,
            transition_reason=reason,
            compact_attempted=self.compact_attempted,
            output_recovery_count=self.output_recovery_count,
            total_compact_count=self.total_compact_count,
            total_prompt_tokens=self.total_prompt_tokens,
            total_completion_tokens=self.total_completion_tokens,
        )

    def with_compacted(self, messages: list[dict]) -> LoopState:
        """After auto compact: mark attempted, increment count."""
        return LoopState(
            messages=tuple(messages),
            iteration=self.iteration,
            transition_reason="auto_compact",
            compact_attempted=True,
            output_recovery_count=self.output_recovery_count,
            total_compact_count=self.total_compact_count + 1,
            total_prompt_tokens=self.total_prompt_tokens,
            total_completion_tokens=self.total_completion_tokens,
        )

    def accumulate_usage(self, prompt: int, completion: int) -> LoopState:
        """Return new state with accumulated token usage."""
        return LoopState(
            messages=self.messages,
            iteration=self.iteration,
            transition_reason=self.transition_reason,
            compact_attempted=self.compact_attempted,
            output_recovery_count=self.output_recovery_count,
            total_compact_count=self.total_compact_count,
            total_prompt_tokens=self.total_prompt_tokens + prompt,
            total_completion_tokens=self.total_completion_tokens + completion,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_loop_state.py -v`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/executor/loop_state.py tests/test_loop_state.py
git commit -m "feat: add LoopState immutable dataclass for agent loop state"
```

---

## Task 3: Token Estimator

**Files:**
- Create: `src/tianshu/executor/compaction/__init__.py`
- Create: `src/tianshu/executor/compaction/token_estimator.py`
- Create: `tests/test_compaction.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_compaction.py
"""Tests for compaction strategies."""

import pytest

from tianshu.executor.compaction.token_estimator import estimate_tokens


class TestTokenEstimator:
    def test_empty_messages(self):
        assert estimate_tokens(()) == 0

    def test_simple_text_message(self):
        msgs = ({"role": "user", "content": "hello world"},)  # 11 chars
        result = estimate_tokens(msgs)
        # 11 // 3 = 3
        assert result == 3

    def test_chinese_text(self):
        msgs = ({"role": "user", "content": "你好世界测试"},)  # 6 chars
        result = estimate_tokens(msgs)
        # 6 // 3 = 2
        assert result == 2

    def test_empty_content(self):
        msgs = ({"role": "user", "content": ""},)
        assert estimate_tokens(msgs) == 0

    def test_missing_content_key(self):
        msgs = ({"role": "system"},)
        assert estimate_tokens(msgs) == 0

    def test_multiple_messages(self):
        msgs = (
            {"role": "system", "content": "abc"},      # 3 // 3 = 1
            {"role": "user", "content": "defghi"},      # 6 // 3 = 2
            {"role": "assistant", "content": "jklmnopqr"},  # 9 // 3 = 3
        )
        assert estimate_tokens(msgs) == 6

    def test_list_content_blocks(self):
        msgs = (
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},  # 5 // 3 = 1
                    {"type": "text", "text": "world"},  # 5 // 3 = 1
                ],
            },
        )
        assert estimate_tokens(msgs) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestTokenEstimator -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/tianshu/executor/compaction/__init__.py
"""Multi-strategy context compaction for the agent loop."""
```

```python
# src/tianshu/executor/compaction/token_estimator.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestTokenEstimator -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/executor/compaction/__init__.py src/tianshu/executor/compaction/token_estimator.py tests/test_compaction.py
git commit -m "feat: add token estimator for compaction threshold checks"
```

---

## Task 4: Micro Compact

**Files:**
- Create: `src/tianshu/executor/compaction/micro.py`
- Modify: `tests/test_compaction.py` (append tests)

- [ ] **Step 1: Write the test**

Append to `tests/test_compaction.py`:

```python
from tianshu.executor.loop_state import LoopState
from tianshu.executor.compaction.micro import micro_compact


class TestMicroCompact:
    def _make_state(self, messages: list[dict]) -> LoopState:
        return LoopState(messages=tuple(messages), iteration=0)

    def test_no_tool_messages_unchanged(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=4)
        assert list(result.messages) == msgs

    def test_recent_tool_results_preserved(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "tool_call_id": "1", "content": "result1"},
            {"role": "tool", "tool_call_id": "2", "content": "result2"},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=4)
        # Only 2 tool messages, both within keep_recent=4
        assert result.messages[1]["content"] == "result1"
        assert result.messages[2]["content"] == "result2"

    def test_old_tool_results_truncated(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "tool", "tool_call_id": "1", "content": "A" * 500},
            {"role": "tool", "tool_call_id": "2", "content": "B" * 500},
            {"role": "tool", "tool_call_id": "3", "content": "C" * 500},
            {"role": "tool", "tool_call_id": "4", "content": "D" * 500},
            {"role": "tool", "tool_call_id": "5", "content": "E" * 500},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=2)

        # First 3 tool msgs truncated (indices 1,2,3)
        assert "[已压缩]" in result.messages[1]["content"]
        assert "[已压缩]" in result.messages[2]["content"]
        assert "[已压缩]" in result.messages[3]["content"]
        # Last 2 tool msgs preserved (indices 4,5)
        assert result.messages[4]["content"] == "D" * 500
        assert result.messages[5]["content"] == "E" * 500

    def test_tool_call_id_preserved(self):
        msgs = [
            {"role": "tool", "tool_call_id": "abc", "content": "X" * 1000},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=0)
        assert result.messages[0]["tool_call_id"] == "abc"

    def test_short_tool_results_not_truncated(self):
        msgs = [
            {"role": "tool", "tool_call_id": "1", "content": "short"},
            {"role": "tool", "tool_call_id": "2", "content": "also short"},
        ]
        state = self._make_state(msgs)
        result = micro_compact(state, keep_recent=0)
        # Content <= 200 chars, so no truncation even when not in keep_recent
        assert result.messages[0]["content"] == "short"

    def test_transition_reason(self):
        state = self._make_state([{"role": "tool", "tool_call_id": "1", "content": "X" * 500}])
        result = micro_compact(state, keep_recent=0)
        assert result.transition_reason == "micro_compact"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestMicroCompact -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/tianshu/executor/compaction/micro.py
"""Micro compact — per-turn tool result cleanup, zero LLM cost."""

from __future__ import annotations

from tianshu.executor.loop_state import LoopState

# Truncation threshold: tool results shorter than this are left alone
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
    return f"[已压缩, 原始 {len(content)} 字符]\n{preview}..."
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestMicroCompact -v`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/executor/compaction/micro.py tests/test_compaction.py
git commit -m "feat: add micro compact strategy for per-turn tool result cleanup"
```

---

## Task 5: Auto Compact

**Files:**
- Create: `src/tianshu/executor/compaction/auto.py`
- Modify: `tests/test_compaction.py` (append tests)

- [ ] **Step 1: Write the test**

Append to `tests/test_compaction.py`:

```python
from unittest.mock import AsyncMock, MagicMock

from tianshu.executor.compaction.auto import (
    COMPACT_THRESHOLD_RATIO,
    auto_compact,
    should_auto_compact,
)


class TestAutoCompact:
    def test_should_auto_compact_below_threshold(self):
        # 9 chars total -> 3 tokens. Threshold for 128K = 96000. Not triggered.
        msgs = ({"role": "user", "content": "hi there!"},)
        assert should_auto_compact(msgs, context_limit=128000) is False

    def test_should_auto_compact_above_threshold(self):
        # Create message that exceeds 75% of context_limit=100
        # 75% of 100 = 75 tokens -> need > 75*3 = 225 chars
        big = "x" * 300
        msgs = tuple(
            [{"role": "system", "content": "s"}]
            + [{"role": "user", "content": big}]
            + [{"role": "assistant", "content": f"r{i}"} for i in range(8)]
        )
        assert should_auto_compact(msgs, context_limit=100) is True

    def test_should_auto_compact_too_few_messages(self):
        big = "x" * 300
        msgs = (
            {"role": "system", "content": "s"},
            {"role": "user", "content": big},
        )
        # Only 2 messages, need > 8
        assert should_auto_compact(msgs, context_limit=100) is False

    async def test_auto_compact_summarizes(self):
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(12):
            msgs.append({"role": "user", "content": f"msg {i}"})

        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="Summary of conversation")

        state = LoopState(messages=tuple(msgs), iteration=3)
        result = await auto_compact(state, mock_llm, context_limit=128000)

        # Should have: system + compact_msg + last 6 messages = 8
        assert len(result.messages) == 8
        assert result.messages[0]["role"] == "system"
        assert "摘要" in result.messages[1]["content"] or "Summary" in result.messages[1]["content"]
        assert result.compact_attempted is True
        assert result.total_compact_count == 1
        assert result.transition_reason == "auto_compact"
        mock_llm.chat.assert_called_once()

    async def test_auto_compact_skips_when_few_messages(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        mock_llm = AsyncMock()
        state = LoopState(messages=tuple(msgs), iteration=0)
        result = await auto_compact(state, mock_llm, context_limit=128000)
        # Should return unchanged state
        assert result is state
        mock_llm.chat.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestAutoCompact -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/tianshu/executor/compaction/auto.py
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
MAX_CONSECUTIVE_FAILURES = 3
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestAutoCompact -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/executor/compaction/auto.py tests/test_compaction.py
git commit -m "feat: add auto compact strategy with LLM summarization"
```

---

## Task 6: Reactive Compact

**Files:**
- Create: `src/tianshu/executor/compaction/reactive.py`
- Modify: `tests/test_compaction.py` (append tests)

- [ ] **Step 1: Write the test**

Append to `tests/test_compaction.py`:

```python
from tianshu.executor.compaction.reactive import reactive_compact


class TestReactiveCompact:
    def _make_state_with_tools(self, tool_count: int, content_size: int) -> LoopState:
        msgs: list[dict] = [{"role": "system", "content": "sys"}]
        for i in range(tool_count):
            msgs.append({
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"tc_{i}", "type": "function", "function": {"name": "grep", "arguments": "{}"}}],
            })
            msgs.append({
                "role": "tool",
                "tool_call_id": f"tc_{i}",
                "content": "X" * content_size,
            })
        return LoopState(messages=tuple(msgs), iteration=5)

    async def test_aggressive_micro_sufficient(self):
        state = self._make_state_with_tools(10, 1000)
        result = await reactive_compact(state, mock_llm=AsyncMock(), context_limit=50000)
        assert result is not None
        # Old tool results should be truncated
        tool_msgs = [m for m in result.messages if m.get("role") == "tool"]
        truncated = [m for m in tool_msgs if "[已压缩]" in m.get("content", "")]
        assert len(truncated) > 0

    async def test_falls_back_to_auto_compact(self):
        # Create state that aggressive micro won't fix (context_limit very small)
        state = self._make_state_with_tools(20, 2000)
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="Summary")
        result = await reactive_compact(state, mock_llm=mock_llm, context_limit=100)
        assert result is not None
        assert result.compact_attempted is True

    async def test_returns_none_on_total_failure(self):
        state = self._make_state_with_tools(5, 500)
        mock_llm = AsyncMock()
        mock_llm.chat.side_effect = Exception("LLM down")
        result = await reactive_compact(state, mock_llm=mock_llm, context_limit=10)
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestReactiveCompact -v`
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# src/tianshu/executor/compaction/reactive.py
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
    mock_llm: "LLMClient",
    context_limit: int,
) -> LoopState | None:
    """Two-step recovery for context overflow errors.

    1. Aggressive micro compact (keep_recent=2)
    2. If still over limit, fall back to auto compact
    Returns None if both fail.
    """
    # Step 1: aggressive micro compact
    aggressive = micro_compact(state, keep_recent=2)
    if estimate_tokens(aggressive.messages) <= int(context_limit * 0.9):
        logger.info("Reactive compact: aggressive micro compact sufficient")
        return aggressive

    # Step 2: auto compact
    try:
        result = await auto_compact(aggressive, mock_llm, context_limit)
        logger.info("Reactive compact: auto compact succeeded")
        return result
    except Exception:
        logger.warning("Reactive compact: auto compact failed", exc_info=True)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestReactiveCompact -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/executor/compaction/reactive.py tests/test_compaction.py
git commit -m "feat: add reactive compact for API context overflow recovery"
```

---

## Task 7: Add finish_reason to LLMResponse

**Files:**
- Modify: `src/tianshu/llm.py:26-27,103-104`

- [ ] **Step 1: Write the test**

Append to `tests/test_compaction.py` (reusing the file for related tests):

```python
class TestLLMResponseFinishReason:
    def test_finish_reason_field_exists(self):
        from tianshu.llm import LLMResponse
        from tianshu.models import UsageSummary

        resp = LLMResponse(
            content="hello",
            tool_calls=None,
            usage=UsageSummary(),
            finish_reason="stop",
        )
        assert resp.finish_reason == "stop"

    def test_finish_reason_default_none(self):
        from tianshu.llm import LLMResponse
        from tianshu.models import UsageSummary

        resp = LLMResponse(
            content="hello",
            tool_calls=None,
            usage=UsageSummary(),
        )
        assert resp.finish_reason is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestLLMResponseFinishReason -v`
Expected: `TypeError: LLMResponse.__init__() got an unexpected keyword argument 'finish_reason'`

- [ ] **Step 3: Modify LLMResponse and chat method**

In `src/tianshu/llm.py`, add `finish_reason` field to `LLMResponse`:

```python
# Line 26-27: Add field to LLMResponse
@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[dict] | None
    usage: UsageSummary
    reasoning_content: str | None = None
    finish_reason: str | None = None  # "stop", "length", "tool_calls", etc.
```

In the `chat` method, capture `finish_reason` from the API response (around line 130):

```python
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            usage=usage,
            reasoning_content=getattr(message, "reasoning_content", None),
            finish_reason=getattr(choice, "finish_reason", None),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_compaction.py::TestLLMResponseFinishReason -v`
Expected: 2 tests PASS

- [ ] **Step 5: Run existing tests to verify no regression**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_agent.py -v`
Expected: All existing tests PASS (finish_reason defaults to None)

- [ ] **Step 6: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/llm.py tests/test_compaction.py
git commit -m "feat: add finish_reason to LLMResponse for truncation detection"
```

---

## Task 8: Rewrite Agent.execute with New Loop Model

This is the core task. It replaces the existing `Agent.execute` method with the new while-loop design.

**Files:**
- Modify: `src/tianshu/executor/agent.py`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write the new tests**

Append to `tests/test_agent.py`:

```python
from tianshu.executor.exit_reason import ExitReason


class TestAgentNewLoop:
    """Tests for the redesigned agent loop."""

    @pytest.fixture
    def tools(self):
        return ToolRegistry()

    @pytest.fixture
    def skills(self, tmp_path):
        return SkillsLoader(builtin_dir=tmp_path, char_budget=1000)

    @pytest.fixture
    def agent(self, config_manager, tools, skills):
        return Agent(
            config_manager=config_manager,
            tools=tools,
            skills=skills,
        )

    async def test_exit_reason_completed(self, agent):
        edict = Edict(goal="say hello")
        mock_response = MagicMock(
            content="Hello!",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=5, completion_tokens=5, total_tokens=10),
            finish_reason="stop",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert result.iteration_count == 0

    async def test_exit_reason_max_iterations(self, agent, config_manager):
        """Agent loops until max_iterations when LLM always returns tool calls."""
        edict = Edict(goal="loop forever")
        edict.runtime.max_iterations = 2

        tool_response = MagicMock(
            content="thinking",
            tool_calls=[{"id": "tc1", "name": "nonexistent", "args": "{}"}],
            usage=UsageSummary(),
            finish_reason="tool_calls",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = tool_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.FAILED
        assert result.exit_reason == ExitReason.MAX_ITERATIONS
        assert result.iteration_count == 2

    async def test_exit_reason_cancelled(self, agent):
        edict = Edict(goal="test cancel")
        agent.request_shutdown()

        mock_response = MagicMock(
            content=None,
            tool_calls=[{"id": "tc1", "name": "grep", "args": "{}"}],
            usage=UsageSummary(),
            finish_reason="tool_calls",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.exit_reason == ExitReason.CANCELLED

    async def test_output_truncation_recovery(self, agent):
        """When finish_reason='length', agent injects continuation and retries."""
        edict = Edict(goal="long output")
        truncated = MagicMock(
            content="partial...",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=10, completion_tokens=10, total_tokens=20),
            finish_reason="length",
        )
        complete = MagicMock(
            content="...rest of answer",
            tool_calls=None,
            usage=UsageSummary(prompt_tokens=15, completion_tokens=15, total_tokens=30),
            finish_reason="stop",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = [truncated, complete]
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert result.recovery_attempts.get("output_continuation", 0) >= 1

    async def test_compact_count_tracked(self, agent):
        edict = Edict(goal="test")
        mock_response = MagicMock(
            content="Done",
            tool_calls=None,
            usage=UsageSummary(),
            finish_reason="stop",
        )
        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.return_value = mock_response
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.compact_count == 0
        assert isinstance(result.recovery_attempts, dict)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_agent.py::TestAgentNewLoop -v`
Expected: `AttributeError: 'AgentResult' has no attribute 'exit_reason'`

- [ ] **Step 3: Rewrite Agent.execute and AgentResult**

Replace the content of `src/tianshu/executor/agent.py` with the new implementation. Key changes:

1. Import `ExitReason`, `LoopState`, `micro_compact`, `should_auto_compact`, `auto_compact`, `reactive_compact`
2. Add `exit_reason`, `iteration_count`, `compact_count`, `recovery_attempts` fields to `AgentResult`
3. Replace `for iteration in range(max_iterations)` with `while state.iteration < max_iterations`
4. Add micro compact at loop start
5. Add auto compact check
6. Add output truncation recovery (finish_reason == "length")
7. Add context overflow recovery (catch litellm.ContextWindowExceededError)
8. Replace mutable `messages` list with immutable `LoopState`
9. Track recovery attempts in a dict

The full implementation follows the spec's pseudocode in section 1.1.4. The key structural change:

```python
class AgentResult(BaseModel):
    status: TaskStatus
    summary: str | None = None
    result: str | None = None
    usage: UsageSummary = Field(default_factory=UsageSummary)
    error: str | None = None
    events: list[dict] = Field(default_factory=list)
    exit_reason: ExitReason = ExitReason.COMPLETED
    iteration_count: int = 0
    compact_count: int = 0
    recovery_attempts: dict = Field(default_factory=dict)
```

The execute method body becomes:

```python
async def execute(self, edict, on_event=None, history=None,
                  user_content=None, tool_filter=None, persona=None):
    # ... (LLM client setup unchanged) ...

    state = LoopState(
        messages=tuple(messages),
        iteration=0,
    )
    recovery_attempts: dict[str, int] = {}

    while state.iteration < max_iterations:
        if self._shutdown_event.is_set():
            return self._build_result(state, ExitReason.CANCELLED, usage=usage,
                                       events=events, recovery=recovery_attempts)

        # Phase 1: micro compact
        state = micro_compact(state)

        # Phase 2: auto compact
        if should_auto_compact(state.messages, context_limit):
            try:
                state = await auto_compact(state, llm, context_limit)
            except Exception:
                logger.warning("Auto compact failed", exc_info=True)

        # Phase 3: hooks
        # ... (BEFORE_ITERATION hook unchanged) ...

        # Phase 4: LLM call with context overflow recovery
        try:
            response = await llm.chat(list(state.messages), tools=openai_tools)
        except Exception as e:
            if _is_context_overflow(e):
                recovered = await reactive_compact(state, llm, context_limit)
                if recovered and not state.compact_attempted:
                    state = recovered
                    recovery_attempts["context_overflow"] = recovery_attempts.get("context_overflow", 0) + 1
                    continue
                return self._build_result(state, ExitReason.CONTEXT_OVERFLOW,
                                           usage=usage, events=events,
                                           error=str(e), recovery=recovery_attempts)
            return self._build_result(state, ExitReason.LLM_ERROR,
                                       usage=usage, events=events,
                                       error=str(e), recovery=recovery_attempts)

        usage = self._accumulate_usage(usage, response.usage)
        state = state.accumulate_usage(response.usage.prompt_tokens,
                                        response.usage.completion_tokens)

        # Phase 5: no tool calls → recovery chain or finish
        if not response.tool_calls:
            if response.finish_reason == "length" and state.output_recovery_count < 3:
                continuation = {"role": "user", "content": "你的输出被截断了。请从中断处直接继续，不要重复已输出的内容。"}
                new_msgs = list(state.messages) + [
                    {"role": "assistant", "content": response.content or ""},
                    continuation,
                ]
                state = LoopState(
                    messages=tuple(new_msgs),
                    iteration=state.iteration,  # don't increment
                    transition_reason="output_continuation",
                    output_recovery_count=state.output_recovery_count + 1,
                    compact_attempted=state.compact_attempted,
                    total_compact_count=state.total_compact_count,
                    total_prompt_tokens=state.total_prompt_tokens,
                    total_completion_tokens=state.total_completion_tokens,
                )
                recovery_attempts["output_continuation"] = recovery_attempts.get("output_continuation", 0) + 1
                continue

            exit_reason = ExitReason.OUTPUT_TRUNCATED if response.finish_reason == "length" else ExitReason.COMPLETED
            return self._build_result(state, exit_reason, summary=response.content,
                                       usage=usage, events=events, recovery=recovery_attempts)

        # Phase 6: tool execution (sequential, unchanged for now)
        # ... (tool execution code largely unchanged, using list(state.messages)) ...

        # Phase 7: state replacement
        state = state.next_turn(new_messages_list)

    return self._build_result(state, ExitReason.MAX_ITERATIONS,
                               usage=usage, events=events, recovery=recovery_attempts,
                               error=f"Max iterations ({max_iterations}) reached")
```

Add helper:

```python
def _build_result(self, state, exit_reason, *, usage=None, events=None,
                  summary=None, error=None, recovery=None):
    status = TaskStatus.COMPLETED if exit_reason == ExitReason.COMPLETED else (
        TaskStatus.CANCELLED if exit_reason == ExitReason.CANCELLED else TaskStatus.FAILED
    )
    return AgentResult(
        status=status,
        summary=summary,
        result=summary,
        usage=usage or UsageSummary(),
        error=error,
        events=events or [],
        exit_reason=exit_reason,
        iteration_count=state.iteration,
        compact_count=state.total_compact_count,
        recovery_attempts=recovery or {},
    )

def _is_context_overflow(e: Exception) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in ("context_length", "prompt_too_long", "context window"))
```

- [ ] **Step 4: Run the new tests**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_agent.py::TestAgentNewLoop -v`
Expected: 5 tests PASS

- [ ] **Step 5: Run ALL existing agent tests to check backward compat**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_agent.py -v`
Expected: All tests PASS. The old `TestAgent` tests still work because `exit_reason` has a default value and the old assertions (`result.status`, `result.result`, `result.usage`) are preserved.

- [ ] **Step 6: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/executor/agent.py tests/test_agent.py
git commit -m "feat: rewrite Agent.execute with while-loop, exit reasons, compaction, and recovery"
```

---

## Task 9: Integration Smoke Test

**Files:**
- Modify: `tests/test_agent.py` (append)

- [ ] **Step 1: Write an integration-style test exercising the full flow**

Append to `tests/test_agent.py`:

```python
class TestAgentIntegration:
    """Integration tests for the full loop with tools + compaction."""

    @pytest.fixture
    def tools(self):
        registry = ToolRegistry()
        from tianshu.tools.types import ok_result
        from tianshu.tools.registry import ToolDefinition

        async def mock_grep(**kwargs):
            return ok_result("line1: match\nline2: match\n" + "x" * 500)

        registry.register(
            "grep",
            mock_grep,
            ToolDefinition(name="grep", description="Search", parameters={"type": "object", "properties": {"pattern": {"type": "string"}}}),
        )
        return registry

    @pytest.fixture
    def agent(self, config_manager, tools):
        skills = SkillsLoader(builtin_dir="/tmp/empty", char_budget=0)
        return Agent(config_manager=config_manager, tools=tools, skills=skills)

    async def test_multi_turn_with_tool_calls(self, agent):
        edict = Edict(goal="find bugs")
        edict.runtime.max_iterations = 5

        call_count = 0

        def make_response():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return MagicMock(
                    content=f"thinking step {call_count}",
                    tool_calls=[{"id": f"tc{call_count}", "name": "grep", "args": '{"pattern": "bug"}'}],
                    usage=UsageSummary(prompt_tokens=50, completion_tokens=50, total_tokens=100),
                    finish_reason="tool_calls",
                )
            return MagicMock(
                content="Found 2 bugs in main.py",
                tool_calls=None,
                usage=UsageSummary(prompt_tokens=100, completion_tokens=100, total_tokens=200),
                finish_reason="stop",
            )

        with patch("tianshu.executor.agent.LLMClient") as MockLLM:
            mock_llm = AsyncMock()
            mock_llm.chat.side_effect = lambda *a, **kw: make_response()
            MockLLM.return_value = mock_llm
            result = await agent.execute(edict)

        assert result.status == TaskStatus.COMPLETED
        assert result.exit_reason == ExitReason.COMPLETED
        assert result.iteration_count == 2  # 2 tool turns before final
        assert "2 bugs" in result.summary
```

- [ ] **Step 2: Run the integration test**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/test_agent.py::TestAgentIntegration -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add tests/test_agent.py
git commit -m "test: add integration smoke test for agent loop with tools and compaction"
```

---

## Task 10: Update compaction __init__.py re-exports

**Files:**
- Modify: `src/tianshu/executor/compaction/__init__.py`

- [ ] **Step 1: Add re-exports for clean imports**

```python
# src/tianshu/executor/compaction/__init__.py
"""Multi-strategy context compaction for the agent loop."""

from tianshu.executor.compaction.auto import auto_compact, should_auto_compact
from tianshu.executor.compaction.micro import micro_compact
from tianshu.executor.compaction.reactive import reactive_compact
from tianshu.executor.compaction.token_estimator import estimate_tokens

__all__ = [
    "auto_compact",
    "estimate_tokens",
    "micro_compact",
    "reactive_compact",
    "should_auto_compact",
]
```

- [ ] **Step 2: Verify imports work**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -c "from tianshu.executor.compaction import micro_compact, auto_compact, reactive_compact, estimate_tokens; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run full test suite one final time**

Run: `cd /Users/chenjiamin/tiangong/tianshu && python -m pytest tests/ -v --timeout=30`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/chenjiamin/tiangong/tianshu
git add src/tianshu/executor/compaction/__init__.py
git commit -m "chore: add compaction package re-exports"
```

---

## Summary

| Task | Component | New Files | Tests |
|------|-----------|-----------|-------|
| 1 | ExitReason enum | `exit_reason.py` | 3 |
| 2 | LoopState dataclass | `loop_state.py` | 6 |
| 3 | Token estimator | `compaction/token_estimator.py` | 7 |
| 4 | Micro compact | `compaction/micro.py` | 6 |
| 5 | Auto compact | `compaction/auto.py` | 5 |
| 6 | Reactive compact | `compaction/reactive.py` | 3 |
| 7 | LLMResponse.finish_reason | (modify `llm.py`) | 2 |
| 8 | Agent.execute rewrite | (modify `agent.py`) | 5 |
| 9 | Integration test | (modify `test_agent.py`) | 1 |
| 10 | Package re-exports | (modify `__init__.py`) | 0 |
| **Total** | | **7 new files** | **38 tests** |
