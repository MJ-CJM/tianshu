"""Tests for Storage universe CRUD methods."""
from tianshu.storage import Storage


def test_universe_crud_and_single_champion(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe(
        {
            "id": "u1",
            "name": "genesis",
            "status": "champion",
            "origin": "genesis",
            "created_at": "2026-06-07T00:00:00+00:00",
        }
    )
    assert s.get_champion_universe()["id"] == "u1"
    s.save_universe(
        {
            "id": "u2",
            "name": "exp",
            "status": "challenger",
            "origin": "manual_branch",
            "created_at": "2026-06-07T01:00:00+00:00",
        }
    )
    assert len(s.list_universes()) == 2
    s.set_universe_status("u1", "challenger")
    s.set_universe_status("u2", "champion")
    assert s.get_champion_universe()["id"] == "u2"
    s.update_universe_fitness("u2", {"score": 0.9, "samples": 30})
    assert s.get_universe("u2")["fitness"]["score"] == 0.9


def test_get_universe_returns_none_for_missing(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    assert s.get_universe("nonexistent") is None


def test_get_champion_returns_none_when_empty(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    assert s.get_champion_universe() is None


def test_list_excludes_archived_when_requested(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe(
        {
            "id": "a",
            "name": "a",
            "status": "challenger",
            "origin": "manual_branch",
            "created_at": "2026-06-07T00:00:00+00:00",
        }
    )
    s.save_universe(
        {
            "id": "b",
            "name": "b",
            "status": "archived",
            "origin": "manual_branch",
            "created_at": "2026-06-07T00:00:01+00:00",
        }
    )
    assert {u["id"] for u in s.list_universes(include_archived=False)} == {"a"}
    assert len(s.list_universes()) == 2


def test_universe_fitness_defaults_to_empty(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe(
        {
            "id": "u1",
            "name": "genesis",
            "status": "champion",
            "origin": "genesis",
            "created_at": "2026-06-07T00:00:00+00:00",
        }
    )
    u = s.get_universe("u1")
    assert u["fitness"] == {} or u["fitness"] is None


def test_universe_roundtrip_optional_fields(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe(
        {
            "id": "u1",
            "name": "test",
            "status": "challenger",
            "origin": "manual_branch",
            "parent_universe_id": "parent-1",
            "mutation_reason": "test reason",
            "description": "desc",
            "created_at": "2026-06-07T00:00:00+00:00",
        }
    )
    u = s.get_universe("u1")
    assert u["parent_universe_id"] == "parent-1"
    assert u["description"] == "desc"


def test_universe_code_ref_roundtrip(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe({
        "id": "cv1", "name": "code-variant",
        "status": "challenger", "origin": "code_variant",
        "code_ref": "universe/cv1",
        "created_at": "2026-06-08T00:00:00+00:00",
    })
    u = s.get_universe("cv1")
    assert u["code_ref"] == "universe/cv1"
    assert u["origin"] == "code_variant"


def test_universe_code_ref_defaults_none(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe({
        "id": "d1", "name": "data", "status": "challenger",
        "origin": "manual_branch", "created_at": "2026-06-08T00:00:00+00:00",
    })
    assert s.get_universe("d1")["code_ref"] is None


def test_delete_universe_removes_row_and_eval_runs(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    s.save_universe({
        "id": "u1", "name": "test", "status": "challenger",
        "origin": "manual_branch", "created_at": "2026-06-08T00:00:00+00:00",
    })
    from datetime import UTC, datetime
    s.save_variant_eval_run({
        "id": "r1", "universe_id": "u1",
        "gate_passed": True, "gate_detail": None,
        "fitness": {"score": 0.9}, "eval_set_version": "v1",
        "cost": 0.01,
        "created_at": datetime.now(UTC).isoformat(),
    })
    assert s.get_universe("u1") is not None
    assert len(s.list_variant_eval_runs("u1")) == 1

    s.delete_universe("u1")

    assert s.get_universe("u1") is None
    assert s.list_variant_eval_runs("u1") == []


def test_delete_universe_nonexistent_is_noop(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_db()
    # Should not raise
    s.delete_universe("ghost-id")
