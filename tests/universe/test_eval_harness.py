"""EvalHarness 单测。

纯逻辑部分（score / select_eval_set / aggregate_db_stats）用真实 Storage；
evaluate() orchestration 用 fake sandbox + monkeypatch。
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from dataclasses import dataclass
from pathlib import Path

import pytest

from tianshu.models import Edict, EdictStatus, Memorial, TaskStatus
from tianshu.storage import Storage
from tianshu.universe.eval_harness import EvalHarness
from tianshu.universe.fitness import compute_fitness


async def _no_op_run_goal(*_args) -> None:
    return None


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
    """从 completed edict 和 failed memorial 混采，约 60% 成功 + 40% 失败，去重。

    edict 没有 failed 生命周期：失败信号挂在 memorial 层，edict 本身保持
    默认(open)状态，select_eval_set 需经 memorial 反查 edict.goal 采到失败样本。
    """
    # 插入 3 个 completed goals
    for _i, g in enumerate(["goal A", "goal B", "goal C"]):
        e = Edict(goal=g, status=EdictStatus.COMPLETED)
        tmp_storage.save_edict(e)
        tmp_storage.update_edict_status(e.id, "completed")

    # 插入 2 个 failed goals：edict 保持 open，失败状态挂在 memorial 上
    for _i, g in enumerate(["failed goal 1", "failed goal 2"]):
        e = Edict(goal=g)
        tmp_storage.save_edict(e)
        tmp_storage.save_memorial(Memorial(edict_id=e.id, status=TaskStatus.FAILED))

    # 追加一个 open 状态的 edict，不应被选中
    open_e = Edict(goal="open goal")
    tmp_storage.save_edict(open_e)

    harness = EvalHarness(tmp_storage, None)
    result = harness.select_eval_set(5)

    assert len(result) <= 5
    # open edict 的 goal 不应出现
    assert "open goal" not in result
    # 应包含目标样本：约 60% 成功（3 条）+ 40% 失败（2 条）
    failed_count = sum(1 for g in result if g.startswith("failed"))
    completed_count = sum(1 for g in result if g.startswith("goal"))
    assert failed_count == 2
    assert completed_count == 3


def test_select_eval_set_respects_size(tmp_storage):
    """size 参数截断结果，混采模式下也应遵守大小限制。"""
    # 插入 10 个 completed goals
    for i in range(10):
        e = Edict(goal=f"goal {i}", status=EdictStatus.COMPLETED)
        tmp_storage.save_edict(e)
        tmp_storage.update_edict_status(e.id, "completed")

    # 插入 10 个 failed goals：edict 保持 open，失败状态挂在 memorial 上
    for i in range(10):
        e = Edict(goal=f"failed goal {i}")
        tmp_storage.save_edict(e)
        tmp_storage.save_memorial(Memorial(edict_id=e.id, status=TaskStatus.FAILED))

    harness = EvalHarness(tmp_storage, None)
    result = harness.select_eval_set(10)
    assert len(result) <= 10
    # 应约 60% 成功（6 条）+ 40% 失败（4 条）
    failed_count = sum(1 for g in result if g.startswith("failed"))
    completed_count = sum(1 for g in result if g.startswith("goal"))
    assert failed_count == 4
    assert completed_count == 6


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


@pytest.mark.asyncio
async def test_goal_polling_is_async_and_observes_cancellation_promptly(
    tmp_path,
    monkeypatch,
):
    assert inspect.iscoroutinefunction(EvalHarness._run_goal)
    harness = EvalHarness(storage=None, sandbox_runner=None)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocking_goal(_base_url, _goal, _timeout_s):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(harness, "_run_goal", blocking_goal)
    task = asyncio.create_task(
        harness._evaluate_session(
            _FakeHandle(base_url="http://fake:9999", db_path=tmp_path / "eval.db"),
            eval_set=["goal"],
            goal_timeout_s=300,
            budget_cny=None,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=0.5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(cancelled.wait(), timeout=0.5)


@pytest.mark.asyncio
async def test_goal_polling_ignores_host_proxy_environment_and_redirects(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _Response()

    def client_factory(**kwargs):
        captured.update(kwargs)
        return _Client()

    monkeypatch.setattr("tianshu.universe.eval_harness.httpx.AsyncClient", client_factory)

    await EvalHarness(None, None)._run_goal("http://127.0.0.1:1", "goal", 1)

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False


@pytest.mark.asyncio
async def test_goal_polling_does_not_issue_get_after_deadline_expires_during_sleep(
    monkeypatch,
) -> None:
    clock = 0.0
    get_calls = 0

    class _Loop:
        def time(self) -> float:
            return clock

    async def advance_clock(delay: float) -> None:
        nonlocal clock
        clock += delay

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _Response({"data": {"id": "edict-id"}})

        async def get(self, *_args, **_kwargs):
            nonlocal get_calls
            get_calls += 1
            return _Response({"data": {"status": "completed"}})

    monkeypatch.setattr("tianshu.universe.eval_harness.asyncio.get_running_loop", _Loop)
    monkeypatch.setattr("tianshu.universe.eval_harness.asyncio.sleep", advance_clock)
    monkeypatch.setattr(
        "tianshu.universe.eval_harness.httpx.AsyncClient",
        lambda **_kwargs: _Client(),
    )

    await EvalHarness(None, None)._run_goal("http://127.0.0.1:1", "goal", 1)

    assert get_calls == 0


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

    async def fake_run_goal(base_url, goal, timeout_s):
        run_goal_calls.append(goal)

    monkeypatch.setattr(harness, "_run_goal", fake_run_goal)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    eval_set = ["goal 1", "goal 2"]

    result = harness.evaluate(worktree, eval_set=eval_set)

    # 返回结构正确(迭代 2 起含 goal_results per-goal 明细)
    assert set(result.keys()) == {"fitness", "stats", "n", "truncated", "goal_results"}
    assert result["n"] == len(eval_set)
    assert isinstance(result["fitness"], dict)
    assert "score" in result["fitness"]
    assert isinstance(result["stats"], dict)
    assert "total" in result["stats"]
    assert result["truncated"] is False
    assert isinstance(result["goal_results"], list)

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
    async def record_goal(_base, goal, _timeout):
        ran.append(goal)

    monkeypatch.setattr(harness, "_run_goal", record_goal)
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

    async def record_goal(_base, goal, _timeout):
        ran.append(goal)

    monkeypatch.setattr(harness, "_run_goal", record_goal)
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

    async def _fake_evaluate(worktree, *, eval_set, extra_env=None, **kw):
        calls.append(str(worktree))
        score = 0.8 if "variant" in str(worktree) else 0.7
        return {
            "fitness": {"score": score, "samples": len(eval_set)},
            "stats": {"cost": 1.0},
            "n": len(eval_set),
            "truncated": False,
        }

    monkeypatch.setattr(harness, "evaluate_async", _fake_evaluate)

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


def test_evaluate_paired_bare_fitness_dict_cache(tmp_path, monkeypatch):
    """cached_baseline 为裸 fitness dict(无 "fitness" 键)时应被自动包裹。

    这是生产真实路径:evolver 把 storage.latest_baseline_fitness() 的裸 dict
    原样传入 cached_baseline,而非 evaluate_paired 完整返回形态。
    """
    from tianshu.universe.eval_harness import EvalHarness

    harness = EvalHarness(storage=None, sandbox_runner=None)
    calls: list[str] = []

    async def _fake_evaluate(worktree, *, eval_set, extra_env=None, **kw):
        calls.append(str(worktree))
        return {
            "fitness": {"score": 0.8, "samples": len(eval_set)},
            "stats": {"cost": 1.0},
            "n": len(eval_set),
            "truncated": False,
        }

    monkeypatch.setattr(harness, "evaluate_async", _fake_evaluate)

    r = harness.evaluate_paired(
        tmp_path / "variant",
        eval_set=["g1"],
        baseline_worktree=tmp_path / "main",
        cached_baseline={"score": 0.75, "samples": 20},  # 裸 dict,无 "fitness" 键
    )
    assert r["baseline_cached"] is True
    assert round(r["delta"], 4) == 0.05  # 0.8 - 0.75
    assert calls == [str(tmp_path / "variant")]  # 命中缓存,baseline_worktree 未被评估


# ---------------------------------------------------------------------------
# test_select_eval_set_mixes_failed_goals (新测试)
# ---------------------------------------------------------------------------


def test_select_eval_set_mixes_failed_goals():
    """edict 层无 failed 状态：list_edicts("failed") 故意留空，

    失败样本经 list_memorials + get_edict 反查 goal 采集。
    """
    from tianshu.universe.eval_harness import EvalHarness

    class _E:
        def __init__(self, goal):
            self.goal = goal

    class _M:
        def __init__(self, edict_id):
            self.edict_id = edict_id

    class _FakeStorage:
        def __init__(self):
            self._failed_edicts = {f"f{i}": _E(f"失败目标{i}") for i in range(20)}

        def list_edicts(self, status=None, limit=50, **kw):
            if status == "completed":
                return [_E(f"成功目标{i}") for i in range(20)], 20
            return [], 0  # edict 层无 failed 状态

        def list_memorials(self, status=None, limit=50, offset=0):
            if status == "failed":
                ids = list(self._failed_edicts)
                return [_M(eid) for eid in ids], len(ids)
            return [], 0

        def get_edict(self, edict_id):
            return self._failed_edicts.get(edict_id)

    harness = EvalHarness(storage=_FakeStorage(), sandbox_runner=None)
    goals = harness.select_eval_set(10)
    assert len(goals) == 10
    assert sum(1 for g in goals if g.startswith("失败")) == 4  # 40%
    assert sum(1 for g in goals if g.startswith("成功")) == 6


def test_select_eval_set_backfills_when_failed_scarce():
    """失败样本不足时用成功样本回填到满额。"""
    from tianshu.universe.eval_harness import EvalHarness

    class _E:
        def __init__(self, goal):
            self.goal = goal

    class _M:
        def __init__(self, edict_id):
            self.edict_id = edict_id

    class _FakeStorage:
        def __init__(self):
            self._failed_edicts = {"f0": _E("失败目标0")}

        def list_edicts(self, status=None, limit=50, **kw):
            if status == "completed":
                return [_E(f"成功目标{i}") for i in range(20)], 20
            return [], 0  # edict 层无 failed 状态

        def list_memorials(self, status=None, limit=50, offset=0):
            if status == "failed":
                return [_M("f0")], 1
            return [], 0

        def get_edict(self, edict_id):
            return self._failed_edicts.get(edict_id)

    goals = EvalHarness(storage=_FakeStorage(), sandbox_runner=None).select_eval_set(10)
    assert len(goals) == 10
    assert sum(1 for g in goals if g.startswith("失败")) == 1
    assert sum(1 for g in goals if g.startswith("成功")) == 9


def test_select_eval_set_dedupes_across_strata():
    """同一 goal 同时出现在成功与失败层时不重复入集。"""
    from tianshu.universe.eval_harness import EvalHarness

    class _E:
        def __init__(self, goal):
            self.goal = goal

    class _M:
        def __init__(self, edict_id):
            self.edict_id = edict_id

    class _FakeStorage:
        def __init__(self):
            self._failed_edicts = {"f0": _E("重叠目标"), "f1": _E("失败目标1")}

        def list_edicts(self, status=None, limit=50, **kw):
            if status == "completed":
                return [_E("重叠目标"), _E("成功目标1"), _E("成功目标2")], 3
            return [], 0  # edict 层无 failed 状态

        def list_memorials(self, status=None, limit=50, offset=0):
            if status == "failed":
                return [_M("f0"), _M("f1")], 2
            return [], 0

        def get_edict(self, edict_id):
            return self._failed_edicts.get(edict_id)

    goals = EvalHarness(storage=_FakeStorage(), sandbox_runner=None).select_eval_set(5)
    assert len(goals) == len(set(goals))  # 无重复
    assert "重叠目标" in goals


def test_select_eval_set_failed_layer_survives_empty_list_edicts():
    """守门测试：list_edicts(status="failed") 返回空模拟真实系统

    (edict 没有 failed 生命周期)。失败层必须仍能从 memorial 路径采到样本，
    防止实现回归为直接查 list_edicts("failed") 的伪实现。
    """
    from tianshu.universe.eval_harness import EvalHarness

    class _E:
        def __init__(self, goal):
            self.goal = goal

    class _M:
        def __init__(self, edict_id):
            self.edict_id = edict_id

    class _FakeStorage:
        def __init__(self):
            self._failed_edicts = {
                "f0": _E("失败目标0"),
                "f1": _E("失败目标1"),
                "f2": _E("失败目标2"),
            }

        def list_edicts(self, status=None, limit=50, **kw):
            if status == "completed":
                return [_E(f"成功目标{i}") for i in range(10)], 10
            if status == "failed":
                return [], 0  # 模拟真实系统：edict 从不进入 failed 状态
            return [], 0

        def list_memorials(self, status=None, limit=50, offset=0):
            if status == "failed":
                ids = list(self._failed_edicts)
                return [_M(eid) for eid in ids], len(ids)
            return [], 0

        def get_edict(self, edict_id):
            return self._failed_edicts.get(edict_id)

    goals = EvalHarness(storage=_FakeStorage(), sandbox_runner=None).select_eval_set(5)
    failed = [g for g in goals if g.startswith("失败")]
    assert len(failed) == 2  # size=5 → n_fail=int(5*0.4)=2，全部经 memorial 路径采到
    assert set(failed) <= {"失败目标0", "失败目标1", "失败目标2"}


# ---------------------------------------------------------------------------
# test_evaluate_merges_base_env_into_sandbox (沙箱凭证隔离测试)
# ---------------------------------------------------------------------------


def test_evaluate_merges_base_env_into_sandbox(tmp_path, monkeypatch):
    """evaluate() 应将 base_env 与 extra_env 合并后传给 sandbox.session()。

    base_env 作为默认值，extra_env 优先级更高。验证沙箱进程收到合并后的 env dict。
    """
    from tianshu.universe.eval_harness import EvalHarness

    captured = {}

    class _H:
        base_url = "http://x"
        db_path = tmp_path / "_eval.db"

    class _FakeSandbox:
        import contextlib

        @contextlib.contextmanager
        def session(self, worktree, *, db_path, extra_env=None):
            captured["extra_env"] = extra_env
            yield _H()

    harness = EvalHarness(
        storage=None,
        sandbox_runner=_FakeSandbox(),
        base_env={"TIANSHU_LLM_API_KEY": "${settings:eval_llm_api_key}"},
    )
    monkeypatch.setattr(harness, "_run_goal", _no_op_run_goal)
    monkeypatch.setattr(
        harness,
        "aggregate_db_stats",
        lambda db: {
            "total": 0,
            "success": 0,
            "retries": 0,
            "audited": 0,
            "audit_pass": 0,
            "cost": 0.0,
            "feedback": 0,
        },
    )

    harness.evaluate(tmp_path, eval_set=["g"], extra_env={"TIANSHU_RUNTIME_PERSONAS_DIR": "/tmp/p"})
    assert captured["extra_env"]["TIANSHU_LLM_API_KEY"] == "${settings:eval_llm_api_key}"
    assert captured["extra_env"]["TIANSHU_RUNTIME_PERSONAS_DIR"] == "/tmp/p"


# ---------------------------------------------------------------------------
# test_evaluate_generates_unique_iso_db_per_call (发现 1 回归测试)
# ---------------------------------------------------------------------------


def test_evaluate_generates_unique_iso_db_per_call(tmp_path, monkeypatch):
    """两次 evaluate() 调用应各自生成唯一的 iso_db 文件名。

    修复前 iso_db 固定为 "_eval.db"：并发评估（行为层/代码层各一条 cron，经
    异步评估并发）会共享同一文件，先结束者 stop() 的 unlink 会
    删掉对方仍在使用的库。文件名唯一化后每次 evaluate() 各用各的库。
    """
    from tianshu.universe.eval_harness import EvalHarness

    captured_db_paths: list[Path] = []

    class _H:
        base_url = "http://x"
        db_path = tmp_path / "_eval.db"

    class _FakeSandbox:
        import contextlib

        @contextlib.contextmanager
        def session(self, worktree, *, db_path, extra_env=None):
            captured_db_paths.append(Path(db_path))
            yield _H()

    harness = EvalHarness(storage=None, sandbox_runner=_FakeSandbox())
    monkeypatch.setattr(harness, "_run_goal", _no_op_run_goal)
    monkeypatch.setattr(
        harness,
        "aggregate_db_stats",
        lambda db: {
            "total": 0,
            "success": 0,
            "retries": 0,
            "audited": 0,
            "audit_pass": 0,
            "cost": 0.0,
            "feedback": 0,
        },
    )

    harness.evaluate(tmp_path, eval_set=["g1"])
    harness.evaluate(tmp_path, eval_set=["g1"])

    assert len(captured_db_paths) == 2
    assert captured_db_paths[0] != captured_db_paths[1]
    for p in captured_db_paths:
        assert p.name.startswith("_eval-")
        assert p.name.endswith(".db")
