"""巡按御史（Xunan）—— 主动巡检系统体征,异常则产出巡按奏报(迭代 7「制度补全」)。

明制巡按御史"代天子巡狩",不待事发而主动按行地方、纠察不法。天枢的巡按由调用方定期排程,
把 zeroclaw 的 heartbeat + doctor 思路落位为几道**只读**巡查项,基于 storage 既有查询:

- **backlog(任务积压)**:待执行(submitted)+执行中(running)的 memorial 堆积超阈;
- **failure_rate(失败率偏高)**:近窗口内已结束任务(完成+失败)的失败占比超阈;
- **cost_spike(成本陡增)**:今日成本相对周均日耗异常放大;
- **stale_tasks(僵死任务)**:活跃态却长时间无心跳的孤儿任务堆积(心跳存活判定)。

巡按只**产出奏报、不作处置**(纯只读:不发通知、不落库)——是否推送、如何处置由调用方决定,
便于测试与复用。每道巡查项独立容错:单项查询失败仅记 debug 并跳过,不牵连其它项。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _finding(kind: str, severity: str, detail: str, value: Any) -> dict:
    """构造一条巡按发现(新字典,不改动入参)。severity ∈ {"warn", "alert"}。"""
    return {"kind": kind, "severity": severity, "detail": detail, "value": value}


class Xunan:
    """巡按御史:主动巡检器。阈值经构造注入(出厂默认),patrol() 汇总只读奏报。"""

    def __init__(
        self,
        storage: Any,
        *,
        backlog_threshold: int = 20,
        failure_rate_threshold: float = 0.5,
        failure_min_samples: int = 5,
        window_hours: int = 24,
        cost_spike_ratio: float = 3.0,
        cost_floor_cny: float = 1.0,
        stale_idle_seconds: int = 1800,
        stale_threshold: int = 5,
    ) -> None:
        self._storage = storage
        self._backlog_threshold = backlog_threshold
        self._failure_rate_threshold = failure_rate_threshold
        self._failure_min_samples = failure_min_samples
        self._window_hours = window_hours
        self._cost_spike_ratio = cost_spike_ratio
        self._cost_floor_cny = cost_floor_cny
        self._stale_idle_seconds = stale_idle_seconds
        self._stale_threshold = stale_threshold

    def patrol(self) -> dict:
        """依次跑各巡查项,汇总巡按奏报。

        返回 ``{"findings": [...], "healthy": bool}``:无异常时 findings=[] 且 healthy=True。
        healthy 仅由 alert 级发现决定(warn 属提醒,不判定为不健康)。单项失败安全:某巡查项
        查询抛错时记 debug 并跳过,不影响其它项产出,故 patrol 本身不会因单项异常而崩。
        """
        checks = (
            ("backlog", self._check_backlog),
            ("failure_rate", self._check_failure_rate),
            ("cost_spike", self._check_cost_spike),
            ("stale_tasks", self._check_stale_tasks),
        )
        findings: list[dict] = []
        for kind, check in checks:
            try:
                finding = check()
            except Exception:  # noqa: BLE001 —— 单项失败安全,不牵连其它巡查项
                logger.debug("[XUNAN] 巡查项 %s 查询失败,跳过", kind, exc_info=True)
                continue
            if finding is not None:
                findings.append(finding)
        healthy = not any(f["severity"] == "alert" for f in findings)
        return {"findings": findings, "healthy": healthy}

    def _check_backlog(self) -> dict | None:
        """任务积压:待执行(submitted)+执行中(running)堆积超阈 → alert。"""
        pending = self._count_status("submitted") + self._count_status("running")
        if pending <= self._backlog_threshold:
            return None
        return _finding(
            "backlog",
            "alert",
            f"任务积压 {pending} 件(待执行+执行中)超过阈值 {self._backlog_threshold},恐有拥塞",
            pending,
        )

    def _check_failure_rate(self) -> dict | None:
        """失败率:近窗口内已结束任务(完成+失败)的失败占比超阈 → alert。"""
        since = (datetime.now(UTC) - timedelta(hours=self._window_hours)).isoformat()
        stats = self._storage.report_window_stats(since)
        failed = int(stats.get("failed", 0))
        finished = int(stats.get("completed", 0)) + failed
        if finished < self._failure_min_samples:  # 样本不足,不下结论以免噪声误报
            return None
        rate = failed / finished
        if rate <= self._failure_rate_threshold:
            return None
        return _finding(
            "failure_rate",
            "alert",
            f"近 {self._window_hours}h 失败率 {rate:.0%}({failed}/{finished})"
            f"超过阈值 {self._failure_rate_threshold:.0%}",
            round(rate, 3),
        )

    def _check_cost_spike(self) -> dict | None:
        """成本陡增:今日成本相对周均日耗放大;轻度 warn、重度(≥1.5 倍阈值)alert。"""
        day = float(self._storage.get_cost_summary(period="day")["total_cost_cny"])
        week = float(self._storage.get_cost_summary(period="week")["total_cost_cny"])
        baseline = week / 7.0  # 周均日耗
        if day < self._cost_floor_cny or baseline <= 0:  # 低于地板价不评估,规避小额噪声
            return None
        ratio = day / baseline
        if ratio < self._cost_spike_ratio:
            return None
        severity = "alert" if ratio >= self._cost_spike_ratio * 1.5 else "warn"
        return _finding(
            "cost_spike",
            severity,
            f"今日成本 ¥{day:.2f} 达周均日耗 ¥{baseline:.2f} 的 {ratio:.1f} 倍,疑似成本陡增",
            round(ratio, 2),
        )

    def _check_stale_tasks(self) -> dict | None:
        """僵死任务:活跃态却长时间无心跳的孤儿任务堆积超阈 → alert。"""
        stale = self._storage.list_stale_memorials(self._stale_idle_seconds)
        count = len(stale)
        if count < self._stale_threshold:
            return None
        minutes = self._stale_idle_seconds // 60
        return _finding(
            "stale_tasks",
            "alert",
            f"{count} 个任务超过 {minutes} 分钟无心跳(疑似僵死/孤儿),建议排查",
            count,
        )

    def _count_status(self, status: str) -> int:
        """某状态的 memorial 总数(仅取计数,limit=1 不载全量行)。"""
        _rows, total = self._storage.list_memorials(status=status, limit=1)
        return int(total)
