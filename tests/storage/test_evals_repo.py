"""EvalsMixin 与 memorial 失败归因存储(迭代 2「证明」)。"""

from __future__ import annotations

from tianshu.models import Memorial, TaskStatus
from tianshu.models.edict import Edict


def _run(
    run_id: str, fp: str = "fp-1", score: float = 0.5, created_at: str = "2026-07-08T00:00:00+00:00"
) -> dict:
    return {
        "id": run_id,
        "eval_set_name": "regression",
        "eval_set_fingerprint": fp,
        "target": "/repo@abc123",
        "fitness": {"score": score},
        "stats": {"total": 2},
        "goal_results": [
            {
                "instruction": "g1",
                "status": "completed",
                "error": None,
                "failure_reason": None,
                "cost": 0.01,
            },
            {
                "instruction": "g2",
                "status": "failed",
                "error": "429",
                "failure_reason": "agent_error.provider_capacity_or_rate_limit",
                "cost": 0.0,
            },
        ],
        "n": 2,
        "truncated": False,
        "delta_vs_prev": None,
        "created_at": created_at,
    }


class TestEvalSets:
    def test_save_get_list_roundtrip(self, storage):
        storage.save_eval_set("regression", ["g1", "g2"], source="sampled")
        got = storage.get_eval_set("regression")
        assert got["goals"] == ["g1", "g2"]
        assert got["source"] == "sampled"
        assert [s["name"] for s in storage.list_eval_sets()] == ["regression"]

    def test_missing_returns_none(self, storage):
        assert storage.get_eval_set("ghost") is None


class TestEvalRuns:
    def test_save_get_roundtrip_full_fields(self, storage):
        storage.save_platform_eval_run(_run("r1"))
        got = storage.get_platform_eval_run("r1")
        assert got["fitness"]["score"] == 0.5
        assert got["goal_results"][1]["failure_reason"] == (
            "agent_error.provider_capacity_or_rate_limit"
        )
        assert got["truncated"] is False

    def test_list_is_brief(self, storage):
        storage.save_platform_eval_run(_run("r1"))
        brief = storage.list_platform_eval_runs()[0]
        assert "goal_results" not in brief
        assert "stats" not in brief
        assert brief["fitness"]["score"] == 0.5

    def test_latest_by_fingerprint(self, storage):
        storage.save_platform_eval_run(
            _run("r1", fp="fp-A", created_at="2026-07-01T00:00:00+00:00")
        )
        storage.save_platform_eval_run(
            _run("r2", fp="fp-A", created_at="2026-07-02T00:00:00+00:00")
        )
        storage.save_platform_eval_run(
            _run("r3", fp="fp-B", created_at="2026-07-03T00:00:00+00:00")
        )
        assert storage.latest_platform_eval_run("fp-A")["id"] == "r2"
        assert storage.latest_platform_eval_run("fp-none") is None


class TestFailureReasonWritePath:
    """写路径自动归因:save/update 落库即细化(multica in-flight classifier 同构)。"""

    def _failed_memorial(self, storage, error: str) -> Memorial:
        e = Edict(goal="test goal")
        storage.save_edict(e)
        m = Memorial(edict_id=e.id, status=TaskStatus.FAILED, error=error)
        storage.save_memorial(m)
        return m

    def test_save_auto_classifies(self, storage):
        m = self._failed_memorial(storage, "Execution timed out after 300s")
        assert storage.get_memorial(m.id).failure_reason == "agent_error.agent_timeout"

    def test_update_reclassifies_on_new_error(self, storage):
        m = self._failed_memorial(storage, "Execution timed out after 300s")
        got = storage.get_memorial(m.id)
        got.error = "RateLimitError 429"
        got.failure_reason = None
        storage.update_memorial(got)
        assert storage.get_memorial(m.id).failure_reason == (
            "agent_error.provider_capacity_or_rate_limit"
        )

    def test_explicit_reason_preserved(self, storage):
        e = Edict(goal="test goal")
        storage.save_edict(e)
        m = Memorial(
            edict_id=e.id,
            status=TaskStatus.FAILED,
            error="whatever",
            failure_reason="custom.reason",
        )
        storage.save_memorial(m)
        assert storage.get_memorial(m.id).failure_reason == "custom.reason"

    def test_non_failed_has_none(self, storage):
        e = Edict(goal="test goal")
        storage.save_edict(e)
        m = Memorial(edict_id=e.id, status=TaskStatus.COMPLETED)
        storage.save_memorial(m)
        assert storage.get_memorial(m.id).failure_reason is None

    def test_distribution_and_backfill(self, storage):
        self._failed_memorial(storage, "401 unauthorized")
        self._failed_memorial(storage, "invalid api key 401")
        self._failed_memorial(storage, "connection refused")
        dist = storage.failure_reason_distribution()
        assert dist[0]["reason"] == "agent_error.provider_auth_or_access"
        assert dist[0]["count"] == 2

        # 抹掉归因模拟历史库 → 回填补齐 → 幂等 → 全量重分类
        storage._conn.execute("UPDATE memorials SET failure_reason = NULL")
        storage._conn.commit()
        assert storage.backfill_failure_reasons() == 3
        assert storage.backfill_failure_reasons() == 0
        assert storage.backfill_failure_reasons(reclassify=True) == 3
