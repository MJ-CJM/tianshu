"""京察·官员考核(迭代 7「制度补全」)——称职 / 观政 / 不称职三等考语。"""

from __future__ import annotations

from tianshu.persona.jingcha import Jingcha


class _FakeStorage:
    def __init__(self, personas, stats):
        self._personas = personas
        self._stats = stats

    def list_personas(self):
        return self._personas

    def get_persona_stats(self, pid):
        return self._stats[pid]


def _storage(stats):
    personas = [{"id": pid, "name": pid} for pid in stats]
    return _FakeStorage(personas, stats)


def test_verdicts_three_tiers():
    storage = _storage(
        {
            "star": {"total_executions": 20, "success_rate": 95.0, "total_cost_cny": 1.0},
            "green": {"total_executions": 2, "success_rate": 50.0, "total_cost_cny": 0.1},
            "poor": {"total_executions": 30, "success_rate": 40.0, "total_cost_cny": 9.0},
        }
    )
    report = Jingcha(storage).review(min_executions=5, pass_rate=80.0)
    by_id = {e["persona_id"]: e for e in report["evaluations"]}
    assert by_id["star"]["verdict"] == "称职"
    assert by_id["green"]["verdict"] == "观政"  # 数据不足
    assert by_id["poor"]["verdict"] == "不称职"
    assert "致仕" in by_id["poor"]["recommendation"]
    assert report["summary"] == {"total": 3, "称职": 1, "观政": 1, "不称职": 1}


def test_empty_roster():
    report = Jingcha(_storage({})).review()
    assert report["summary"]["total"] == 0
    assert report["evaluations"] == []
