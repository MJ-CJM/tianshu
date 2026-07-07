"""Tests for ConsultationSession — multi-persona opinion collection + synthesis.

LLM 用 fake（非 mock 库）：FakeProviderManager.get_client() 返回 FakeLLM，按 system
prompt 区分"persona 意见"与"会诊综合"两种调用，避免依赖 asyncio.gather 的调度顺序。

注意（项目记忆 project_consultation_confidence_placeholder）：
ConsultationSession._get_opinion 里 confidence=0.8 是硬编码占位值，不是真汇聚结果。
本文件按现状断言 confidence == 0.8，不"修正"它。
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
        # 现状断言：confidence 是 session.py 里硬编码的占位值 0.8，不是真汇聚结果
        assert all(o.confidence == 0.8 for o in resp.opinions)
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

    async def test_no_matching_personas_completes_without_synthesis(self, config_manager):
        loader = _FakePersonaLoader(personas=_personas())
        llm = _FakeLLM()
        session = ConsultationSession(
            persona_loader=loader,
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(llm),
        )
        req = ConsultationRequest(topic="无人应答的议题", persona_ids=["nobody"])

        resp = await session.start(req)

        assert resp.status == "completed"
        assert resp.opinions == []
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
