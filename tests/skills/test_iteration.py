"""Tests for SkillMetricsStore.list_iteration_candidates, SkillMetrics properties, and SkillCurator._iterate_pass."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tianshu.config_manager import AgentConfigState
from tianshu.skills.curator import SkillCurator
from tianshu.skills.loader import SkillsLoader
from tianshu.skills.metrics import SkillMetrics, SkillMetricsStore
from tianshu.storage import Storage

_SKILL_MD = (
    "---\nname: {name}\ndescription: test skill for iteration\n---\n\n# {name}\n\nbody for {name}"
)


@pytest.fixture
def env(tmp_path: Path):
    db = Storage(str(tmp_path / "t.db"))
    db.init_db()
    ms = SkillMetricsStore(db._conn)
    (tmp_path / "builtin").mkdir()
    (tmp_path / "user").mkdir()
    loader = SkillsLoader(
        builtin_dir=tmp_path / "builtin",
        user_dir=tmp_path / "user",
    )
    yield loader, ms, db, tmp_path
    db.close()


def _add_agent_skill(loader: SkillsLoader, ms: SkillMetricsStore, name: str) -> None:
    loader.create_skill(name, _SKILL_MD.format(name=name))
    ms.ensure_exists(name, created_by="agent")


def _set_counters(ms: SkillMetricsStore, name: str, usage: int, success: int) -> None:
    ms._conn.execute(
        "UPDATE skill_metrics SET usage_count = ?, success_count = ? WHERE skill_name = ?",
        (usage, success, name),
    )
    ms._conn.commit()


class TestListIterationCandidates:
    def test_returns_low_success_active_agent_skills(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "bad-skill")
        # usage=5, success=1 → success_rate=0.2 < 0.5
        _set_counters(ms, "bad-skill", usage=5, success=1)
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=3)
        assert any(c.skill_name == "bad-skill" for c in candidates)

    def test_excludes_pinned(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "pinned-skill")
        _set_counters(ms, "pinned-skill", usage=5, success=1)
        ms.set_pinned("pinned-skill", True)
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=3)
        assert not any(c.skill_name == "pinned-skill" for c in candidates)

    def test_excludes_human_curated(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "curated-skill")
        _set_counters(ms, "curated-skill", usage=5, success=1)
        ms.set_human_curated("curated-skill", True)
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=3)
        assert not any(c.skill_name == "curated-skill" for c in candidates)

    def test_excludes_non_active_state(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "stale-skill")
        _set_counters(ms, "stale-skill", usage=5, success=1)
        ms.set_state("stale-skill", "stale")
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=3)
        assert not any(c.skill_name == "stale-skill" for c in candidates)

    def test_excludes_below_min_usage(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "few-uses")
        _set_counters(ms, "few-uses", usage=2, success=0)
        # usage=2 < min_usage=3
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=3)
        assert not any(c.skill_name == "few-uses" for c in candidates)

    def test_excludes_insufficient_data_for_rate(self, env) -> None:
        """success_rate is None for usage < 3; such skills should be excluded."""
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "too-few")
        _set_counters(ms, "too-few", usage=2, success=0)
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=1)
        assert not any(c.skill_name == "too-few" for c in candidates)

    def test_excludes_high_success_rate(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "good-skill")
        # usage=5, success=5 → success_rate=1.0 >= 0.5
        _set_counters(ms, "good-skill", usage=5, success=5)
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=3)
        assert not any(c.skill_name == "good-skill" for c in candidates)

    def test_excludes_manual_skills(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "manual-skill")
        _set_counters(ms, "manual-skill", usage=5, success=1)
        # Change created_by to manual
        ms._conn.execute(
            "UPDATE skill_metrics SET created_by = 'manual' WHERE skill_name = ?",
            ("manual-skill",),
        )
        ms._conn.commit()
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=3)
        assert not any(c.skill_name == "manual-skill" for c in candidates)

    def test_multiple_candidates_all_returned(self, env) -> None:
        loader, ms, _, _ = env
        for name in ("bad-a", "bad-b"):
            _add_agent_skill(loader, ms, name)
            _set_counters(ms, name, usage=4, success=1)
        candidates = ms.list_iteration_candidates(min_success_rate=0.5, min_usage=3)
        names = {c.skill_name for c in candidates}
        assert {"bad-a", "bad-b"} <= names


class TestSetHumanCurated:
    def test_set_human_curated_sets_flag_and_timestamp(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "skill-a")
        ms.set_human_curated("skill-a", True)
        m = ms.get("skill-a")
        assert m is not None
        assert m.human_curated is True
        assert m.last_human_action is not None

    def test_set_human_curated_false_clears_flag(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "skill-b")
        ms.set_human_curated("skill-b", True)
        ms.set_human_curated("skill-b", False)
        m = ms.get("skill-b")
        assert m is not None
        assert m.human_curated is False


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeConfig:
    def __init__(self, **kw) -> None:
        self._c = AgentConfigState(**kw)

    @property
    def agent_config(self) -> AgentConfigState:
        return self._c


class TestIteratePass:
    def _make_curator(self, env, llm_resp: str) -> SkillCurator:
        loader, ms, db, tmp = env
        mock_llm = MagicMock()
        mock_llm.chat = MagicMock(return_value=_Resp(llm_resp))

        async def async_chat(**kwargs):
            return _Resp(llm_resp)

        mock_llm.chat = async_chat

        curator = SkillCurator(
            llm_client=mock_llm,
            loader=loader,
            metrics_store=ms,
            storage=db,
            config_manager=_FakeConfig(
                skill_iterate_min_success_rate=0.5,
                skill_iterate_min_usage=3,
            ),
            runtime_dir=tmp / "runtime",
            governed_writes_available=True,
        )
        return curator

    @pytest.mark.asyncio
    async def test_iterate_pass_improves_low_success_skill(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "improve-me")
        _set_counters(ms, "improve-me", usage=5, success=1)

        improved_md = (
            "---\nname: improve-me\ndescription: improved skill\n---\n\n# Improved\n\nbetter body"
        )
        curator = self._make_curator(env, improved_md)
        improved = await curator._iterate_pass()

        assert improved == []
        # An unwired curator cannot mutate live skill content.
        skill = loader.get_skill("improve-me")
        assert skill is not None
        assert "better body" not in skill["content"]

    @pytest.mark.asyncio
    async def test_iterate_pass_skips_human_curated(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "do-not-touch")
        _set_counters(ms, "do-not-touch", usage=5, success=1)
        ms.set_human_curated("do-not-touch", True)

        curator = self._make_curator(env, "---\nname: do-not-touch\ndescription: x\n---\nbetter")
        improved = await curator._iterate_pass()

        assert "do-not-touch" not in improved

    @pytest.mark.asyncio
    async def test_iterate_pass_skips_pinned(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "keep-this")
        _set_counters(ms, "keep-this", usage=5, success=1)
        ms.set_pinned("keep-this", True)

        curator = self._make_curator(env, "---\nname: keep-this\ndescription: x\n---\nbetter")
        improved = await curator._iterate_pass()

        assert "keep-this" not in improved

    @pytest.mark.asyncio
    async def test_iterate_pass_skips_invalid_llm_output(self, env) -> None:
        """If LLM returns content that fails validation, skill is not saved."""
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "bad-llm-out")
        _set_counters(ms, "bad-llm-out", usage=5, success=1)

        # Missing required 'description' field in frontmatter -> validation fails
        invalid_md = "---\nname: bad-llm-out\n---\n\nbody without description"
        curator = self._make_curator(env, invalid_md)
        improved = await curator._iterate_pass()

        assert "bad-llm-out" not in improved

    @pytest.mark.asyncio
    async def test_iterate_pass_empty_llm_response_skipped(self, env) -> None:
        loader, ms, _, _ = env
        _add_agent_skill(loader, ms, "empty-resp")
        _set_counters(ms, "empty-resp", usage=5, success=1)

        curator = self._make_curator(env, "   ")  # empty response
        improved = await curator._iterate_pass()

        assert "empty-resp" not in improved


class TestSkillMetricsProperties:
    """Unit tests for SkillMetrics dataclass properties (success_rate, status, is_dormant)."""

    def test_success_rate_none_when_usage_lt_3(self) -> None:
        m = SkillMetrics(skill_name="x", usage_count=2, success_count=2)
        assert m.success_rate is None

    def test_success_rate_zero_usage_returns_none(self) -> None:
        m = SkillMetrics(skill_name="x", usage_count=0)
        assert m.success_rate is None

    def test_success_rate_computed_when_usage_gte_3(self) -> None:
        m = SkillMetrics(skill_name="x", usage_count=4, success_count=2)
        assert m.success_rate == 0.5

    def test_success_rate_one_hundred_percent(self) -> None:
        m = SkillMetrics(skill_name="x", usage_count=5, success_count=5)
        assert m.success_rate == 1.0

    def test_status_healthy_when_no_data(self) -> None:
        m = SkillMetrics(skill_name="x", usage_count=1)
        assert m.status == "healthy"

    def test_status_retire_suggested_when_very_low_rate_and_enough_usage(self) -> None:
        m = SkillMetrics(skill_name="x", usage_count=10, success_count=1)
        # rate = 0.1 < 0.3 and usage >= 5
        assert m.status == "retire_suggested"

    def test_status_warning_when_moderate_low_rate(self) -> None:
        m = SkillMetrics(skill_name="x", usage_count=5, success_count=2)
        # rate = 0.4, < 0.6 but not < 0.3
        assert m.status == "warning"

    def test_status_healthy_when_high_rate(self) -> None:
        m = SkillMetrics(skill_name="x", usage_count=5, success_count=4)
        # rate = 0.8 >= 0.6
        assert m.status == "healthy"

    def test_is_dormant_no_last_used(self) -> None:
        m = SkillMetrics(skill_name="x", last_used_at=None)
        assert m.is_dormant() is False

    def test_is_dormant_recent_use(self) -> None:
        recent = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        m = SkillMetrics(skill_name="x", last_used_at=recent)
        assert m.is_dormant(dormant_days=90) is False

    def test_is_dormant_old_use(self) -> None:
        old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
        m = SkillMetrics(skill_name="x", last_used_at=old)
        assert m.is_dormant(dormant_days=90) is True

    def test_is_dormant_invalid_timestamp_returns_false(self) -> None:
        m = SkillMetrics(skill_name="x", last_used_at="not-a-date")
        assert m.is_dormant() is False


class TestSkillMetricsStoreIncrements:
    """Tests for increment_usage, increment_success, increment_failure, set_state, mark_archived."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> SkillMetricsStore:
        db = Storage(str(tmp_path / "t.db"))
        db.init_db()
        ms = SkillMetricsStore(db._conn)
        ms.ensure_exists("skill-a", created_by="agent")
        yield ms
        db.close()

    def test_increment_usage(self, store: SkillMetricsStore) -> None:
        store.increment_usage("skill-a")
        store.increment_usage("skill-a")
        m = store.get("skill-a")
        assert m is not None
        assert m.usage_count == 2
        assert m.last_used_at is not None

    def test_increment_success(self, store: SkillMetricsStore) -> None:
        store.increment_success("skill-a")
        m = store.get("skill-a")
        assert m is not None
        assert m.success_count == 1

    def test_increment_failure(self, store: SkillMetricsStore) -> None:
        store.increment_failure("skill-a")
        m = store.get("skill-a")
        assert m is not None
        assert m.failure_count == 1

    def test_set_state(self, store: SkillMetricsStore) -> None:
        store.set_state("skill-a", "stale")
        m = store.get("skill-a")
        assert m is not None
        assert m.state == "stale"

    def test_mark_archived_sets_state_and_absorbed_into(self, store: SkillMetricsStore) -> None:
        store.mark_archived("skill-a", into="umbrella-skill")
        m = store.get("skill-a")
        assert m is not None
        assert m.state == "archived"
        assert m.absorbed_into == "umbrella-skill"
        assert m.archived_at is not None

    def test_mark_archived_without_into(self, store: SkillMetricsStore) -> None:
        store.mark_archived("skill-a")
        m = store.get("skill-a")
        assert m is not None
        assert m.state == "archived"
        assert m.absorbed_into is None

    def test_get_all_returns_list(self, store: SkillMetricsStore) -> None:
        store.ensure_exists("skill-b")
        rows = store.get_all()
        names = {r.skill_name for r in rows}
        assert "skill-a" in names
        assert "skill-b" in names
