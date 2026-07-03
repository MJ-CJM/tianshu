"""_update_universe_fitness:在线信号只归 champion,不覆盖 challenger 的沙箱评估分。"""

from tianshu.bootstrap.universe_hooks import _update_universe_fitness


class _FakeMemorial:
    def __init__(self, universe_id):
        self.universe_id = universe_id


class _FakeStorage:
    def __init__(self, champion_id):
        self._champ = {"id": champion_id} if champion_id else None
        self.updated = []

    def get_memorial(self, memorial_id):
        return _FakeMemorial("u-target")

    def get_champion_universe(self):
        return self._champ

    def universe_memorial_stats(self, universe_id):
        return {
            "total": 5,
            "success": 5,
            "retries": 0,
            "audited": 0,
            "audit_pass": 0,
            "cost": 0.0,
            "feedback": 0,
        }

    def update_universe_fitness(self, universe_id, fitness):
        self.updated.append(universe_id)


class _FakeConfigManager:
    class agent_config:  # noqa: N801
        parallel_universe_enabled = True
        universe_fitness_weights = (0.4, 0.15, 0.2, 0.1, 0.15)


class _FakeEvent:
    memorial_id = "mem-1"


async def test_champion_fitness_updated():
    storage = _FakeStorage(champion_id="u-target")
    await _update_universe_fitness(
        _FakeEvent(), config_manager=_FakeConfigManager(), storage=storage
    )
    assert storage.updated == ["u-target"]


async def test_challenger_fitness_not_overwritten():
    storage = _FakeStorage(champion_id="u-other")  # memorial 归属 u-target ≠ 冠军
    await _update_universe_fitness(
        _FakeEvent(), config_manager=_FakeConfigManager(), storage=storage
    )
    assert storage.updated == []
