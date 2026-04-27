"""监督报告集成测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tianshu.executor.orchestrator.state import (
    ChecksResult,
    CriticResult,
    IterationRecord,
    OuterLoopState,
)
from tianshu.executor.orchestrator.supervision import generate_supervision_report
from tianshu.models.common import TaskStatus
from tianshu.models.edict import Edict
from tianshu.storage import Storage


def _make_persona(persona_id="ducha-yu", name="都察院·林御史", dept="ducha"):
    p = MagicMock()
    p.id = persona_id
    p.name = name
    p.department = dept
    p.soul_path = None
    p.role_path = None
    p.llm_config_name = None
    return p


def _make_state_with_history():
    now = datetime.now(timezone.utc)
    record = IterationRecord(
        iteration=0, level="L0", actor_output="第一稿内容...",
        checks_result=ChecksResult(all_passed=True),
        critic_result=CriticResult(
            verdict="fail", issue_class="factual_error", feedback="事实错误：xx",
        ),
        started_at=now, finished_at=now, cost_cny=0.001,
    )
    return OuterLoopState(
        edict_id="e1", iteration=2, current_level="L0",
        history=(record,), total_cost_cny=0.005,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_supervision_report_happy_path():
    edict = Edict(id="e1", goal="写一段介绍")
    state = _make_state_with_history()
    persona = _make_persona()
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(
        content="""{
            "issues_observed": ["第 1 轮事实错误"],
            "well_done": ["第 2 轮采纳建议"],
            "poorly_done": ["首轮缺校验"],
            "recommendation": "建议先列大纲"
        }""",
    ))
    report = await generate_supervision_report(
        edict, state, TaskStatus.COMPLETED, persona, llm,
    )
    assert report.edict_id == "e1"
    assert report.persona_id == "ducha-yu"
    assert report.persona_name == "都察院·林御史"
    assert report.final_status == TaskStatus.COMPLETED
    assert report.iterations_count == 2
    assert report.issues_observed == ["第 1 轮事实错误"]
    assert report.well_done == ["第 2 轮采纳建议"]
    assert report.poorly_done == ["首轮缺校验"]
    assert report.recommendation == "建议先列大纲"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_supervision_report_llm_failure_returns_empty_report():
    """LLM 调用失败不抛异常，仍返回 SupervisionReport（章节空 + raw_feedback 含错误）。"""
    edict = Edict(id="e1", goal="g")
    state = _make_state_with_history()
    persona = _make_persona()
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))

    report = await generate_supervision_report(
        edict, state, TaskStatus.FAILED, persona, llm,
    )
    assert report.issues_observed == []
    assert report.recommendation is None
    assert "LLM down" in report.raw_feedback


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_supervision_report_invalid_json_falls_back():
    """LLM 返非 JSON 时，章节空但 raw_feedback 保留。"""
    edict = Edict(id="e1", goal="g")
    state = _make_state_with_history()
    persona = _make_persona()
    llm = MagicMock()
    llm.chat = AsyncMock(return_value=MagicMock(
        content="不是合法 JSON 也没有 {} 块",
    ))
    report = await generate_supervision_report(
        edict, state, TaskStatus.COMPLETED, persona, llm,
    )
    assert report.issues_observed == []
    assert "不是合法" in report.raw_feedback


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generate_supervision_report_resume_history_empty(tmp_path):
    """resume 后 state.history=()，回查 outer_loop_iterations 表。"""
    storage = Storage(str(tmp_path / "t.db"))
    if hasattr(storage, "init_db"):
        storage.init_db()
    # 模拟之前已落库的迭代
    storage.save_outer_loop_iteration({
        "id": "i1", "edict_id": "e1", "iteration": 0, "level": "L0",
        "actor_output": "落库的旧 output", "checks_result": "{}",
        "critic_result": '{"verdict":"fail","issue_class":"factual_error","feedback":"x"}',
        "cost_cny": 0.001,
        "started_at": "2026-04-27T00:00:00Z",
        "finished_at": "2026-04-27T00:01:00Z",
    })

    edict = Edict(id="e1", goal="g")
    state = OuterLoopState(edict_id="e1", iteration=2, history=(), total_cost_cny=0.005)
    persona = _make_persona()

    captured = []

    async def capture_chat(messages):
        captured.append(messages)
        return MagicMock(content='{"issues_observed":[],"well_done":[],"poorly_done":[],"recommendation":"x"}')

    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=capture_chat)

    report = await generate_supervision_report(
        edict, state, TaskStatus.COMPLETED, persona, llm, storage=storage,
    )
    # 验证回查 DB 后 prompt 含旧 actor_output
    assert "落库的旧 output" in captured[0][1]["content"]
    assert report.recommendation == "x"
