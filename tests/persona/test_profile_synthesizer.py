"""ProfileSynthesizer:聚合纯函数 / LLM 降级 / 端到端 persist。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from tianshu.memory.drawer import Drawer
from tianshu.persona.profile_synthesizer import ProfileSynthesisInput, ProfileSynthesizer


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    def __init__(self, payload):
        self._payload = payload
        self.prompts = []

    async def chat(self, messages):
        self.prompts.append(messages[-1]["content"])
        return _FakeResp(self._payload)


class _FakeDrawerStore:
    def __init__(self, drawers=()):
        self._drawers = list(drawers)

    async def get_drawers(self, wing, limit=200):
        return self._drawers


class _FakeStorage:
    def __init__(self, events=()):
        self._events = list(events)

    def list_persona_events(self, persona_id, since_iso):
        return self._events

    def try_acquire_synthesis_lock(self, persona_id):
        return True

    def release_synthesis_lock(self, persona_id):
        pass


class _FakeSkillMetricsStore:
    def __init__(self, metrics=()):
        self._metrics = list(metrics)

    def list_for_persona(self, persona_id):
        return self._metrics


class _FakePersonaLoader:
    def __init__(self, persona=None):
        self._persona = persona

    def get(self, persona_id):
        return self._persona


def _bare_synthesizer() -> ProfileSynthesizer:
    """构造仅用于纯函数测试的 synthesizer：依赖全为 None,不会被调用。"""
    return ProfileSynthesizer(
        llm_client=None,
        drawer_store=None,
        storage=None,
        skill_metrics_store=None,
        personas_runtime_dir=Path("/tmp/unused-profile-dir"),
        persona_loader=None,
    )


def _drawer(*, category: str, confidence: float, timestamp: str, idx: int = 0) -> Drawer:
    return Drawer(
        id=f"d{idx}",
        wing="persona",
        room="room",
        content=f"content-{idx}",
        source_edict_id="e1",
        timestamp=timestamp,
        category=category,
        confidence=confidence,
        chunk_index=0,
    )


def test_aggregate_task_distribution():
    synth = _bare_synthesizer()
    events = (
        {"event_type": "execution.completed"},
        {"event_type": "execution.completed"},
        {"event_type": "execution.completed"},
        {"event_type": "execution.completed"},
        {"event_type": "execution.failed"},
        {"event_type": "execution.failed"},
        {"event_type": "audit.completed"},
    )

    result = synth.aggregate_task_distribution(events, window_days=14)

    assert result["total"] == 7
    assert result["window_days"] == 14
    assert result["buckets"] == [
        {"type": "execution.completed", "count": 4, "pct": 57.1},
        {"type": "execution.failed", "count": 2, "pct": 28.6},
        {"type": "audit.completed", "count": 1, "pct": 14.3},
    ]
    assert result["key_events"] == list(events[4:7])


def test_aggregate_health():
    synth = _bare_synthesizer()
    now_iso = datetime.now(UTC).isoformat()
    old_iso = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    drawers = (
        _drawer(category="O", confidence=0.9, timestamp=now_iso, idx=1),
        _drawer(category="O", confidence=0.9, timestamp=now_iso, idx=2),
        _drawer(category="O", confidence=0.9, timestamp=old_iso, idx=3),
    )
    skill_metrics = (
        {"skill_name": "a", "usage_count": 10, "success_count": 9, "failure_count": 1},
        {"skill_name": "b", "usage_count": 4, "success_count": 2, "failure_count": 2},
        {"skill_name": "c", "usage_count": 6, "success_count": 1, "failure_count": 5},
    )

    result = synth.aggregate_health(drawers, skill_metrics, events_total=15, window_days=14)

    assert result == {
        "skills_status": {"healthy": 1, "warning": 1, "retire_suggested": 1},
        "active_drawers": 3,
        "drawers_added_window": 2,
        "tasks_in_window": 15,
        "activity_level": "active",
    }


def test_pick_degradation_candidates():
    synth = _bare_synthesizer()
    metrics = (
        {"skill_name": "ok_skill", "usage_count": 10, "success_count": 9, "failure_count": 1},
        {"skill_name": "warn_skill", "usage_count": 4, "success_count": 2, "failure_count": 2},
        {"skill_name": "bad_skill", "usage_count": 6, "success_count": 1, "failure_count": 5},
        {"skill_name": "unused_skill", "usage_count": 0, "success_count": 0, "failure_count": 0},
    )

    result = synth.pick_degradation_candidates(metrics)

    assert result == [
        {
            "skill": "warn_skill",
            "usage_count": 4,
            "success_count": 2,
            "failure_count": 2,
            "status": "warning",
        },
        {
            "skill": "bad_skill",
            "usage_count": 6,
            "success_count": 1,
            "failure_count": 5,
            "status": "retire_suggested",
        },
    ]


def test_detect_conflict():
    from tianshu.persona.profile_renderer import render_markdown
    from tianshu.persona.profile_schema import ProfileFrontmatter

    synth = _bare_synthesizer()
    fm = ProfileFrontmatter(persona_id="p1", persona_name="测试", version=1)
    old_auto = (
        "# 测试 · 成长档案\n\n"
        "## 擅长领域\n- **检索**:擅长信息检索\n\n"
        "## 近期任务分布(14 天)\n| 类型 | 次数 | 占比 |\n|---|---|---|\n"
    )
    prev_markdown = render_markdown(fm, old_auto, manual_section="")

    # 方向一：new_auto_section 与旧版本几乎完全不同 → 判定冲突
    very_different_auto = "# 全新内容\n\n" + ("完全不同的文本 " * 50)
    assert synth.detect_conflict(prev_markdown, very_different_auto) is True

    # 方向二：new_auto_section 与旧版本一致（无实质变化）→ 不冲突
    assert synth.detect_conflict(prev_markdown, old_auto) is False


async def test_llm_memory_review_bad_json_fails_safe():
    synth = ProfileSynthesizer(
        llm_client=_FakeLLM("这不是合法 JSON,也不是代码块"),
        drawer_store=None,
        storage=None,
        skill_metrics_store=None,
        personas_runtime_dir=Path("/tmp/unused-profile-dir"),
        persona_loader=None,
    )
    inputs = ProfileSynthesisInput(
        persona_id="p1",
        persona_name="测试",
        data_window_days=14,
        drawers=(),
        recent_events=({"kind": "task", "payload": {"summary": "did something"}},),
        skill_metrics=(),
        previous_profile_md=None,
    )

    result = await synth.llm_memory_review(inputs)

    assert result == []


async def test_run_end_to_end_with_fakes(tmp_path):
    persona = SimpleNamespace(name="诗魂")
    now_iso = datetime.now(UTC).isoformat()
    drawers = tuple(
        _drawer(category="O", confidence=0.9, timestamp=now_iso, idx=i) for i in range(6)
    )
    events = (
        {"event_type": "execution.completed"},
        {"event_type": "execution.completed"},
        {"event_type": "execution.failed"},
    )
    skill_metrics = (
        {
            "skill_name": "search",
            "usage_count": 5,
            "success_count": 1,
            "failure_count": 4,
            "status": "warning",
            "last_used_at": now_iso,
        },
    )
    fake_llm = _FakeLLM(
        json.dumps(
            {
                "specialties": [{"title": "检索", "detail": "擅长信息检索"}],
                "degradations": [{"skill": "search", "reason": "近期失败率上升"}],
                "items": [],
            }
        )
    )
    synthesizer = ProfileSynthesizer(
        llm_client=fake_llm,
        drawer_store=_FakeDrawerStore(drawers),
        storage=_FakeStorage(events=events),
        skill_metrics_store=_FakeSkillMetricsStore(skill_metrics),
        personas_runtime_dir=tmp_path,
        persona_loader=_FakePersonaLoader(persona),
    )

    result = await synthesizer.run(persona_id="poet")

    assert result is not None
    assert result.degraded is False
    assert result.version == 1

    profile_path = tmp_path / "poet" / "PROFILE.md"
    assert profile_path.exists()
    content = profile_path.read_text(encoding="utf-8")
    assert "成长档案" in content
    assert "擅长领域" in content
    assert "检索" in content
    # 首次合成没有历史文件 → previous_profile_md=None → detect_conflict 恒为 False，正常回写
    assert synthesizer.detect_conflict(None, result.auto_section) is False


class TestNarrowListResult:
    """gather(return_exceptions=True) 结果窄化 —— CancelledError(BaseException)回归锚点。"""

    def test_list_passes_through(self):
        from tianshu.persona.profile_synthesizer import _narrow_list_result

        assert _narrow_list_result([{"name": "x"}], "llm_specialties") == [{"name": "x"}]

    def test_exception_downgrades_to_empty(self):
        from tianshu.persona.profile_synthesizer import _narrow_list_result

        assert _narrow_list_result(ValueError("boom"), "llm_specialties") == []

    def test_cancelled_error_downgrades_to_empty(self):
        # CancelledError 继承 BaseException 而非 Exception,
        # 旧代码 isinstance(x, Exception) 漏判 —— 本缺陷的直接回归用例
        import asyncio

        from tianshu.persona.profile_synthesizer import _narrow_list_result

        assert _narrow_list_result(asyncio.CancelledError(), "llm_specialties") == []
