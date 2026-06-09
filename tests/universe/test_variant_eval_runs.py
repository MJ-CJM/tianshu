"""Tests for variant_eval_runs storage + code_variant config defaults."""
from tianshu.config_manager import AgentConfigState
from tianshu.storage import Storage


def test_code_variant_config_defaults():
    s = AgentConfigState()
    assert s.code_variant_enabled is False
    assert s.code_variant_auto_promote is False
    assert s.code_variant_sandbox_timeout_s == 900
    assert s.code_variant_eval_set_size == 20
    assert "src/tianshu/planner/" in s.code_variant_evolvable_paths


def test_variant_eval_run_roundtrip(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_variant_eval_run({
        "id": "r1", "universe_id": "u1",
        "gate_passed": True, "gate_detail": {"static": "ok", "tests": "pass"},
        "fitness": {"score": 0.8}, "eval_set_version": "v1",
        "cost": 1.25, "created_at": "2026-06-08T00:00:00+00:00",
    })
    runs = s.list_variant_eval_runs("u1")
    assert len(runs) == 1
    r = runs[0]
    assert r["gate_passed"] is True
    assert r["gate_detail"] == {"static": "ok", "tests": "pass"}
    assert r["fitness"]["score"] == 0.8
    assert r["cost"] == 1.25


def test_list_variant_eval_runs_empty(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    assert s.list_variant_eval_runs("nope") == []
