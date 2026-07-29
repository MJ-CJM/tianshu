"""内阁派官（persona/dispatcher.py）。"""

from __future__ import annotations

from types import SimpleNamespace

from tianshu.llm import LLMResponse
from tianshu.models import Edict, UsageSummary
from tianshu.persona.dispatcher import CabinetDispatcher


class _FakeLoader:
    def __init__(self, personas):
        self._personas = {p.id: p for p in personas}


class _FakeClient:
    def __init__(self, content):
        self._content = content

    async def chat(self, messages, tools=None):
        return LLMResponse(content=self._content, tool_calls=None, usage=UsageSummary())


class _FakePM:
    def __init__(self, content):
        self._content = content
        self.calls = []

    def get_client_for_slot(self, slot, **kwargs):
        self.calls.append((slot, kwargs))
        return _FakeClient(self._content)


def _roster():
    return [
        SimpleNamespace(id="bingbu", name="兵部尚书", department="bingbu", title="尚书"),
        SimpleNamespace(id="smg", name="司马光", department="wenyuan", title="历史学者"),
    ]


async def test_dispatch_picks_roster_persona():
    pm = _FakePM('{"persona_id": "smg", "reason": "历史问题宜由学者办理"}')
    dispatcher = CabinetDispatcher(_FakeLoader(_roster()), pm)
    result = await dispatcher.dispatch(Edict(goal="司马光是谁？"))
    assert result == ("smg", "历史问题宜由学者办理")
    slot, kwargs = pm.calls[0]
    assert slot == "edict_parse"


async def test_dispatch_rejects_unknown_persona():
    pm = _FakePM('{"persona_id": "nonexistent", "reason": "x"}')
    dispatcher = CabinetDispatcher(_FakeLoader(_roster()), pm)
    assert await dispatcher.dispatch(Edict(goal="hi")) is None


async def test_dispatch_tolerates_markdown_wrapped_json():
    pm = _FakePM('```json\n{"persona_id": "bingbu", "reason": "通用执行"}\n```')
    dispatcher = CabinetDispatcher(_FakeLoader(_roster()), pm)
    assert await dispatcher.dispatch(Edict(goal="跑个脚本")) == ("bingbu", "通用执行")


async def test_dispatch_skips_single_persona_roster():
    pm = _FakePM('{"persona_id": "bingbu"}')
    dispatcher = CabinetDispatcher(_FakeLoader(_roster()[:1]), pm)
    assert await dispatcher.dispatch(Edict(goal="hi")) is None
    assert pm.calls == []  # 名册只有一人，不花 LLM 调用


async def test_dispatch_failure_returns_none():
    class _BoomPM:
        def get_client_for_slot(self, slot, **kwargs):
            raise RuntimeError("no client")

    dispatcher = CabinetDispatcher(_FakeLoader(_roster()), _BoomPM())
    assert await dispatcher.dispatch(Edict(goal="hi")) is None
