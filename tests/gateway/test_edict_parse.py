"""edict_parse 纯函数测试 + parse 端点测试。"""

from __future__ import annotations

import json

import pytest

from tianshu.gateway.edict_parse import (
    ParseEdictError,
    build_parse_messages,
    coerce_draft,
    parse_llm_json,
)


@pytest.mark.unit
def test_build_parse_messages_shape():
    msgs = build_parse_messages("每天早上9点推送天气")
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "每天早上9点推送天气"}


@pytest.mark.unit
def test_parse_llm_json_plain():
    assert parse_llm_json('{"goal":"x"}') == {"goal": "x"}


@pytest.mark.unit
def test_parse_llm_json_fenced():
    out = parse_llm_json('```json\n{"goal":"x"}\n```')
    assert out == {"goal": "x"}


@pytest.mark.unit
def test_parse_llm_json_with_noise():
    out = parse_llm_json('好的，结果如下：{"goal":"x","priority":"low"} 完毕')
    assert out["goal"] == "x"


@pytest.mark.unit
def test_parse_llm_json_bad_raises():
    with pytest.raises(ParseEdictError):
        parse_llm_json("这不是 JSON")


@pytest.mark.unit
def test_coerce_cron():
    raw = {"goal": "推送天气", "schedule": {"type": "cron", "cron": "0 18 * * *"}, "notes": "每天18点"}
    draft, notes = coerce_draft(raw)
    assert draft["goal"] == "推送天气"
    assert draft["schedule"]["type"] == "cron"
    assert draft["schedule"]["cron"] == "0 18 * * *"
    assert draft["schedule"]["timezone"] == "Asia/Shanghai"
    assert "每天18点" in notes


@pytest.mark.unit
def test_coerce_once():
    raw = {"goal": "提醒", "schedule": {"type": "once", "at": "2026-05-21 14:30:00"}}
    draft, _ = coerce_draft(raw)
    assert draft["schedule"]["type"] == "once"
    assert draft["schedule"]["at"] == "2026-05-21 14:30:00"


@pytest.mark.unit
def test_coerce_drops_invalid_cron():
    raw = {"goal": "x", "schedule": {"type": "cron", "cron": "not a cron"}}
    draft, notes = coerce_draft(raw)
    assert "schedule" not in draft
    assert "未识别" in notes


@pytest.mark.unit
def test_coerce_drops_invalid_priority():
    raw = {"goal": "x", "priority": "超级紧急"}
    draft, _ = coerce_draft(raw)
    assert "priority" not in draft
    assert draft["goal"] == "x"
