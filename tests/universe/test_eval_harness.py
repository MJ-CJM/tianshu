"""EvalHarness 单测。

纯逻辑部分（score / select_eval_set / aggregate_db_stats）用真实 Storage；
evaluate() orchestration 用 fake sandbox + monkeypatch。
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from tianshu.models import Edict, EdictStatus, Memorial, TaskStatus
from tianshu.storage import Storage
from tianshu.universe.eval_harness import EvalHarness
from tianshu.universe.fitness import compute_fitness

# ---------------------------------------------------------------------------
# 辅助：临时 Storage
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_storage(tmp_path):
    db = tmp_path / "test.db"
    s = Storage(str(db))
    s.init_db()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# test_score_uses_compute_fitness
# ---------------------------------------------------------------------------


def test_score_uses_compute_fitness():
    """EvalHarness.score() 应与直接调用 compute_fitness 结果一致。"""
    default_weights = (0.4, 0.15, 0.2, 0.1, 0.15)
    harness = EvalHarness(None, None, fitness_weights=default_weights)
    stats = {
        "total": 10,
        "success": 8,
        "retries": 2,
        "audited": 5,
        "audit_pass": 4,
        "cost": 0.5,
        "feedback": 2,
    }
    assert harness.score(stats) == compute_fitness(stats, weights=default_weights)


# ---------------------------------------------------------------------------
# test_select_eval_set_from_storage
# ---------------------------------------------------------------------------


def test_select_eval_set_from_storage(tmp_storage):
    """从 completed edict 中选出 goal 列表，不超过 size，去重。"""
    goals = ["goal A", "goal B", "goal C", "goal A"]  # "goal A" 重复
    for g in goals:
        e = Edict(goal=g, status=EdictStatus.COMPLETED)
        tmp_storage.save_edict(e)
        tmp_storage.update_edict_status(e.id, "completed")

    # 追加一个 open 状态的 edict，不应被选中
    open_e = Edict(goal="open goal")
    tmp_storage.save_edict(open_e)

    harness = EvalHarness(tmp_storage, None)
    result = harness.select_eval_set(5)

    assert len(result) <= 5
    # "goal A" 去重后只出现一次
    assert result.count("goal A") == 1
    # open edict 的 goal 不应出现
    assert "open goal" not in result
    # 三个唯一 completed goals 都应在结果中
    assert set(result) == {"goal A", "goal B", "goal C"}


def test_select_eval_set_respects_size(tmp_storage):
    """size 参数截断结果。"""
    for i in range(10):
        e = Edict(goal=f"goal {i}", status=EdictStatus.COMPLETED)
        tmp_storage.save_edict(e)
        tmp_storage.update_edict_status(e.id, "completed")

    harness = EvalHarness(tmp_storage, None)
    result = harness.select_eval_set(3)
    assert len(result) <= 3


# ---------------------------------------------------------------------------
# test_aggregate_db_stats
# ---------------------------------------------------------------------------


def test_aggregate_db_stats(tmp_storage, tmp_path):
    """aggregate_db_stats 聚合沙箱 DB 中所有 memorial，不按 universe_id 过滤。"""

    # 插入两条 memorial：一条成功，一条失败
    e = Edict(goal="test")
    tmp_storage.save_edict(e)

    m1 = Memorial(
        edict_id=e.id,
        status=TaskStatus.COMPLETED,
        attempt=1,
    )
    m1.usage.cost_cny = 0.01
    tmp_storage.save_memorial(m1)

    m2 = Memorial(
        edict_id=e.id,
        status=TaskStatus.FAILED,
        attempt=2,  # 1 retry
    )
    tmp_storage.save_memorial(m2)

    db_path = Path(tmp_storage._db_path)
    harness = EvalHarness(None, None)
    stats = harness.aggregate_db_stats(db_path)

    assert stats["total"] == 2
    assert stats["success"] == 1
    assert stats["retries"] == 1  # attempt=2 → 1 retry
    assert stats["audited"] == 0
    assert stats["audit_pass"] == 0
    assert stats["feedback"] == 0
    assert stats["cost"] >= 0.0


def test_aggregate_db_stats_empty(tmp_storage):
    """空库返回全零 stats。"""
    db_path = Path(tmp_storage._db_path)
    harness = EvalHarness(None, None)
    stats = harness.aggregate_db_stats(db_path)
    assert stats == {
        "total": 0,
        "success": 0,
        "retries": 0,
        "audited": 0,
        "audit_pass": 0,
        "cost": 0.0,
        "feedback": 0,
    }


# ---------------------------------------------------------------------------
# test_evaluate_orchestration_with_fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeHandle:
    base_url: str
    db_path: Path


class _FakeSandboxRunner:
    """fake sandbox runner：session() 直接 yield 一个带已知 db_path 的 handle。"""

    def __init__(self, db_path: Path):
        self._db_path = db_path

    @contextlib.contextmanager
    def session(self, worktree, *, db_path, extra_env=None):
        yield _FakeHandle(base_url="http://fake:9999", db_path=self._db_path)


def test_evaluate_orchestration_with_fakes(tmp_path, tmp_storage, monkeypatch):
    """evaluate() 应：调用 _run_goal、调 aggregate_db_stats、返回 {fitness, stats, n}。

    用 fake sandbox + monkeypatch _run_goal 为 no-op；
    pre-seed iso_db 让 aggregate_db_stats 返回已知 stats。
    """
    # 准备一个有数据的 db，用作 "iso_db"（pre-seeded）
    iso_db = tmp_path / "_eval.db"
    # 把 tmp_storage 的 db 拷过去，再插 memorial
    import shutil

    shutil.copy(tmp_storage._db_path, iso_db)
    seeded = Storage(str(iso_db))
    seeded.init_db()
    e = Edict(goal="test goal")
    seeded.save_edict(e)
    m = Memorial(edict_id=e.id, status=TaskStatus.COMPLETED, attempt=1)
    seeded.save_memorial(m)
    seeded.close()

    fake_sandbox = _FakeSandboxRunner(iso_db)
    harness = EvalHarness(tmp_storage, fake_sandbox)

    # monkeypatch _run_goal 为 no-op（不做真实 HTTP）
    run_goal_calls: list[str] = []

    def fake_run_goal(base_url, goal, timeout_s):
        run_goal_calls.append(goal)

    monkeypatch.setattr(harness, "_run_goal", fake_run_goal)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    eval_set = ["goal 1", "goal 2"]

    result = harness.evaluate(worktree, eval_set=eval_set)

    # 返回结构正确
    assert set(result.keys()) == {"fitness", "stats", "n", "truncated"}
    assert result["n"] == len(eval_set)
    assert isinstance(result["fitness"], dict)
    assert "score" in result["fitness"]
    assert isinstance(result["stats"], dict)
    assert "total" in result["stats"]
    assert result["truncated"] is False

    # _run_goal 被调用了 len(eval_set) 次
    assert run_goal_calls == eval_set


def test_evaluate_truncates_on_budget(tmp_path, monkeypatch):
    """预算触顶后停止回放剩余 goal,结果标记 truncated。"""
    from tianshu.universe.eval_harness import EvalHarness

    ran: list[str] = []

    class _H:
        base_url = "http://x"
        db_path = tmp_path / "_eval.db"

    class _FakeSandbox:
        import contextlib

        @contextlib.contextmanager
        def session(self, worktree, *, db_path, extra_env=None):
            yield _H()

    harness = EvalHarness(storage=None, sandbox_runner=_FakeSandbox())
    # 每回放一条 goal,沙箱 DB 里累积 1.0 元成本
    monkeypatch.setattr(harness, "_run_goal", lambda base, goal, t: ran.append(goal))
    costs = iter([1.0, 2.0, 3.0, 3.0])  # 第 2 条后 cost=2.0 ≥ budget → 截断
    monkeypatch.setattr(
        harness,
        "aggregate_db_stats",
        lambda db: {
            "total": len(ran),
            "success": len(ran),
            "retries": 0,
            "audited": 0,
            "audit_pass": 0,
            "cost": next(costs),
            "feedback": 0,
        },
    )

    result = harness.evaluate(tmp_path, eval_set=["g1", "g2", "g3"], budget_cny=2.0)
    assert result["truncated"] is True
    assert ran == ["g1", "g2"]
    assert result["n"] == 2


def test_evaluate_no_budget_runs_all(tmp_path, monkeypatch):
    """不传预算时全量回放,truncated 恒 False。"""
    from tianshu.universe.eval_harness import EvalHarness

    ran: list[str] = []

    class _H:
        base_url = "http://x"
        db_path = tmp_path / "_eval.db"

    class _FakeSandbox:
        import contextlib

        @contextlib.contextmanager
        def session(self, worktree, *, db_path, extra_env=None):
            yield _H()

    harness = EvalHarness(storage=None, sandbox_runner=_FakeSandbox())
    monkeypatch.setattr(harness, "_run_goal", lambda base, goal, t: ran.append(goal))
    costs = iter([1.0, 2.0, 3.0, 3.0])
    monkeypatch.setattr(
        harness,
        "aggregate_db_stats",
        lambda db: {
            "total": len(ran),
            "success": len(ran),
            "retries": 0,
            "audited": 0,
            "audit_pass": 0,
            "cost": next(costs),
            "feedback": 0,
        },
    )

    result = harness.evaluate(tmp_path, eval_set=["g1", "g2", "g3"])
    assert result["truncated"] is False
    assert ran == ["g1", "g2", "g3"]
    assert result["n"] == 3


# ---------------------------------------------------------------------------
# test_eval_set_fingerprint / test_evaluate_paired
# ---------------------------------------------------------------------------


def test_eval_set_fingerprint_stable_and_sensitive():
    from tianshu.universe.eval_harness import EvalHarness

    fp1 = EvalHarness.eval_set_fingerprint(["a", "b"], "champ-1")
    assert fp1 == EvalHarness.eval_set_fingerprint(["a", "b"], "champ-1")
    assert len(fp1) == 12
    assert fp1 != EvalHarness.eval_set_fingerprint(["a", "c"], "champ-1")  # 集合变
    assert fp1 != EvalHarness.eval_set_fingerprint(["a", "b"], "champ-2")  # 冠军变


def test_evaluate_paired_delta_and_cache(tmp_path, monkeypatch):
    from tianshu.universe.eval_harness import EvalHarness

    harness = EvalHarness(storage=None, sandbox_runner=None)
    calls: list[str] = []

    def _fake_evaluate(worktree, *, eval_set, extra_env=None, **kw):
        calls.append(str(worktree))
        score = 0.8 if "variant" in str(worktree) else 0.7
        return {
            "fitness": {"score": score, "samples": len(eval_set)},
            "stats": {"cost": 1.0},
            "n": len(eval_set),
            "truncated": False,
        }

    monkeypatch.setattr(harness, "evaluate", _fake_evaluate)

    r = harness.evaluate_paired(
        tmp_path / "variant", eval_set=["g1"], baseline_worktree=tmp_path / "main"
    )
    assert r["delta"] == 0.1
    assert r["baseline_cached"] is False
    assert len(calls) == 2

    calls.clear()
    r2 = harness.evaluate_paired(
        tmp_path / "variant",
        eval_set=["g1"],
        baseline_worktree=tmp_path / "main",
        cached_baseline={"fitness": {"score": 0.75, "samples": 1}, "stats": {}, "n": 1},
    )
    assert r2["baseline_cached"] is True
    assert round(r2["delta"], 4) == 0.05
    assert calls == [str(tmp_path / "variant")]  # 命中缓存只评 variant
