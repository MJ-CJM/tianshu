"""Silijian（司礼监·代批）—— 学习用户批红习惯,对低风险 decree 自动代批(迭代 7「制度补全」)。

解 HITL 最大痛点(审批疲劳):不是一刀切自动放行,而是**四道闸**防"宦官专权":
1. 只代批策略允许的**低风险类**(tool_tier ≤ silijian_max_tier);
2. 每笔**留痕可撤**(先提交建议,再由统一 DecisionService 授权并投影 Decree,可回滚);
3. 用户可随时**一键收回批红权**(关配置开关)+ **急停穿透**(estop engaged 时一律不代批);
4. 代批基于**历史同类高通过率**(近期人工批红通过率 ≥ 阈值且样本足,冷启动不代批),
   准确率进京察考核。

与迭代 5 approve_for_session 衔接:那是会话级白名单(用户显式授权),司礼监是跨会话学习型代批。
出厂默认关(silijian_enabled=False)。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SilijianApprovalProposal:
    """Non-authoritative proposal for the generic DecisionService."""

    reason: str


class Silijian:
    def __init__(self, storage: Any, config_manager: Any, estop_manager: Any = None) -> None:
        self._storage = storage
        self._config = config_manager
        self._estop = estop_manager

    def maybe_auto_approve(
        self, memorial_id: str, tool_tier: Any, rule_id: str
    ) -> SilijianApprovalProposal | None:
        """四道闸全过则提出批准建议；持久授权仅由 DecisionService 完成。"""
        cfg = self._config.agent_config
        if not getattr(cfg, "silijian_enabled", False):  # 闸1:一键收回=关此开关
            return None
        if self._estop_engaged():  # 闸3:急停穿透,代批即停
            return None
        tier_val = int(getattr(tool_tier, "value", tool_tier))
        if tier_val > getattr(cfg, "silijian_max_tier", 1):  # 闸1续:仅低风险类
            return None
        rate, samples = self._recent_human_approval_rate()
        if samples < getattr(cfg, "silijian_min_samples", 10):  # 闸4:冷启动不代批
            return None
        if rate < getattr(cfg, "silijian_min_approval_rate", 0.9):  # 闸4:同类高通过率
            return None

        proposal = SilijianApprovalProposal(
            reason=(
                f"司礼监代批:低风险(T{tier_val}/{rule_id})且近期人工通过率 "
                f"{rate:.0%}(n={samples});如误代批可事后撤回并关闭代批权。"
            )
        )
        logger.info(
            "[SILIJIAN] proposed auto-approval for memorial %s (rule=%s, tier=T%s)",
            memorial_id,
            rule_id,
            tier_val,
        )
        return proposal

    def _estop_engaged(self) -> bool:
        if self._estop is None:
            return False
        try:
            return bool(self._estop.status().engaged)
        except Exception:  # noqa: BLE001
            return True  # 读不到状态 → fail-closed,不敢代批

    def _recent_human_approval_rate(self, window: int = 50) -> tuple[float, int]:
        """近期**人工** decree 的批准率与样本量——代批以外的信号,避免自我强化。"""
        try:
            decrees = self._storage.list_recent_decrees(window)
        except Exception:  # noqa: BLE001
            return 0.0, 0
        human = [
            d
            for d in decrees
            if (actor := getattr(d, "actor", "")) == "human"
            or actor.startswith(("user:", "feishu:", "telegram:"))
        ]
        decisive = [
            d for d in human if getattr(d, "action", None) in ("approve", "reject", "guide")
        ]
        if not decisive:
            return 0.0, 0
        approved = sum(1 for d in decisive if d.action == "approve")
        return approved / len(decisive), len(decisive)
