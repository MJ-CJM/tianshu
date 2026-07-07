"""位面适应度：隐式指标 + 显式反馈 → 综合分（越高越好）。"""

from __future__ import annotations


def _safe_ratio(num: int, den: int) -> float:
    return num / den if den else 0.0


def compute_fitness(
    stats: dict,
    *,
    weights: tuple[float, ...] = (0.4, 0.15, 0.2, 0.1, 0.15),
) -> dict:
    """weights = (success, cost, audit, retry, feedback)。各分项归一到 [0,1] 后加权。

    cost / retry 为"越低越好"，取反向分；feedback 为累积整数，squash 到 [0,1]。
    """
    w_succ, w_cost, w_audit, w_retry, w_fb = weights
    total = stats.get("total", 0)
    success_rate = _safe_ratio(stats.get("success", 0), total)
    audit_rate = _safe_ratio(stats.get("audit_pass", 0), stats.get("audited", 0))
    avg_retries = _safe_ratio(stats.get("retries", 0), total)
    retry_score = max(0.0, 1.0 - avg_retries / 2.0)
    avg_cost = _safe_ratio(int(stats.get("cost", 0) * 1000), total)
    cost_score = 1.0 / (1.0 + avg_cost) if avg_cost > 0 else 1.0
    fb = float(stats.get("feedback", 0))
    fb_norm = 0.5 + 0.5 * (fb / (1.0 + abs(fb)))

    score = (
        w_succ * success_rate
        + w_cost * cost_score
        + w_audit * audit_rate
        + w_retry * retry_score
        + w_fb * fb_norm
    )
    return {
        "score": round(score, 4),
        "samples": total,
        "success_rate": round(success_rate, 4),
        "audit_rate": round(audit_rate, 4),
        "retry_score": round(retry_score, 4),
        "cost_score": round(cost_score, 4),
        "feedback": fb,
    }
