"""迭代 6「演化 2.0」evolver 接线:feature-flag 晋升灰度(B)/ 画像驱动变异(D)/ 晋升廷议门(E)。"""

from __future__ import annotations

from dataclasses import dataclass, field

from tianshu.feature_flags import FeatureFlags
from tianshu.universe.evolver import UniverseEvolver


@dataclass
class _FakeOpinion:
    persona_name: str
    stance: str
    opinion: str


@dataclass
class _FakeResp:
    synthesis: str
    opinions: list = field(default_factory=list)


class _FakeConsultation:
    def __init__(self):
        self.started_with = None

    async def start(self, request):
        self.started_with = request
        return _FakeResp(
            synthesis="综合意见:建议晋升,但需观察成本。",
            opinions=[_FakeOpinion("户部", "conditional", "成本可控则支持")],
        )


def _evolver(storage, *, flags=None, consultation=None, profile_provider=None):
    return UniverseEvolver(
        llm_client=None,
        manager=None,
        storage=storage,
        config_manager=None,
        feature_flags=flags,
        consultation=consultation,
        profile_provider=profile_provider,
    )


class TestPromotionFlag:
    def test_registers_disabled_zero_pct(self, storage):
        flags = FeatureFlags(storage)
        ev = _evolver(storage, flags=flags)
        key = ev._register_promotion_flag("u123", 0.12)
        assert key == "universe:promote:u123"
        assert flags.is_enabled(key) is False  # 默认关,操作者手动放量
        row = next(x for x in flags.list_all() if x["key"] == key)
        assert row["rollout_pct"] == 0

    def test_no_flags_returns_none(self, storage):
        assert _evolver(storage)._register_promotion_flag("u1", 0.1) is None


class TestDeliberationGate:
    async def test_payload_has_flag_and_deliberation(self, storage):
        flags = FeatureFlags(storage)
        consult = _FakeConsultation()
        ev = _evolver(storage, flags=flags, consultation=consult)
        payload = await ev._on_promotion_recommended(
            {"id": "u9", "name": "候选甲", "mutation_reason": "更简洁"}, 0.2, 25
        )
        assert payload["flag_key"] == "universe:promote:u9"
        assert payload["deliberation"]["synthesis"].startswith("综合意见")
        assert payload["deliberation"]["opinions"][0]["stance"] == "conditional"
        assert "位面晋升评议" in consult.started_with.topic

    async def test_no_consultation_omits_deliberation(self, storage):
        ev = _evolver(storage, flags=FeatureFlags(storage))
        payload = await ev._on_promotion_recommended({"id": "u9"}, 0.2, 25)
        assert "deliberation" not in payload
        assert payload["flag_key"] == "universe:promote:u9"


class TestProfileDrivenMutation:
    def test_profile_injected_and_truncated(self, storage):
        ev = _evolver(storage, profile_provider=lambda: "偏好简洁直接" * 500)
        out = ev._user_profile(limit=50)
        assert out.endswith("…") and len(out) <= 51

    def test_missing_provider_placeholder(self, storage):
        assert _evolver(storage)._user_profile() == "(暂无画像)"

    def test_provider_failure_is_safe(self, storage):
        def _boom():
            raise RuntimeError("profile read failed")

        assert _evolver(storage, profile_provider=_boom)._user_profile() == "(暂无画像)"
