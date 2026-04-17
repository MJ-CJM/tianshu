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
