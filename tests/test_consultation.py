"""Tests for ConsultationSession — multi-persona opinion collection + synthesis.

LLM 用 fake（非 mock 库）：FakeProviderManager.get_client() 返回 FakeLLM，按 system
prompt 区分"persona 意见"与"会诊综合"两种调用，避免依赖 asyncio.gather 的调度顺序。

ADR-0008 已落地（迭代 5 廷议 2.0）：废硬编码 confidence=0.8 换结构化 stance
（赞成/反对/有条件 + 条件 + 论据），并加言官强制反调破六官同构的意见趋同。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from tianshu.consultation.models import ConsultationRequest, RoundRequest
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
        if "synthesizing multi-perspective analysis" in system:
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
        # 言官改为显式任命（issue #55）：此前是 idx==0 硬编码，谁执异取决于列表顺序
        req = ConsultationRequest(topic="是否批准新预算", censor_persona_ids=["ducha"])

        resp = await session.start(req)

        assert resp.status == "completed"
        assert {o.persona_id for o in resp.opinions} == {"neige", "ducha", "hubu"}
        # ADR-0008：废 confidence 换结构化 stance
        assert all(o.stance in ("support", "oppose", "conditional") for o in resp.opinions)
        assert [o.persona_id for o in resp.opinions if o.is_censor] == ["ducha"]
        assert resp.synthesis == "各部意见一致，均建议推进。"
        # LLM 的产出降格为票拟（内阁建议），裁决权归用户（issue #55）
        assert resp.proposal == "批准执行。"
        assert resp.verdict is None
        assert resp.completed_at is not None

    async def test_no_censor_unless_named(self, config_manager):
        """不点名就无人执异——显式优于隐式（issue #55）。"""
        session = ConsultationSession(
            persona_loader=_FakePersonaLoader(personas=_personas()),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(_FakeLLM()),
        )

        resp = await session.start(ConsultationRequest(topic="是否批准新预算"))

        assert [o.persona_id for o in resp.opinions if o.is_censor] == []

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
        assert resp.proposal is None
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

    def test_opinion_body_on_following_lines_is_kept(self):
        """OPINION: 换行写正文时不得丢失（issue #54）。

        旧实现只取冒号后同一行，LLM 一换行 opinion 就是空串，前端渲染出一张
        只有标签、没有正文的空卡片。
        """
        from tianshu.consultation.session import ConsultationSession

        stance, conditions, opinion = ConsultationSession._parse_opinion(
            "STANCE: conditional\n"
            "CONDITIONS: 需划定人机边界; 保留最终决策权\n"
            "OPINION:\n"
            "吾乃张居正。夫AI者，术也非道也。\n"
            "第一，降本增效不可不用。\n"
            "第二，决策权不可外包。"
        )
        assert stance == "conditional"
        assert conditions == ["需划定人机边界", "保留最终决策权"]
        assert opinion.startswith("吾乃张居正")
        assert "第二，决策权不可外包。" in opinion

    def test_multiline_opinion_after_marker_is_not_truncated(self):
        """OPINION: 同行起笔、后续续写时，后面的段落同样不得丢（issue #54）。"""
        from tianshu.consultation.session import ConsultationSession

        _s, _c, opinion = ConsultationSession._parse_opinion(
            "STANCE: support\nOPINION: 首段结论。\n\n展开论据一。\n展开论据二。"
        )
        assert opinion.startswith("首段结论。")
        assert "展开论据二。" in opinion

    def test_conditions_spanning_lines_are_collected(self):
        """CONDITIONS 跨行书写时同样要收全（issue #54）。"""
        from tianshu.consultation.session import ConsultationSession

        _s, conditions, opinion = ConsultationSession._parse_opinion(
            "STANCE: conditional\nCONDITIONS:\n先做预算评估;\n再灰度放量\nOPINION: 谨慎推进"
        )
        assert conditions == ["先做预算评估", "再灰度放量"]
        assert opinion == "谨慎推进"

    def test_marker_without_body_falls_back_to_full_text(self):
        """只给了标记却没正文时回落全文，宁可多余也不要空白卡片（issue #54）。"""
        from tianshu.consultation.session import ConsultationSession

        _s, _c, opinion = ConsultationSession._parse_opinion("STANCE: support\nOPINION:")
        assert opinion


class TestMultiRound:
    """多轮朝议：追问、@点名、历史回放、裁决（issue #55）。"""

    async def test_follow_up_round_only_asks_named_personas(self, config_manager):
        session = ConsultationSession(
            persona_loader=_FakePersonaLoader(personas=_personas()),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(_FakeLLM()),
        )
        resp = await session.start(ConsultationRequest(topic="是否批准新预算"))

        session.append_round(
            resp.id, RoundRequest(prompt="户部单独说说钱", participant_ids=["hubu"])
        )
        after = await session.run(resp.id)

        assert len(after.rounds) == 2
        assert [o.persona_id for o in after.rounds[1].opinions] == ["hubu"]
        # 首轮记录不受影响
        assert len(after.rounds[0].opinions) == 3

    async def test_follow_up_without_names_asks_everyone(self, config_manager):
        session = ConsultationSession(
            persona_loader=_FakePersonaLoader(personas=_personas()),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(_FakeLLM()),
        )
        resp = await session.start(ConsultationRequest(topic="是否批准新预算"))

        session.append_round(resp.id, RoundRequest(prompt="再议"))
        after = await session.run(resp.id)

        assert {o.persona_id for o in after.rounds[1].opinions} == {"neige", "ducha", "hubu"}

    async def test_follow_up_carries_prior_rounds_into_the_prompt(self, config_manager):
        llm = _FakeLLM()
        session = ConsultationSession(
            persona_loader=_FakePersonaLoader(personas=_personas()),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(llm),
        )
        resp = await session.start(ConsultationRequest(topic="是否批准新预算"))
        session.set_verdict(resp.id, "准奏，但须季度复核。")
        llm.calls.clear()

        session.append_round(
            resp.id, RoundRequest(prompt="复核频次是否够？", participant_ids=["hubu"])
        )
        await session.run(resp.id)

        prompt = llm.calls[0][1]["content"]
        assert "此前廷议记录" in prompt
        assert "第 1 轮：是否批准新预算" in prompt
        assert "户部 的意见：建议推进。" in prompt  # 上一轮的原话被回放
        assert "【票拟】批准执行。" in prompt
        assert "准奏，但须季度复核。" in prompt  # 用户裁决进入后续上下文
        assert "本轮追问\n复核频次是否够？" in prompt

    async def test_first_round_has_no_history_section(self, config_manager):
        llm = _FakeLLM()
        session = ConsultationSession(
            persona_loader=_FakePersonaLoader(personas=_personas()),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(llm),
        )

        await session.start(ConsultationRequest(topic="是否批准新预算", persona_ids=["hubu"]))

        assert "此前廷议记录" not in llm.calls[0][1]["content"]

    async def test_cannot_append_while_a_round_is_running(self, config_manager):
        import pytest

        session = ConsultationSession(
            persona_loader=_FakePersonaLoader(personas=_personas()),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(_FakeLLM()),
        )
        resp = session.create_pending(ConsultationRequest(topic="是否批准新预算"))

        with pytest.raises(ValueError, match="still in progress"):
            session.append_round(resp.id, RoundRequest(prompt="抢跑"))

    async def test_verdict_is_recorded_with_timestamp(self, config_manager):
        session = ConsultationSession(
            persona_loader=_FakePersonaLoader(personas=_personas()),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(_FakeLLM()),
        )
        resp = await session.start(ConsultationRequest(topic="是否批准新预算"))

        updated = session.set_verdict(resp.id, "准奏。")

        assert updated.verdict == "准奏。"
        assert updated.verdict_at is not None

    async def test_history_budget_keeps_the_most_recent_round(self, config_manager):
        """预算极小时也必须保住最近一轮，否则追问失去上下文。"""
        llm = _FakeLLM()
        session = ConsultationSession(
            persona_loader=_FakePersonaLoader(personas=_personas()),
            config_manager=config_manager,
            provider_manager=_FakeProviderManager(llm),
            history_max_chars=1,
        )
        resp = await session.start(
            ConsultationRequest(topic="是否批准新预算", persona_ids=["hubu"])
        )
        session.append_round(resp.id, RoundRequest(prompt="二轮", participant_ids=["hubu"]))
        await session.run(resp.id)
        llm.calls.clear()

        session.append_round(resp.id, RoundRequest(prompt="三轮", participant_ids=["hubu"]))
        await session.run(resp.id)

        prompt = llm.calls[0][1]["content"]
        assert "第 2 轮：二轮" in prompt  # 最近一轮必留
        assert "第 1 轮：是否批准新预算" not in prompt  # 超预算的更早轮次被截掉
