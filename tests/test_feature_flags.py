"""feature-flag 灰度(迭代 6「演化 2.0」)——deny-by-default + 哈希百分比灰度。"""

from __future__ import annotations

import pytest

from tianshu.feature_flags import FeatureFlags, _bucket


@pytest.fixture
def flags(storage):
    return FeatureFlags(storage)


class TestDenyByDefault:
    def test_unknown_flag_is_off(self, flags):
        assert flags.is_enabled("nonexistent") is False

    def test_disabled_flag_is_off(self, flags):
        flags.set("f", enabled=False, rollout_pct=100)
        assert flags.is_enabled("f") is False


class TestRollout:
    def test_full_rollout_always_on(self, flags):
        flags.set("f", enabled=True, rollout_pct=100)
        assert all(flags.is_enabled("f", f"subject-{i}") for i in range(20))

    def test_zero_rollout_always_off(self, flags):
        flags.set("f", enabled=True, rollout_pct=0)
        assert not any(flags.is_enabled("f", f"subject-{i}") for i in range(20))

    def test_partial_rollout_splits_cohort(self, flags):
        flags.set("f", enabled=True, rollout_pct=50)
        on = sum(flags.is_enabled("f", f"user-{i}") for i in range(200))
        # 50% ± 合理波动(哈希均匀)
        assert 70 < on < 130

    def test_stable_per_subject(self, flags):
        flags.set("f", enabled=True, rollout_pct=50)
        first = flags.is_enabled("f", "alice")
        assert all(flags.is_enabled("f", "alice") == first for _ in range(5))

    def test_rollout_clamped(self, flags):
        flags.set("f", enabled=True, rollout_pct=999)
        assert flags.list_all()[0]["rollout_pct"] == 100
        flags.set("g", enabled=True, rollout_pct=-5)
        assert next(x for x in flags.list_all() if x["key"] == "g")["rollout_pct"] == 0


class TestBucketIndependence:
    def test_different_flags_independent_distribution(self):
        # 同 subject 在不同 flag 上分桶独立(key 混入哈希),避免所有 flag 同进同出
        b1 = [_bucket("flag-a", f"u{i}") for i in range(50)]
        b2 = [_bucket("flag-b", f"u{i}") for i in range(50)]
        assert b1 != b2


class TestCrud:
    def test_set_list_delete(self, flags):
        flags.set("f", enabled=True, rollout_pct=30, description="灰度中")
        row = next(x for x in flags.list_all() if x["key"] == "f")
        assert row["enabled"] == 1 and row["rollout_pct"] == 30 and row["description"] == "灰度中"
        flags.delete("f")
        assert flags.is_enabled("f") is False
