"""起居注官(迭代 4「记忆 2.0」)——从用户行为信号蒸馏偏好画像 + 偏好三元组入 KG。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tianshu.memory.diarist import Diarist
from tianshu.memory.kg import KnowledgeGraph
from tianshu.models import Decree, Memorial, TaskStatus
from tianshu.models.edict import Edict


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    async def chat(self, messages):
        return SimpleNamespace(content=self._content)


_PROFILE_RESPONSE = """《起居注·主人偏好》
主人偏好简洁输出,常做 Python 项目,批红多数直接批准。

---TRIPLES---
prefers|简洁输出
works_on|Python 项目
review_habit|多数直接批准"""


@pytest.fixture
def diarist(storage, config_manager, tmp_path):
    kg = KnowledgeGraph(storage)
    return Diarist(storage, config_manager, kg, memory_dir=tmp_path / "memory"), kg


def _seed_signals(storage, n=5):
    """播种足够的批红/反馈信号(>=3 才蒸馏)。"""
    for i in range(n):
        e = Edict(goal=f"task {i} python script")
        storage.save_edict(e)
        m = Memorial(edict_id=e.id, status=TaskStatus.COMPLETED, feedback_score=1)
        storage.save_memorial(m)
        storage.save_decree(Decree(memorial_id=m.id, action="approve", actor="human"))


class TestDiarist:
    async def test_synthesize_writes_profile_and_kg(self, diarist, storage):
        d, kg = diarist
        _seed_signals(storage)
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM(_PROFILE_RESPONSE)):
            assert await d.synthesize() is True
        # USER_PROFILE.md 写了
        assert "简洁输出" in d.read_profile()
        # 偏好三元组入 KG(scope=user)
        prefs = kg.query(scope="user", subject="user", predicate="prefers")
        assert len(prefs) == 1 and prefs[0]["object"] == "简洁输出"
        assert len(kg.query(scope="user", subject="user")) == 3

    async def test_sparse_signals_skip(self, diarist, storage):
        d, _kg = diarist
        # 只 1 个信号 < 3 阈值 → skip
        e = Edict(goal="one")
        storage.save_edict(e)
        m = Memorial(edict_id=e.id, status=TaskStatus.COMPLETED)
        storage.save_memorial(m)
        storage.save_decree(Decree(memorial_id=m.id, action="approve", actor="human"))
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM(_PROFILE_RESPONSE)):
            assert await d.synthesize() is False
        assert d.read_profile() == ""

    async def test_preference_drift_via_kg(self, diarist, storage):
        d, kg = diarist
        _seed_signals(storage)
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM(_PROFILE_RESPONSE)):
            await d.synthesize()
        # 主人偏好漂移:第二次蒸馏出不同偏好 → KG 时序更新
        drift = "《偏好》\n---TRIPLES---\nprefers|详细输出"
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM(drift)):
            await d.synthesize()
        cur = kg.query(scope="user", subject="user", predicate="prefers")
        assert len(cur) == 1 and cur[0]["object"] == "详细输出"  # 新偏好接位

    def test_parse_handles_no_triples(self):
        profile, triples = Diarist._parse("just a profile, no triples section")
        assert "just a profile" in profile and triples == []
