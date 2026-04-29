"""IntentParser 单元测试：JSON 容错 + 白名单 intent + LLM mock。"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tianshu.gateway.feishu.intent_parser import INTENTS, IntentParser


def test_parse_json_valid():
    r = IntentParser._parse_json('{"intent": "list", "args": {}}')
    assert r == {"intent": "list", "args": {}}


def test_parse_json_with_preamble():
    r = IntentParser._parse_json('解析结果 {"intent": "new", "args": {"goal": "x"}} 完成')
    assert r["intent"] == "new"
    assert r["args"]["goal"] == "x"


def test_parse_json_garbage():
    assert IntentParser._parse_json("garbage") == {"intent": "unknown", "args": {}}


def test_parse_json_intent_not_in_whitelist():
    r = IntentParser._parse_json('{"intent": "drop_table", "args": {}}')
    assert r == {"intent": "unknown", "args": {}}


def test_parse_json_args_not_dict():
    r = IntentParser._parse_json('{"intent": "list", "args": "not-a-dict"}')
    assert r == {"intent": "list", "args": {}}


def test_parse_json_empty():
    assert IntentParser._parse_json("") == {"intent": "unknown", "args": {}}


def test_intents_constant_complete():
    expected = {"new", "list", "status", "cancel", "budget", "help", "unknown"}
    assert set(INTENTS) == expected


@pytest.mark.asyncio
async def test_parse_when_persona_missing_returns_unknown():
    loader = MagicMock()
    loader.get.return_value = None
    parser = IntentParser(
        persona_loader=loader,
        provider_manager=MagicMock(),
        persona_id="missing",
    )
    r = await parser.parse("显示列表")
    assert r == {"intent": "unknown", "args": {}}


@pytest.mark.asyncio
async def test_parse_when_no_llm_config_returns_unknown():
    p = MagicMock()
    p.llm_config_name = None
    loader = MagicMock()
    loader.get.return_value = p
    parser = IntentParser(
        persona_loader=loader,
        provider_manager=MagicMock(),
        persona_id="x",
    )
    r = await parser.parse("hi")
    assert r == {"intent": "unknown", "args": {}}


@pytest.mark.asyncio
async def test_parse_llm_failure_returns_unknown():
    """LLM 调用失败时降级为 unknown。"""
    p = MagicMock()
    p.llm_config_name = "haiku"
    p.name = "通政司"
    loader = MagicMock()
    loader.get.return_value = p
    pm = MagicMock()
    client = MagicMock()
    client.chat = AsyncMock(side_effect=RuntimeError("net err"))
    pm.get_client.return_value = client
    parser = IntentParser(persona_loader=loader, provider_manager=pm, persona_id="x")
    r = await parser.parse("hi")
    assert r == {"intent": "unknown", "args": {}}


@pytest.mark.asyncio
async def test_parse_llm_success_returns_parsed():
    """LLM 返回合法 JSON 时正常解析。"""
    p = MagicMock()
    p.llm_config_name = "haiku"
    p.name = "通政司"
    loader = MagicMock()
    loader.get.return_value = p
    pm = MagicMock()
    client = MagicMock()
    response = MagicMock()
    response.content = '{"intent": "list", "args": {"filter": "open"}}'
    client.chat = AsyncMock(return_value=response)
    pm.get_client.return_value = client
    parser = IntentParser(persona_loader=loader, provider_manager=pm, persona_id="x")
    r = await parser.parse("显示列表")
    assert r["intent"] == "list"
    assert r["args"]["filter"] == "open"


@pytest.mark.asyncio
async def test_parse_get_client_failure_returns_unknown():
    """provider_manager.get_client 抛异常时也降级。"""
    p = MagicMock()
    p.llm_config_name = "haiku"
    p.name = "通政司"
    loader = MagicMock()
    loader.get.return_value = p
    pm = MagicMock()
    pm.get_client.side_effect = RuntimeError("config not found")
    parser = IntentParser(persona_loader=loader, provider_manager=pm, persona_id="x")
    r = await parser.parse("hi")
    assert r == {"intent": "unknown", "args": {}}


def test_set_persona_updates_id():
    parser = IntentParser(
        persona_loader=MagicMock(),
        provider_manager=MagicMock(),
        persona_id="old",
    )
    parser.set_persona("new")
    assert parser._persona_id == "new"
