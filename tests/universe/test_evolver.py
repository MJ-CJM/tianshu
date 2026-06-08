import asyncio
from unittest.mock import AsyncMock, MagicMock
from tianshu.universe.evolver import UniverseEvolver


def _cfg(**over):
    d = dict(parallel_universe_enabled=True, universe_evolver_idle_hours=0,
             universe_challenger_fail_limit=3, universe_min_samples=10,
             universe_promote_margin=0.05, universe_auto_promote=False)
    d.update(over)
    return type("C", (), d)()


def _evolver(cfg, mgr, storage, llm=None):
    cm = MagicMock(); cm.agent_config = cfg
    return UniverseEvolver(llm or AsyncMock(), mgr, storage, cm)


def test_disabled_skips():
    cm_cfg = type("C", (), {"parallel_universe_enabled": False})()
    ev = _evolver(cm_cfg, MagicMock(), MagicMock())
    assert asyncio.run(ev.run()).skipped == "disabled"


def test_lock_held_skips():
    st = MagicMock(); st.try_acquire_synthesis_lock.return_value = False
    st.last_activity_at.return_value = None
    ev = _evolver(_cfg(), MagicMock(), st)
    assert asyncio.run(ev.run()).skipped == "lock_held"


def test_no_champion_releases_lock():
    st = MagicMock(); st.try_acquire_synthesis_lock.return_value = True
    st.last_activity_at.return_value = None
    mgr = MagicMock(); mgr.champion.return_value = None
    ev = _evolver(_cfg(), mgr, st)
    assert asyncio.run(ev.run()).skipped == "no_champion"
    assert st.release_synthesis_lock.called


def test_retires_failing_challenger():
    st = MagicMock(); st.try_acquire_synthesis_lock.return_value = True
    st.last_activity_at.return_value = None
    st.universe_memorial_stats.return_value = {"total": 5, "success": 0}
    champ = {"id": "champ", "fitness": {"score": 0.5}}
    mgr = MagicMock(); mgr.champion.return_value = champ
    mgr.list.return_value = [dict(champ, status="champion"),
                             {"id": "bad", "status": "challenger", "fitness": {}}]
    llm = AsyncMock(); llm.chat.return_value = type("R", (), {"content": '{"target": null}'})()
    ev = _evolver(_cfg(), mgr, st, llm)
    r = asyncio.run(ev.run())
    assert "bad" in r.retired


def test_recommends_promotion_without_switch():
    st = MagicMock(); st.try_acquire_synthesis_lock.return_value = True
    st.last_activity_at.return_value = None
    st.universe_memorial_stats.return_value = {"total": 0, "success": 0}
    champ = {"id": "champ", "fitness": {"score": 0.4}}
    mgr = MagicMock(); mgr.champion.return_value = champ
    mgr.list.return_value = [dict(champ, status="champion"),
                             {"id": "win", "status": "challenger",
                              "fitness": {"score": 0.9, "samples": 50}}]
    llm = AsyncMock(); llm.chat.return_value = type("R", (), {"content": '{"target": null}'})()
    ev = _evolver(_cfg(), mgr, st, llm)
    r = asyncio.run(ev.run())
    assert r.promotion_recommended == "win"
    assert not mgr.switch.called


def test_auto_promote_switches():
    st = MagicMock(); st.try_acquire_synthesis_lock.return_value = True
    st.last_activity_at.return_value = None
    st.universe_memorial_stats.return_value = {"total": 0, "success": 0}
    champ = {"id": "champ", "fitness": {"score": 0.4}}
    mgr = MagicMock(); mgr.champion.return_value = champ
    mgr.list.return_value = [dict(champ, status="champion"),
                             {"id": "win", "status": "challenger",
                              "fitness": {"score": 0.9, "samples": 50}}]
    llm = AsyncMock(); llm.chat.return_value = type("R", (), {"content": '{"target": null}'})()
    ev = _evolver(_cfg(universe_auto_promote=True), mgr, st, llm)
    asyncio.run(ev.run())
    assert mgr.switch.called


def test_mutation_branches_challenger():
    st = MagicMock(); st.try_acquire_synthesis_lock.return_value = True
    st.last_activity_at.return_value = None
    st.universe_memorial_stats.return_value = {"total": 0, "success": 0}
    champ = {"id": "champ", "fitness": {"score": 0.5}}
    mgr = MagicMock(); mgr.champion.return_value = champ
    mgr.list.return_value = [dict(champ, status="champion")]
    mgr.branch.return_value = {"id": "newch"}
    llm = AsyncMock()
    llm.chat.return_value = type("R", (), {"content": '{"target": "policy", "reason": "r", "name": "n"}'})()
    ev = _evolver(_cfg(), mgr, st, llm)
    r = asyncio.run(ev.run())
    assert r.created_challenger == "newch"
    assert mgr.branch.called


def test_manual_bypasses_idle():
    # idle gate would block (idle_hours high, last activity recent) but manual bypasses it
    st = MagicMock(); st.try_acquire_synthesis_lock.return_value = True
    from datetime import datetime, UTC
    st.last_activity_at.return_value = datetime.now(UTC).isoformat()
    champ = {"id": "champ", "fitness": {"score": 0.5}}
    mgr = MagicMock(); mgr.champion.return_value = champ
    mgr.list.return_value = [dict(champ, status="champion")]
    llm = AsyncMock(); llm.chat.return_value = type("R", (), {"content": '{"target": null}'})()
    ev = _evolver(_cfg(universe_evolver_idle_hours=999), mgr, st, llm)
    r = asyncio.run(ev.run(trigger_source="manual"))
    assert r.skipped != "not_idle"  # manual bypassed idle


def test_cron_respects_idle():
    st = MagicMock(); st.try_acquire_synthesis_lock.return_value = True
    from datetime import datetime, UTC
    st.last_activity_at.return_value = datetime.now(UTC).isoformat()
    ev = _evolver(_cfg(universe_evolver_idle_hours=999), MagicMock(), st)
    r = asyncio.run(ev.run(trigger_source="cron"))
    assert r.skipped == "not_idle"


def test_evolver_lands_persona_mutation(tmp_path):
    from pathlib import Path
    from tianshu.storage import Storage
    from tianshu.universe.store import UniverseStore
    from tianshu.universe.manager import UniverseManager

    # Set up live personas/skills dirs
    (p := tmp_path / "personas" / "bingbu").mkdir(parents=True)
    (p / "ROLE.md").write_text("原始职责")
    (tmp_path / "skills").mkdir()

    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    store = UniverseStore(tmp_path / "universes", tmp_path / "personas", tmp_path / "skills")

    class FP:
        runtime_dir = tmp_path / "personas"
        def repoint_runtime(self, _): pass

    class FS:
        _user_dir = tmp_path / "skills"
        @property
        def user_dir(self): return self._user_dir
        def repoint_user_dir(self, _): pass

    mgr = UniverseManager(
        s, store, FP(), FS(),
        config_snapshot=lambda: {"agent_config": {}},
        config_apply=lambda m: None,
    )
    g = mgr.ensure_genesis()

    cfg = _cfg()
    cm = MagicMock(); cm.agent_config = cfg

    llm = AsyncMock()
    llm.chat.side_effect = [
        type("R", (), {"content": '{"target": "persona:bingbu/ROLE.md", "reason": "更主动", "name": "实验"}'})(),
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
