"""巡按御史·主动巡检(迭代 7「制度补全」)——积压/失败率/成本陡增/僵死任务 + 单项失败安全。"""

from __future__ import annotations

from typing import Any

from tianshu.executor.xunan import Xunan


class _FakeStorage:
    """可控 fake storage:按需返回积压计数/窗口统计/成本/僵死列表,或指定某方法抛异常。"""

    def __init__(
        self,
        *,
        status_counts: dict[str, int] | None = None,
        window_stats: dict[str, int] | None = None,
        cost_by_period: dict[str, float] | None = None,
        stale_count: int = 0,
        fail: set[str] | None = None,
    ) -> None:
        self._status_counts = status_counts or {}
        self._window_stats = window_stats or {"completed": 0, "failed": 0}
        self._cost_by_period = cost_by_period or {}
        self._stale_count = stale_count
        self._fail = fail or set()

    def list_memorials(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[Any], int]:
        if "list_memorials" in self._fail:
            raise RuntimeError("boom: list_memorials")
        return [], self._status_counts.get(status or "", 0)

    def report_window_stats(self, since_iso: str) -> dict:
        if "report_window_stats" in self._fail:
            raise RuntimeError("boom: report_window_stats")
        return dict(self._window_stats)

    def get_cost_summary(self, period: str | None = None, edict_id: str | None = None) -> dict:
        if "get_cost_summary" in self._fail:
            raise RuntimeError("boom: get_cost_summary")
        return {"total_cost_cny": self._cost_by_period.get(period or "", 0.0)}

    def list_stale_memorials(
        self, idle_seconds: int, statuses: tuple[str, ...] = (), limit: int = 100
    ) -> list[Any]:
        if "list_stale_memorials" in self._fail:
            raise RuntimeError("boom: list_stale_memorials")
        return [object()] * self._stale_count


def _kinds(report: dict) -> set[str]:
    return {f["kind"] for f in report["findings"]}


def _finding_of(report: dict, kind: str) -> dict:
    return next(f for f in report["findings"] if f["kind"] == kind)


class TestBacklog:
    def test_over_threshold_alerts(self) -> None:
        # 15 待执行 + 10 执行中 = 25 > 阈值 20
        storage = _FakeStorage(status_counts={"submitted": 15, "running": 10})
        report = Xunan(storage).patrol()
        assert "backlog" in _kinds(report)
        f = _finding_of(report, "backlog")
        assert f["severity"] == "alert"
        assert f["value"] == 25
        assert report["healthy"] is False

    def test_at_or_below_threshold_silent(self) -> None:
        storage = _FakeStorage(status_counts={"submitted": 12, "running": 8})  # =20,不超阈
        report = Xunan(storage).patrol()
        assert "backlog" not in _kinds(report)


class TestFailureRate:
    def test_over_threshold_alerts(self) -> None:
        # 已结束 10 件,失败 8 → 80% > 50%
        storage = _FakeStorage(window_stats={"completed": 2, "failed": 8})
        report = Xunan(storage).patrol()
        f = _finding_of(report, "failure_rate")
        assert f["severity"] == "alert"
        assert f["value"] == 0.8
        assert report["healthy"] is False

    def test_insufficient_samples_silent(self) -> None:
        # 仅 2 件已结束 < min_samples(5),即便全失败也不下结论
        storage = _FakeStorage(window_stats={"completed": 0, "failed": 2})
        report = Xunan(storage).patrol()
        assert "failure_rate" not in _kinds(report)

    def test_rate_below_threshold_silent(self) -> None:
        storage = _FakeStorage(window_stats={"completed": 8, "failed": 2})  # 20% < 50%
        report = Xunan(storage).patrol()
        assert "failure_rate" not in _kinds(report)


class TestCostSpike:
    def test_severe_spike_alerts(self) -> None:
        # 今日 ¥100,周耗 ¥140 → 周均日耗 ¥20,5.0 倍 ≥ 1.5×阈值(4.5) → alert
        storage = _FakeStorage(cost_by_period={"day": 100.0, "week": 140.0})
        report = Xunan(storage).patrol()
        f = _finding_of(report, "cost_spike")
        assert f["severity"] == "alert"
        assert f["value"] == 5.0
        assert report["healthy"] is False

    def test_mild_spike_warns_but_stays_healthy(self) -> None:
        # 今日 ¥70,周均日耗 ¥20 → 3.5 倍:超阈(3.0)但未达 4.5 → warn,不判为不健康
        storage = _FakeStorage(cost_by_period={"day": 70.0, "week": 140.0})
        report = Xunan(storage).patrol()
        f = _finding_of(report, "cost_spike")
        assert f["severity"] == "warn"
        assert report["healthy"] is True

    def test_below_floor_suppressed(self) -> None:
        # 今日仅 ¥0.5 < 地板价 ¥1.0:即便倍率高也不报,规避小额噪声
        storage = _FakeStorage(cost_by_period={"day": 0.5, "week": 0.7})
        report = Xunan(storage).patrol()
        assert "cost_spike" not in _kinds(report)


class TestStaleTasks:
    def test_over_threshold_alerts(self) -> None:
        storage = _FakeStorage(stale_count=6)  # ≥ 阈值 5
        report = Xunan(storage).patrol()
        f = _finding_of(report, "stale_tasks")
        assert f["severity"] == "alert"
        assert f["value"] == 6
        assert report["healthy"] is False


class TestHealthyAndIsolation:
    def test_all_healthy_empty_findings(self) -> None:
        storage = _FakeStorage(
            status_counts={"submitted": 1, "running": 1},
            window_stats={"completed": 9, "failed": 1},
            cost_by_period={"day": 10.0, "week": 140.0},  # 0.5 倍,无陡增
            stale_count=0,
        )
        report = Xunan(storage).patrol()
        assert report["findings"] == []
        assert report["healthy"] is True

    def test_single_check_failure_isolated(self) -> None:
        # report_window_stats 抛异常:该项跳过,而 backlog 与 stale 仍照常产出,patrol 不崩
        storage = _FakeStorage(
            status_counts={"submitted": 30, "running": 0},
            stale_count=8,
            fail={"report_window_stats"},
        )
        report = Xunan(storage).patrol()
        kinds = _kinds(report)
        assert "failure_rate" not in kinds  # 失败项被跳过
        assert {"backlog", "stale_tasks"} <= kinds  # 其它项仍产出
        assert report["healthy"] is False
