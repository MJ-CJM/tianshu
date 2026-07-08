"""时序知识图谱(迭代 4「记忆 2.0」)——校勘门 + as_of 时序 + scope 隔离。"""

from __future__ import annotations

import pytest

from tianshu.memory.kg import KnowledgeGraph

_T1 = "2026-01-01T00:00:00+00:00"
_T2 = "2026-02-01T00:00:00+00:00"
_T3 = "2026-03-01T00:00:00+00:00"


@pytest.fixture
def kg(storage):
    return KnowledgeGraph(storage)


class TestCollationGate:
    def test_inserted(self, kg):
        r = kg.assert_fact("user", "prefers", "concise", scope="user", now=_T1)
        assert r.verdict == "inserted" and r.superseded_id is None

    def test_idempotent_same_object(self, kg):
        r1 = kg.assert_fact("user", "prefers", "concise", scope="user", now=_T1)
        r2 = kg.assert_fact("user", "prefers", "concise", scope="user", now=_T2)
        assert r2.verdict == "idempotent" and r2.triple_id == r1.triple_id

    def test_updated_temporal_supersede(self, kg):
        r1 = kg.assert_fact("user", "prefers", "concise", scope="user", now=_T1)
        r3 = kg.assert_fact("user", "prefers", "detailed", scope="user", now=_T3)
        assert r3.verdict == "updated" and r3.superseded_id == r1.triple_id


class TestAsOfQuery:
    def test_current_sees_only_latest(self, kg):
        kg.assert_fact("user", "prefers", "concise", scope="user", now=_T1)
        kg.assert_fact("user", "prefers", "detailed", scope="user", now=_T3)
        cur = kg.query(scope="user", subject="user", predicate="prefers")
        assert len(cur) == 1 and cur[0]["object"] == "detailed"

    def test_as_of_sees_historical(self, kg):
        kg.assert_fact("user", "prefers", "concise", scope="user", now=_T1)
        kg.assert_fact("user", "prefers", "detailed", scope="user", now=_T3)
        old = kg.query(
            scope="user", subject="user", predicate="prefers", as_of="2026-02-15T00:00:00+00:00"
        )
        assert len(old) == 1 and old[0]["object"] == "concise"

    def test_as_of_before_any_fact(self, kg):
        kg.assert_fact("user", "prefers", "concise", scope="user", now=_T2)
        assert kg.query(scope="user", subject="user", as_of=_T1) == []


class TestScopeIsolation:
    def test_scopes_do_not_bleed(self, kg):
        kg.assert_fact("user", "prefers", "concise", scope="user", now=_T1)
        assert kg.query(scope="court", subject="user", predicate="prefers") == []
        assert len(kg.query(scope="user", subject="user")) == 1


class TestManualInvalidate:
    def test_invalidate_removes_from_current(self, kg):
        r = kg.assert_fact("user", "likes", "python", scope="user", now=_T1)
        kg.invalidate(r.triple_id, now=_T2)
        assert kg.query(scope="user", subject="user", predicate="likes") == []
        # 但历史仍可 as_of 查到
        assert len(kg.query(scope="user", subject="user", as_of=_T1)) == 1
