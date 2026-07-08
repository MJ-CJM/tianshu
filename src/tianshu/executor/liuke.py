"""Liuke（六科给事中·封驳）—— 诏令提交预检:封还 / 升 plan_review 票拟(迭代 7「制度补全」)。

明制六科给事中掌"封驳"——诏旨不当可原封退回。天枢的六科在敕令**提交时**预检:
- **封还(reject)**:目标超长(zeroclaw 入站防护的 body 上限类比)等明显不合规,拒收并说明理由;
- **升票拟(plan_review)**:基于记忆宫殿历史的成本预估,或用户声明预算超阈 → 自动置 plan_review
  联动票拟(D9),交人工规划审批而非硬拒——**只升级不删事,非破坏**;
- **放行(pass)**:无异常。

出厂默认开(只做非破坏的升级/超长封还)。成本预估 v1 取历史每敕令均值,按 goal 相似度
加权检索为后续增量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class PrecheckResult:
    verdict: Literal["pass", "plan_review", "reject"]
    reason: str
    estimated_cost_cny: float = 0.0


class Liuke:
    def __init__(self, storage: Any, config_manager: Any) -> None:
        self._storage = storage
        self._config = config_manager

    def precheck(self, goal: str, declared_budget_cny: float | None = None) -> PrecheckResult:
        """提交预检。禁用时一律放行;否则超长封还、成本超阈升 plan_review。"""
        cfg = self._config.agent_config
        if not getattr(cfg, "liuke_enabled", True):
            return PrecheckResult("pass", "六科预检未启用")

        max_chars = getattr(cfg, "liuke_max_goal_chars", 8000)
        if len(goal or "") > max_chars:
            return PrecheckResult(
                "reject", f"诏令封还:目标长度 {len(goal)} 超上限 {max_chars}(疑似超载/注入)"
            )

        threshold = getattr(cfg, "liuke_cost_threshold_cny", 5.0)
        est, samples = self._estimate_cost()
        # 声明预算与历史预估取大者作为风险口径
        projected = max(est, declared_budget_cny or 0.0)
        if projected > threshold:
            basis = (
                f"历史每敕令均值 ¥{est:.2f}(n={samples})"
                if est >= (declared_budget_cny or 0.0)
                else f"声明预算 ¥{declared_budget_cny:.2f}"
            )
            return PrecheckResult(
                "plan_review",
                f"预估成本 ¥{projected:.2f} 超阈 ¥{threshold:.2f}({basis}),升票拟待人工规划审批",
                projected,
            )
        return PrecheckResult("pass", "六科预检通过", est)

    def _estimate_cost(self) -> tuple[float, int]:
        try:
            return self._storage.estimate_edict_cost()
        except Exception:  # noqa: BLE001
            return 0.0, 0
