from unittest.mock import MagicMock

from tianshu.universe.manager import UniverseManager


def _mgr(enabled, ratio, challengers):
    m = UniverseManager.__new__(UniverseManager)
    m._storage = MagicMock()
    m._storage.get_champion_universe.return_value = {"id": "champ"}
    m._storage.list_universes.return_value = [{"id": "champ", "status": "champion"}] + [
        {"id": f"c{i}", "status": "challenger"} for i in range(challengers)
    ]
    m._agent_config = lambda: type(
        "C", (), {"parallel_universe_enabled": enabled, "universe_explore_ratio": ratio}
    )()
    return m


def test_disabled_always_champion():
    assert _mgr(False, 0.5, 2).route_for_memorial("01ABCXYZJKMNPQRSTVWZ00000") == "champ"


def test_no_challengers_returns_champion():
    assert _mgr(True, 0.5, 0).route_for_memorial("01ABCXYZJKMNPQRSTVWZ00000") == "champ"


def test_zero_ratio_returns_champion():
    assert _mgr(True, 0.0, 2).route_for_memorial("01ABCXYZJKMNPQRSTVWZ00000") == "champ"


def test_full_ratio_routes_to_challenger():
    out = _mgr(True, 1.0, 2).route_for_memorial("01ABCXYZJKMNPQRSTVWZ00000")
    assert out.startswith("c")


def test_routing_is_deterministic():
    m = _mgr(True, 0.5, 3)
    assert m.route_for_memorial("ZZZTESTID") == m.route_for_memorial("ZZZTESTID")


def test_ulid_chars_do_not_raise():
    # ULIDs use Crockford base32 (non-hex chars) — must not raise
    _mgr(True, 0.5, 2).route_for_memorial("01J9ZZHKQWERTYUVWXYZ23456")
