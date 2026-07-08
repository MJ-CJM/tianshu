"""PlatformEvalRunner — 配对沙箱评估泛化为平台级回归评测(迭代 2「证明」)。

复用 universe.EvalHarness 的全部机制(分层混采/沙箱回放/fitness 打分),
但评的不是「变体 vs 冠军」而是「平台当前形态 vs 自身历史」:同一评测集
指纹下与上一次运行比 delta,回答"自进化/迭代之后,平台整体变好了吗"
(对标 EvoAgentBench 的 Train/Extract/Evaluate 协议叙事)。

评测是离线活(起沙箱 + 逐条回放 + LLM 调用),入口是 CLI
`tianshu evals run`;gateway 只读台账,不提供 HTTP 触发。
"""

from __future__ import annotations

import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ulid import ULID

from tianshu.universe.eval_harness import EvalHarness

if TYPE_CHECKING:
    from tianshu.storage import Storage

# 平台评测的指纹锚:只锚评测集内容,不掺代码版本——否则每次提交后
# 指纹变化,同集 delta 序列就断了(delta 的意义正是跨代码版本比较)。
_PLATFORM_CHAMPION_KEY = "platform"


def aggregate_failure_distribution(goal_results: list[dict]) -> list[dict]:
    """per-goal 明细 → 失败归因分布(reason/count,按 count 降序)。"""
    counter = Counter(
        r.get("failure_reason") or "unclassified"
        for r in goal_results
        if r.get("status") == "failed"
    )
    return [{"reason": reason, "count": count} for reason, count in counter.most_common()]


def _describe_target(repo_root: Path) -> str:
    """评测对象标识:repo 路径 + git HEAD 短 sha(取不到就只留路径)。"""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        return f"{repo_root}@{sha}"
    except Exception:  # noqa: BLE001 - 非 git 环境下 target 退化为纯路径
        return str(repo_root)


class PlatformEvalRunner:
    def __init__(self, storage: Storage, harness: EvalHarness, *, repo_root: Path) -> None:
        self._storage = storage
        self._harness = harness
        self._repo_root = Path(repo_root)

    def resolve_eval_set(
        self, *, set_name: str | None = None, size: int = 8
    ) -> tuple[str | None, list[str]]:
        """set_name 给定则加载已存评测集(固定回归集),否则现场分层混采。"""
        if set_name:
            saved = self._storage.get_eval_set(set_name)
            if saved is None:
                raise ValueError(f"eval set not found: {set_name}")
            return set_name, saved["goals"]
        return None, self._harness.select_eval_set(size)

    def sample_and_save(self, name: str, *, size: int = 8) -> list[str]:
        """分层混采一份评测集并落库(固化为可重复的回归集)。"""
        goals = self._harness.select_eval_set(size)
        if not goals:
            raise ValueError("no historical memorials to sample from")
        self._storage.save_eval_set(name, goals, source="sampled")
        return goals

    def run(
        self,
        *,
        set_name: str | None = None,
        size: int = 8,
        goal_timeout_s: int = 300,
        budget_cny: float | None = None,
    ) -> dict:
        """跑一次平台回归评测:沙箱回放 → 打分 → 与同指纹上一 run 比 delta → 落台账。

        返回的 run dict 额外带 failure_distribution(派生自 goal_results,
        不落库;台账读回后可用 aggregate_failure_distribution 重算)。
        """
        name, goals = self.resolve_eval_set(set_name=set_name, size=size)
        if not goals:
            raise ValueError("eval set is empty (no historical memorials to sample from)")

        fingerprint = EvalHarness.eval_set_fingerprint(goals, _PLATFORM_CHAMPION_KEY)
        prev = self._storage.latest_platform_eval_run(fingerprint)

        result = self._harness.evaluate(
            self._repo_root,
            eval_set=goals,
            goal_timeout_s=goal_timeout_s,
            budget_cny=budget_cny,
        )

        delta = None
        if prev is not None:
            delta = round(
                result["fitness"].get("score", 0.0) - prev["fitness"].get("score", 0.0), 4
            )

        run = {
            "id": str(ULID()),
            "eval_set_name": name,
            "eval_set_fingerprint": fingerprint,
            "target": _describe_target(self._repo_root),
            "fitness": result["fitness"],
            "stats": result["stats"],
            "goal_results": result.get("goal_results", []),
            "n": result["n"],
            "truncated": result["truncated"],
            "delta_vs_prev": delta,
            "created_at": datetime.now(UTC).isoformat(),
        }
        self._storage.save_platform_eval_run(run)
        return {
            **run,
            "failure_distribution": aggregate_failure_distribution(run["goal_results"]),
        }
