import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.universe.evolver import UniverseEvolver


def _cfg(**over):
    d = dict(
        parallel_universe_enabled=True,
        universe_evolver_idle_hours=0,
        universe_min_samples=10,
        universe_promote_margin=0.05,
        universe_auto_promote=False,
    )
    d.update(over)
    return type("C", (), d)()


def _evolver(cfg, mgr, storage, llm=None):
    cm = MagicMock()
    cm.agent_config = cfg
    return UniverseEvolver(llm or AsyncMock(), mgr, storage, cm)


def test_disabled_skips():
    cm_cfg = type("C", (), {"parallel_universe_enabled": False})()
    ev = _evolver(cm_cfg, MagicMock(), MagicMock())
    assert asyncio.run(ev.run()).skipped == "disabled"


def test_lock_held_skips():
    st = MagicMock()
    st.try_acquire_synthesis_lock.return_value = False
    st.last_activity_at.return_value = None
    ev = _evolver(_cfg(), MagicMock(), st)
    assert asyncio.run(ev.run()).skipped == "lock_held"


def test_no_champion_releases_lock():
    st = MagicMock()
    st.try_acquire_synthesis_lock.return_value = True
    st.last_activity_at.return_value = None
    mgr = MagicMock()
    mgr.champion.return_value = None
    ev = _evolver(_cfg(), mgr, st)
    assert asyncio.run(ev.run()).skipped == "no_champion"
    assert st.release_synthesis_lock.called


def test_mutation_branches_challenger():
    st = MagicMock()
    st.try_acquire_synthesis_lock.return_value = True
    st.last_activity_at.return_value = None
    st.universe_memorial_stats.return_value = {"total": 0, "success": 0}
    champ = {"id": "champ", "fitness": {"score": 0.5}}
    mgr = MagicMock()
    mgr.champion.return_value = champ
    mgr.list.return_value = [dict(champ, status="champion")]
    mgr.branch.return_value = {"id": "newch"}
    llm = AsyncMock()
    llm.chat.return_value = type(
        "R", (), {"content": '{"target": "policy", "reason": "r", "name": "n"}'}
    )()
    ev = _evolver(_cfg(), mgr, st, llm)
    r = asyncio.run(ev.run())
    assert r.created_challenger == "newch"
    assert mgr.branch.called


def test_manual_bypasses_idle():
    # idle gate would block (idle_hours high, last activity recent) but manual bypasses it
    st = MagicMock()
    st.try_acquire_synthesis_lock.return_value = True
    from datetime import UTC, datetime

    st.last_activity_at.return_value = datetime.now(UTC).isoformat()
    champ = {"id": "champ", "fitness": {"score": 0.5}}
    mgr = MagicMock()
    mgr.champion.return_value = champ
    mgr.list.return_value = [dict(champ, status="champion")]
    llm = AsyncMock()
    llm.chat.return_value = type("R", (), {"content": '{"target": null}'})()
    ev = _evolver(_cfg(universe_evolver_idle_hours=999), mgr, st, llm)
    r = asyncio.run(ev.run(trigger_source="manual"))
    assert r.skipped != "not_idle"  # manual bypassed idle


def test_cron_respects_idle():
    st = MagicMock()
    st.try_acquire_synthesis_lock.return_value = True
    from datetime import UTC, datetime

    st.last_activity_at.return_value = datetime.now(UTC).isoformat()
    ev = _evolver(_cfg(universe_evolver_idle_hours=999), MagicMock(), st)
    r = asyncio.run(ev.run(trigger_source="cron"))
    assert r.skipped == "not_idle"


def test_evolver_lands_persona_mutation(tmp_path):
    from tianshu.storage import Storage
    from tianshu.universe.manager import UniverseManager
    from tianshu.universe.store import UniverseStore

    # Set up live personas/skills dirs
    (p := tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (p / "ROLE.md").write_text("原始职责")
    (tmp_path / "skills").mkdir()

    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    store = UniverseStore(tmp_path / "universes", tmp_path / "personas", tmp_path / "skills")

    class FP:
        runtime_dir = tmp_path / "personas"

        def repoint_runtime(self, _):
            pass

    class FS:
        _user_dir = tmp_path / "skills"

        @property
        def user_dir(self):
            return self._user_dir

        def repoint_user_dir(self, _):
            pass

    mgr = UniverseManager(
        s,
        store,
        FP(),
        FS(),
        config_snapshot=lambda: {"agent_config": {}},
        config_apply=lambda m: None,
    )
    g = mgr.ensure_genesis()

    cfg = _cfg()
    cm = MagicMock()
    cm.agent_config = cfg

    llm = AsyncMock()
    llm.chat.side_effect = [
        type(
            "R",
            (),
            {"content": '{"target": "persona:bingbu/ROLE.md", "reason": "更主动", "name": "实验"}'},
        )(),
        type("R", (), {"content": "改写后的职责：主动协同"})(),
    ]

    ev = UniverseEvolver(llm, mgr, s, cm)
    r = asyncio.run(ev.run(trigger_source="manual"))

    assert r.created_challenger is not None
    assert r.mutation_applied is True
    child_role = (store.personas_dir(r.created_challenger) / "bingbu" / "ROLE.md").read_text()
    champ_role = (store.personas_dir(g["id"]) / "bingbu" / "ROLE.md").read_text()
    assert child_role != champ_role
    assert child_role == "改写后的职责：主动协同"


# --- 行为层 challenger 沙箱配对评估：delta 分流(归档/推荐/留观) ---


def _paired(delta: float, samples: int = 20) -> dict:
    v = {
        "fitness": {"score": 0.7 + delta, "samples": samples},
        "stats": {"cost": 1.0},
        "n": samples,
        "truncated": False,
    }
    b = {
        "fitness": {"score": 0.7, "samples": samples},
        "stats": {"cost": 1.0},
        "n": samples,
        "truncated": False,
    }
    return {"variant": v, "baseline": b, "delta": round(delta, 4), "baseline_cached": False}


def _async_return(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


@pytest.fixture
def evolver_fixture(monkeypatch):
    """人格变异候选分支且 apply_mutation 落地成功的 evolver；沙箱评估结果由各用例自行 monkeypatch。"""
    st = MagicMock()
    st.try_acquire_synthesis_lock.return_value = True
    st.last_activity_at.return_value = None
    st.universe_memorial_stats.return_value = {"total": 0, "success": 0}
    champ = {"id": "champ", "fitness": {"score": 0.5}}
    mgr = MagicMock()
    mgr.champion.return_value = champ
    mgr.champion_id.return_value = "champ"
    mgr.list.return_value = [dict(champ, status="champion")]
    mgr.branch.return_value = {"id": "newch"}
    llm = AsyncMock()
    llm.chat.return_value = type(
        "R", (), {"content": '{"target": "persona:bingbu/ROLE.md", "reason": "r", "name": "n"}'}
    )()
    monkeypatch.setattr(
        "tianshu.universe.mutator.apply_mutation",
        _async_return({"applied": True, "target": "persona:bingbu/ROLE.md", "detail": "ok"}),
    )
    ev = _evolver(_cfg(), mgr, st, llm)
    return ev, mgr


async def test_run_archives_challenger_on_negative_delta(evolver_fixture, monkeypatch):
    """delta ≤ -margin → 候选当场归档(retired)。"""
    evolver, mgr = evolver_fixture
    monkeypatch.setattr(evolver, "_evaluate_behavior_challenger", _async_return(_paired(-0.1)))
    result = await evolver.run(trigger_source="manual")
    assert result.created_challenger in result.retired
    assert result.eval_delta == -0.1
    assert mgr.archive.called


async def test_run_recommends_on_positive_delta(evolver_fixture, monkeypatch):
    evolver, mgr = evolver_fixture
    monkeypatch.setattr(evolver, "_evaluate_behavior_challenger", _async_return(_paired(0.08)))
    result = await evolver.run(trigger_source="manual")
    assert result.promotion_recommended == result.created_challenger
    assert not mgr.switch.called  # 默认不自动晋升,只推荐


async def test_run_keeps_challenger_in_margin_band(evolver_fixture, monkeypatch):
    evolver, mgr = evolver_fixture
    monkeypatch.setattr(evolver, "_evaluate_behavior_challenger", _async_return(_paired(0.02)))
    result = await evolver.run(trigger_source="manual")
    assert result.promotion_recommended is None
    assert result.retired == []
    assert not mgr.archive.called
    assert not mgr.switch.called


async def test_evaluate_behavior_challenger_does_not_cache_truncated_baseline(monkeypatch):
    """_evaluate_behavior_challenger 落库时,baseline 若被预算闸截断则存 None。

    与 Task 4 propose_code_variant 的口径一致:避免下次同指纹命中时,把"评了一半"
    的基线当满量基线复用。
    """
    from pathlib import Path

    storage = MagicMock()
    storage.latest_baseline_fitness.return_value = None
    mgr = MagicMock()
    mgr._store.personas_dir.return_value = Path("/personas/child1")
    eval_harness = MagicMock()
    eval_harness.select_eval_set.return_value = ["g1"]
    eval_harness.eval_set_fingerprint.return_value = "fp-x"
    eval_harness.evaluate_paired.return_value = {
        "variant": {"fitness": {"score": 0.7, "samples": 20}, "stats": {"cost": 0.1}},
        "baseline": {"fitness": {"score": 0.7, "samples": 20}, "truncated": True},
        "delta": 0.0,
        "baseline_cached": False,
    }
    code_store = MagicMock()
    code_store.repo_root = Path("/repo")

    ev = UniverseEvolver(
        AsyncMock(),
        mgr,
        storage,
        MagicMock(),
        code_store=code_store,
        eval_harness=eval_harness,
    )
    monkeypatch.setattr(ev, "_baseline_key", lambda: "champ:abc123")

    await ev._evaluate_behavior_challenger("child1", _cfg())

    saved = storage.save_variant_eval_run.call_args[0][0]
    assert saved["baseline"] is None
