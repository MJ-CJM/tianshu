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
