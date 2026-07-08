"""PlatformEvalRunner —— 评测集解析、delta 计算、台账落库(fake harness 注入)。"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.evals import PlatformEvalRunner, aggregate_failure_distribution


class _FakeHarness:
    """固定返回的 EvalHarness 替身;记录 evaluate 调用参数。"""

    def __init__(self, score: float = 0.6, sampled: list[str] | None = None):
        self.score = score
        self.sampled = sampled if sampled is not None else ["goal-a", "goal-b"]
        self.evaluate_calls: list[dict] = []

    def select_eval_set(self, size: int) -> list[str]:
        return self.sampled[:size]

    def evaluate(self, worktree, *, eval_set, goal_timeout_s, budget_cny):
        self.evaluate_calls.append(
            {
                "worktree": worktree,
                "eval_set": eval_set,
                "goal_timeout_s": goal_timeout_s,
                "budget_cny": budget_cny,
            }
        )
        return {
            "fitness": {"score": self.score},
            "stats": {"total": len(eval_set)},
            "n": len(eval_set),
            "truncated": False,
            "goal_results": [
                {
                    "instruction": g,
                    "status": "failed" if i % 2 else "completed",
                    "error": "429" if i % 2 else None,
                    "failure_reason": (
                        "agent_error.provider_capacity_or_rate_limit" if i % 2 else None
                    ),
                    "cost": 0.01,
                }
                for i, g in enumerate(eval_set)
            ],
        }


class TestAggregateFailureDistribution:
    def test_counts_failed_only(self):
        rows = [
            {"status": "failed", "failure_reason": "agent_error.unknown"},
            {"status": "failed", "failure_reason": "agent_error.unknown"},
            {"status": "failed", "failure_reason": None},
            {"status": "completed", "failure_reason": None},
        ]
        assert aggregate_failure_distribution(rows) == [
            {"reason": "agent_error.unknown", "count": 2},
            {"reason": "unclassified", "count": 1},
        ]

    def test_empty(self):
        assert aggregate_failure_distribution([]) == []


class TestPlatformEvalRunner:
    @pytest.fixture
    def runner(self, storage, tmp_path):
        harness = _FakeHarness()
        return PlatformEvalRunner(storage, harness, repo_root=tmp_path), harness

    def test_run_adhoc_sampling(self, runner, storage):
        r, harness = runner
        run = r.run(size=2)
        assert run["eval_set_name"] is None
        assert run["n"] == 2
        assert run["delta_vs_prev"] is None  # 首跑无前次
        assert run["failure_distribution"] == [
            {"reason": "agent_error.provider_capacity_or_rate_limit", "count": 1}
        ]
        # 已落台账
        assert storage.get_platform_eval_run(run["id"])["fitness"]["score"] == 0.6

    def test_run_with_saved_set_and_delta(self, runner, storage):
        r, harness = runner
        storage.save_eval_set("reg", ["goal-a", "goal-b"])
        first = r.run(set_name="reg")
        harness.score = 0.75
        second = r.run(set_name="reg")
        assert second["eval_set_fingerprint"] == first["eval_set_fingerprint"]
        assert second["delta_vs_prev"] == pytest.approx(0.15)

    def test_unknown_set_raises(self, runner):
        r, _ = runner
        with pytest.raises(ValueError, match="not found"):
            r.run(set_name="ghost")

    def test_empty_sample_raises(self, storage, tmp_path):
        r = PlatformEvalRunner(storage, _FakeHarness(sampled=[]), repo_root=tmp_path)
        with pytest.raises(ValueError, match="empty"):
            r.run(size=4)

    def test_sample_and_save(self, runner, storage):
        r, _ = runner
        goals = r.sample_and_save("baseline", size=2)
        assert goals == ["goal-a", "goal-b"]
        assert storage.get_eval_set("baseline")["goals"] == goals

    def test_evaluate_receives_repo_root_and_budget(self, runner, tmp_path):
        r, harness = runner
        r.run(size=1, goal_timeout_s=120, budget_cny=5.0)
        call = harness.evaluate_calls[0]
        assert call["worktree"] == Path(tmp_path)
        assert call["goal_timeout_s"] == 120
        assert call["budget_cny"] == 5.0
