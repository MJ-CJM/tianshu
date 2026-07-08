"""自进化「请旨解锁」(迭代 6「演化 2.0」,ADR-0004)——默认关 + 达阈值上奏折 + 批红开启。"""

from __future__ import annotations

import pytest

from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.models.memorial import Memorial
from tianshu.universe.unlock import EvolutionUnlock


@pytest.fixture
def unlock(storage, config_manager):
    config_manager.update_agent_config(parallel_universe_enabled=False)
    storage.save_edict(Edict(id="e1", goal="seed"))
    return EvolutionUnlock(storage, config_manager, threshold_memorials=3)


def _seed_successful(storage, n: int) -> None:
    for _ in range(n):
        storage.save_memorial(Memorial(edict_id="e1", status=TaskStatus.COMPLETED))


class TestPetition:
    async def test_below_threshold_no_petition(self, unlock, storage):
        _seed_successful(storage, 2)
        result = await unlock.check_and_petition()
        assert result["skipped"] == "below_threshold"
        assert storage.get_pending_petition("behavior_evolution") is None

    async def test_at_threshold_raises_petition(self, unlock, storage):
        _seed_successful(storage, 3)
        result = await unlock.check_and_petition()
        assert "petitioned" in result
        pending = storage.get_pending_petition("behavior_evolution")
        assert pending is not None and pending["status"] == "pending"
        assert "如何回滚" in pending["plan"]  # 白话三段其一

    async def test_idempotent_no_duplicate(self, unlock, storage):
        _seed_successful(storage, 5)
        first = await unlock.check_and_petition()
        second = await unlock.check_and_petition()
        assert "petitioned" in first
        assert second["skipped"] == "already_petitioned"
        assert len(storage.list_petitions()) == 1

    async def test_skip_when_already_enabled(self, unlock, storage, config_manager):
        _seed_successful(storage, 9)
        config_manager.update_agent_config(parallel_universe_enabled=True)
        result = await unlock.check_and_petition()
        assert result["skipped"] == "already_enabled"


class TestResolve:
    async def test_grant_enables_evolution(self, unlock, storage, config_manager):
        _seed_successful(storage, 3)
        pid = (await unlock.check_and_petition())["petitioned"]
        result = unlock.grant(pid)
        assert result["granted"] == pid
        assert config_manager.agent_config.parallel_universe_enabled is True
        assert storage.get_petition(pid)["status"] == "granted"

    async def test_grant_unknown_petition(self, unlock):
        assert unlock.grant("nonexistent")["error"] == "not_found"

    async def test_dismiss_keeps_evolution_off(self, unlock, storage, config_manager):
        _seed_successful(storage, 3)
        pid = (await unlock.check_and_petition())["petitioned"]
        result = unlock.dismiss(pid)
        assert result["dismissed"] == pid
        assert config_manager.agent_config.parallel_universe_enabled is False
        assert storage.get_petition(pid)["status"] == "dismissed"

    async def test_grant_twice_second_is_noop(self, unlock, storage):
        _seed_successful(storage, 3)
        pid = (await unlock.check_and_petition())["petitioned"]
        unlock.grant(pid)
        assert unlock.grant(pid)["skipped"] == "granted"
