"""探索路由已退役:无论开关/候选状态,route_for_memorial 一律归冠军。"""


class _FakeStorage:
    def __init__(self, champion=None, universes=()):
        self._champ = champion
        self._unis = list(universes)

    def get_champion_universe(self):
        return self._champ

    def list_universes(self, include_archived=True):
        return [u for u in self._unis if include_archived or u["status"] != "archived"]


def _mgr(champion, universes=()):
    from tianshu.universe.manager import UniverseManager

    return UniverseManager(
        storage=_FakeStorage(champion, universes),
        store=None,
        persona_loader=None,
        skills_loader=None,
        config_snapshot=lambda: {},
        config_apply=lambda m: None,
    )


def test_route_returns_champion_even_with_challengers():
    champ = {"id": "u-champ", "status": "champion"}
    challenger = {"id": "u-chal", "status": "challenger", "code_ref": None}
    mgr = _mgr(champ, [champ, challenger])
    for i in range(20):  # 任意 memorial_id 都归冠军(旧版曾按哈希分桶)
        assert mgr.route_for_memorial(f"mem-{i}") == "u-champ"


def test_route_returns_none_without_champion():
    assert _mgr(None).route_for_memorial("mem-1") is None
