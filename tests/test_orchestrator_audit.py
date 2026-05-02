"""Completion audit 数据结构与执行器测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tianshu.executor.orchestrator.audit import (
    AuditGap,
    AuditResult,
    parse_audit_response,
    run_completion_audit,
    format_gaps_for_continuation,
)
from tianshu.models.acceptance import AcceptanceCriteria, CheckSpec


def test_parse_passed_audit():
    raw = '{"passed": true, "gaps": []}'
    result = parse_audit_response(raw)
    assert result.passed is True
    assert result.gaps == ()


def test_parse_audit_with_gaps():
    raw = """{
        "passed": false,
        "gaps": [
            {"check_name": "tests", "requirement": "pytest 全绿",
             "evidence_status": "missing", "suggested_action": "跑测试"}
        ]
    }"""
    result = parse_audit_response(raw)
    assert result.passed is False
    assert len(result.gaps) == 1
    assert result.gaps[0].check_name == "tests"
    assert result.gaps[0].evidence_status == "missing"


def test_parse_invalid_json_returns_failed_with_one_gap():
    """解析失败时不抛，返回 passed=false 并附带提示性 gap。"""
    result = parse_audit_response("not json at all")
    assert result.passed is False
    assert len(result.gaps) == 1
    assert "解析" in result.gaps[0].requirement or "parse" in result.gaps[0].requirement.lower()


def test_parse_extracts_json_from_surrounding_text():
    """LLM 偶尔会在 JSON 外加解释文字，应能容错提取。"""
    raw = '解释一段 ```json\n{"passed": true, "gaps": []}\n```'
    result = parse_audit_response(raw)
    assert result.passed is True


def test_format_gaps_for_continuation_human_readable():
    gaps = (
        AuditGap("tests", "pytest 全绿", "missing", "跑 pytest"),
        AuditGap("docs", "更新 README", "weak", "贴 README diff"),
    )
    text = format_gaps_for_continuation(gaps)
    assert "tests" in text
    assert "missing" in text
    assert "docs" in text
    assert "weak" in text


@pytest.mark.asyncio
async def test_run_completion_audit_invokes_llm_and_returns_result():
    fake_llm = AsyncMock()
    fake_response = AsyncMock()
    fake_response.content = '{"passed": true, "gaps": []}'
    fake_llm.chat.return_value = fake_response

    acceptance = AcceptanceCriteria(checks=[
        CheckSpec(kind="bash", name="tests", command="pytest"),
    ])

    result = await run_completion_audit(
        actor_output="我已经跑了 pytest，全部通过",
        objective="测试覆盖",
        acceptance=acceptance,
        llm=fake_llm,
    )
    assert result.passed is True
    assert fake_llm.chat.called
    # system prompt 必含完成审计要求
    call_args = fake_llm.chat.call_args
    messages = call_args.args[0] if call_args.args else call_args.kwargs["messages"]
    full_prompt = " ".join(m.get("content", "") for m in messages)
    assert "<untrusted_objective>" in full_prompt
    assert "tests" in full_prompt  # check name 注入了


@pytest.mark.asyncio
async def test_run_completion_audit_retries_once_on_invalid_json():
    fake_llm = AsyncMock()
    bad = AsyncMock(); bad.content = "not json"
    good = AsyncMock(); good.content = '{"passed": false, "gaps": [{"check_name": "x", "requirement": "y", "evidence_status": "missing", "suggested_action": "z"}]}'
    fake_llm.chat.side_effect = [bad, good]

    result = await run_completion_audit(
        actor_output="output", objective="g",
        acceptance=AcceptanceCriteria(), llm=fake_llm,
    )
    assert result.passed is False
    assert fake_llm.chat.call_count == 2
