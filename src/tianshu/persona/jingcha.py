"""Jingcha（京察·官员考核）—— 周期考核官员绩效,不称职触发演化/致仕建议(迭代 7「制度补全」)。

明制京察三年一考察京官。天枢的京察把 Phase 3.12 的官员绩效评估制度化:按成功率/执行量
给每位官员一个考语——**称职 / 观政(数据不足) / 不称职**;不称职者附建议(演化提案或致仕),
与位面演化联动。复用既有 get_persona_stats,不重复统计。司礼监代批准确率进考核为后续增量。
"""

from __future__ import annotations

from typing import Any


class Jingcha:
    def __init__(self, storage: Any) -> None:
        self._storage = storage

    def review(self, *, min_executions: int = 5, pass_rate: float = 80.0) -> dict:
        """对全体官员出考核报告。返回 {evaluations:[...], summary:{...}}。"""
        evaluations = [
            self._evaluate(p, min_executions, pass_rate) for p in self._storage.list_personas()
        ]
        summary = {
            "total": len(evaluations),
            "称职": sum(1 for e in evaluations if e["verdict"] == "称职"),
            "观政": sum(1 for e in evaluations if e["verdict"] == "观政"),
            "不称职": sum(1 for e in evaluations if e["verdict"] == "不称职"),
        }
        return {"evaluations": evaluations, "summary": summary}

    def _evaluate(self, persona: dict, min_executions: int, pass_rate: float) -> dict:
        pid = persona.get("id") or persona.get("persona_id") or "?"
        stats = self._storage.get_persona_stats(pid)
        total = stats.get("total_executions", 0)
        rate = stats.get("success_rate", 0.0)
        if total < min_executions:
            verdict, recommendation = "观政", "历练尚浅,数据不足,继续观察"
        elif rate >= pass_rate:
            verdict, recommendation = "称职", "考绩优,留任"
        else:
            verdict, recommendation = (
                "不称职",
                f"成功率 {rate:.0f}% 低于 {pass_rate:.0f}%,建议上演化提案调优人格,屡考不称职可议致仕",
            )
        return {
            "persona_id": pid,
            "name": persona.get("name") or persona.get("display_name") or pid,
            "verdict": verdict,
            "recommendation": recommendation,
            "stats": {
                "total_executions": total,
                "success_rate": rate,
                "total_cost_cny": stats.get("total_cost_cny", 0.0),
            },
        }
