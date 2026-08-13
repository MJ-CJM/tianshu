"""Tests for ConsultationSession — multi-persona opinion collection + synthesis.

LLM 用 fake（非 mock 库）：FakeProviderManager.get_client() 返回 FakeLLM，按 system
prompt 区分"persona 意见"与"会诊综合"两种调用，避免依赖 asyncio.gather 的调度顺序。

ADR-0008 已落地（迭代 5 廷议 2.0）：废硬编码 confidence=0.8 换结构化 stance
（赞成/反对/有条件 + 条件 + 论据），并加言官强制反调破六官同构的意见趋同。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from tianshu.consultation.models import ConsultationRequest
from tianshu.consultation.session import ConsultationSession


@dataclass
class _FakePersona:
    id: str
    name: str
    department: str


@dataclass
class _FakePersonaLoader:
    """duck-typed PersonaLoader fake：只实现 ConsultationSession 用到的 2 个方法。"""

    personas: dict[str, _FakePersona] = field(default_factory=dict)

    def load_all(self) -> dict[str, _FakePersona]:
        return dict(self.personas)

    def get(self, persona_id: str) -> _FakePersona | None:
        return self.personas.get(persona_id)


class _FakeLLM:
    """按 system prompt 区分 persona 意见 vs 会诊综合两种调用；记录调用供断言。"""

    def __init__(self, fail_for_persona: str | None = None) -> None:
        self.calls: list[list[dict]] = []
        self._fail_for_persona = fail_for_persona

    async def chat(self, messages: list[dict]) -> SimpleNamespace:
        self.calls.append(messages)
        system = messages[0]["content"]
        if "senior advisor synthesizing" in system:
            return SimpleNamespace(
                content="### Synthesis\n各部意见一致，均建议推进。\n\n### Decision\n批准执行。"
            )
        # persona 意见调用：system = f"You are {persona.name}, {persona.department}."
        name = system.removeprefix("You are ").split(",")[0]
        if self._fail_for_persona and name == self._fail_for_persona:
            raise RuntimeError(f"{name} LLM 调用失败（模拟下游故障）")
        return SimpleNamespace(content=f"{name} 的意见：建议推进。")


class _FakeProviderManager:
    def __init__(self, llm: _FakeLLM) -> None:
        self._llm = llm

    def get_client(self) -> _FakeLLM:
        return self._llm


class _RaisingPersonaLoader:
    """persona_loader.get 直接抛异常 —— 触发 start() 外层 try/except 的失败路径。"""

    def load_all(self) -> dict[str, _FakePersona]:
        return {"neige": _FakePersona(id="neige", name="内阁", department="内阁")}

    def get(self, persona_id: str) -> _FakePersona:
        raise RuntimeError("persona store unavailable")


def _personas() -> dict[str, _FakePersona]:
    return {
        "neige": _FakePersona(id="neige", name="内阁", department="内阁"),
        "ducha": _FakePersona(id="ducha", name="都察", department="都察院"),
        "hubu": _FakePersona(id="hubu", name="户部", department="户部"),
    }


class TestConsultationSessionStart:
    async def test_all_personas_collected_and_synthesized(self, config_manager):
        loader = _FakePersonaLoader(personas=_personas())
        llm = _FakeLLM()
        session = ConsultationSession(
            persona_loader=loader,
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(llm),
        )
        req = ConsultationRequest(topic="是否批准新预算")

        resp = await session.start(req)

        assert resp.status == "completed"
        assert {o.persona_id for o in resp.opinions} == {"neige", "ducha", "hubu"}
        # ADR-0008：废 confidence 换结构化 stance；恰一位官员为言官强制反调
        assert all(o.stance in ("support", "oppose", "conditional") for o in resp.opinions)
        assert sum(1 for o in resp.opinions if o.is_censor) == 1
        assert resp.synthesis == "各部意见一致，均建议推进。"
        assert resp.decision == "批准执行。"
        assert resp.completed_at is not None

    async def test_filters_to_requested_persona_ids_and_skips_unknown(self, config_manager):
        loader = _FakePersonaLoader(personas=_personas())
        llm = _FakeLLM()
        session = ConsultationSession(
            persona_loader=loader,
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(llm),
        )
        req = ConsultationRequest(
            topic="是否批准新预算",
            persona_ids=["neige", "unknown_persona"],
        )

        resp = await session.start(req)

        assert resp.status == "completed"
        assert {o.persona_id for o in resp.opinions} == {"neige"}

    async def test_partial_llm_failure_still_completes_with_remaining_opinions(
        self, config_manager
    ):
        loader = _FakePersonaLoader(personas=_personas())
        llm = _FakeLLM(fail_for_persona="都察")  # ducha 的 LLM 调用会抛异常
        session = ConsultationSession(
            persona_loader=loader,
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(llm),
        )
        req = ConsultationRequest(topic="是否批准新预算")

        resp = await session.start(req)

        # asyncio.gather(..., return_exceptions=True) 吞掉单个 persona 的异常，
        # 整体仍 completed，只是该 persona 的意见缺席
        assert resp.status == "completed"
        assert {o.persona_id for o in resp.opinions} == {"neige", "hubu"}
        assert resp.synthesis  # 剩余 2 条意见仍能正常汇总

    async def test_no_matching_personas_marks_failed(self, config_manager):
        """无人应答必须判 failed（issue #52）。

        旧行为是空 opinions 仍报 completed，前端 completed 分支渲染出一片空白、
        连报错都没有——用户侧表现为"发起廷议后看不到任何反馈"。
        """
        loader = _FakePersonaLoader(personas=_personas())
        llm = _FakeLLM()
        session = ConsultationSession(
            persona_loader=loader,
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(llm),
        )
        req = ConsultationRequest(topic="无人应答的议题", persona_ids=["nobody"])

        resp = await session.start(req)

        assert resp.status == "failed"
        assert resp.opinions == []
        assert resp.error  # 必须带归因，否则前端无从解释失败
        assert resp.synthesis is None
        assert resp.decision is None
        assert llm.calls == []  # 没有任何 persona 参与，不应发起 LLM 调用

    async def test_unexpected_exception_before_gather_marks_failed(self, config_manager):
        session = ConsultationSession(
            persona_loader=_RaisingPersonaLoader(),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(_FakeLLM()),
        )
        req = ConsultationRequest(topic="触发异常路径", persona_ids=["neige"])

        resp = await session.start(req)

        assert resp.status == "failed"
        assert resp.completed_at is not None


class TestConsultationSessionGet:
    async def test_get_returns_stored_session_after_start(self, config_manager):
        loader = _FakePersonaLoader(personas=_personas())
        session = ConsultationSession(
            persona_loader=loader,
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(_FakeLLM()),
        )
        req = ConsultationRequest(topic="议题", persona_ids=["neige"])

        resp = await session.start(req)

        assert session.get(resp.id) is resp

    def test_get_returns_none_for_unknown_id(self, config_manager):
        loader = _FakePersonaLoader(personas=_personas())
        session = ConsultationSession(persona_loader=loader, config_manager=config_manager)
        assert session.get("no-such-id") is None


class TestStanceParsing:
    """ADR-0008：结构化 stance 解析(废 confidence)。"""

    def test_parse_oppose_with_conditions(self):
        from tianshu.consultation.session import ConsultationSession

        stance, conditions, opinion = ConsultationSession._parse_opinion(
            "STANCE: conditional\nCONDITIONS: 需先做预算评估; 需灰度\nOPINION: 谨慎推进"
        )
        assert stance == "conditional"
        assert conditions == ["需先做预算评估", "需灰度"]
        assert opinion == "谨慎推进"

    def test_parse_oppose(self):
        from tianshu.consultation.session import ConsultationSession

        stance, _c, _o = ConsultationSession._parse_opinion("STANCE: oppose\nOPINION: 风险过高")
        assert stance == "oppose"

    def test_parse_defaults_support_when_unformatted(self):
        from tianshu.consultation.session import ConsultationSession

        stance, conditions, opinion = ConsultationSession._parse_opinion("我觉得可以推进")
        assert stance == "support" and conditions == [] and "推进" in opinion
