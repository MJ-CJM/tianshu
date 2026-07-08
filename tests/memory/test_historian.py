"""后台史官(迭代 4「记忆 2.0」)——蒸馏成功 memorial 的可复用执行知识。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tianshu.memory.historian import Historian
from tianshu.models import Memorial, TaskStatus
from tianshu.models.edict import Edict


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    async def chat(self, messages):
        return SimpleNamespace(content=self._content)


@pytest.fixture
def historian(storage, config_manager):
    return Historian(storage, config_manager)


def _seed_success(storage, goal="build a report", n_events=1):
    e = Edict(goal=goal)
    storage.save_edict(e)
    m = Memorial(edict_id=e.id, status=TaskStatus.COMPLETED, instruction=goal)
    storage.save_memorial(m)
    for i in range(n_events):
        storage.append_event(
            e.id, m.id, "tool.completed", {"type": "tool.completed", "tool": "bash"}
        )
    return e, m


class TestHistorian:
    async def test_distill_writes_court_insight(self, historian, storage):
        _e, m = _seed_success(storage)
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM("先跑测试再改代码")):
            n = await historian.distill_recent()
        assert n == 1
        # court 记忆里有蒸馏的 insight
        entries = storage.list_memory_by_persona("court")
        assert any("先跑测试再改代码" in getattr(en, "content", "") for en in entries)

    async def test_skip_produces_no_memory(self, historian, storage):
        _e, m = _seed_success(storage)
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM("SKIP")):
            n = await historian.distill_recent()
        assert n == 1  # 处理了,但没写记忆
        entries = storage.list_memory_by_persona("court")
        assert not any(en.memorial_id == m.id for en in entries)

    async def test_idempotent_no_redistill(self, historian, storage):
        _seed_success(storage)
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM("经验一")):
            assert await historian.distill_recent() == 1
        # 第二次:已蒸馏,不重复
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM("经验二")):
            assert await historian.distill_recent() == 0

    async def test_only_success_memorials(self, historian, storage):
        # 失败的 memorial 不被史官蒸馏
        e = Edict(goal="failed task")
        storage.save_edict(e)
        storage.save_memorial(Memorial(edict_id=e.id, status=TaskStatus.FAILED, error="boom"))
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM("x")):
            assert await historian.distill_recent() == 0

    async def test_no_useful_events_skips(self, historian, storage):
        # 成功但无有价值事件 → 不蒸馏(返回 None,但标记已处理)
        e = Edict(goal="empty")
        storage.save_edict(e)
        m = Memorial(edict_id=e.id, status=TaskStatus.COMPLETED)
        storage.save_memorial(m)
        with patch("tianshu.llm.LLMClient", return_value=_FakeLLM("should not be called")):
            n = await historian.distill_recent()
        assert n == 1
        assert not storage.list_memory_by_persona("court")
