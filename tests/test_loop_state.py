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
            state.iteration = 1

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
        assert next_state.compact_attempted is False
        assert next_state.output_recovery_count == 0
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

        assert recovered.iteration == 5
        assert recovered.transition_reason == "micro_compact"
        assert recovered.compact_attempted is True
        assert recovered.output_recovery_count == 2
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
