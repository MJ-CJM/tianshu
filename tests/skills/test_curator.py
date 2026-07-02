"""Tests for SkillCurator (修撰) — LLM-planned consolidation/archival."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tianshu.config_manager import AgentConfigState
from tianshu.skills.curator import SkillCurator
from tianshu.skills.loader import SkillsLoader
from tianshu.skills.metrics import SkillMetricsStore
from tianshu.storage import Storage

_SKILL = "---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\nbody for {name}"


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    """Returns a fixed JSON payload; records how many times chat() was called."""

    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload, ensure_ascii=False)
        self.calls = 0

    async def chat(self, messages):  # noqa: ANN001
        self.calls += 1
        return _Resp(self._payload)


class _FakeConfig:
    def __init__(self, **kw) -> None:
        self._c = AgentConfigState(**kw)

    @property
    def agent_config(self) -> AgentConfigState:
        return self._c


@pytest.fixture
def env(tmp_path: Path):
    db = Storage(str(tmp_path / "t.db"))
    db.init_db()
    ms = SkillMetricsStore(db._conn)
    (tmp_path / "builtin").mkdir()
    (tmp_path / "user").mkdir()
    loader = SkillsLoader(builtin_dir=tmp_path / "builtin", user_dir=tmp_path / "user")
    yield loader, ms, db, tmp_path
    db.close()


def _add_agent_skill(loader, ms, name, desc="d"):
    loader.create_skill(name, _SKILL.format(name=name, desc=desc))
    ms.ensure_exists(name, created_by="agent")


def _live(loader, name) -> bool:
    return any(m["name"] == name for m in loader.list_all_metadata())


def _make_curator(env, payload, **cfg):
    loader, ms, db, tmp = env
    llm = _FakeLLM(payload)
    curator = SkillCurator(
        llm_client=llm,
        loader=loader,
        metrics_store=ms,
        storage=db,
        config_manager=_FakeConfig(**cfg),
        runtime_dir=tmp / "runtime",
    )
    return curator, llm


@pytest.mark.asyncio
async def test_consolidation_and_archival(env):
    loader, ms, _, tmp = env
    _add_agent_skill(loader, ms, "scrape-react", "scrape react sites")
    _add_agent_skill(loader, ms, "scrape-vue", "scrape vue sites")
    _add_agent_skill(loader, ms, "obsolete-thing", "no longer useful")

    plan = {
        "consolidations": [
            {
                "into": "web-scraping",
                "into_content": _SKILL.format(name="web-scraping", desc="unified scraping"),
                "absorb": ["scrape-react", "scrape-vue"],
                "reason": "merge",
            }
        ],
        "archivals": [{"name": "obsolete-thing", "reason": "stale"}],
    }
    curator, llm = _make_curator(env, plan)

    result = await curator.run(trigger_source="test", dry_run=False)

    assert llm.calls == 1
    assert "web-scraping" in result.created
    assert set(result.archived) == {"scrape-react", "scrape-vue", "obsolete-thing"}
    # New umbrella is live; absorbed + archived skills are gone from the library.
    assert _live(loader, "web-scraping")
    assert not _live(loader, "scrape-react")
    assert not _live(loader, "obsolete-thing")
    # Provenance recorded.
    assert ms.get("scrape-react").absorbed_into == "web-scraping"
    assert ms.get("obsolete-thing").state == "archived"
    assert ms.get("web-scraping").created_by == "agent"
    # Audit report written.
    assert result.report_dir and Path(result.report_dir, "REPORT.md").is_file()


@pytest.mark.asyncio
async def test_dry_run_makes_no_changes(env):
    loader, ms, _, _ = env
    _add_agent_skill(loader, ms, "alpha")
    _add_agent_skill(loader, ms, "beta")
    plan = {
        "consolidations": [
            {
                "into": "merged",
                "into_content": _SKILL.format(name="merged", desc="m"),
                "absorb": ["alpha", "beta"],
                "reason": "r",
            }
        ],
        "archivals": [],
    }
    curator, _ = _make_curator(env, plan)

    result = await curator.run(trigger_source="test", dry_run=True)

    assert result.dry_run and result.plan is not None
    assert result.created == [] and result.archived == []
    # Nothing moved.
    assert _live(loader, "alpha") and _live(loader, "beta")
    assert not _live(loader, "merged")
    assert result.report_dir is None


@pytest.mark.asyncio
async def test_invalid_umbrella_is_skipped(env):
    loader, ms, _, _ = env
    _add_agent_skill(loader, ms, "one")
    _add_agent_skill(loader, ms, "two")
    plan = {
        "consolidations": [
            {
                "into": "BadName",  # uppercase → fails name validation
                "into_content": _SKILL.format(name="BadName", desc="x"),
                "absorb": ["one", "two"],
                "reason": "r",
            }
        ],
        "archivals": [],
    }
    curator, _ = _make_curator(env, plan)

    result = await curator.run(trigger_source="test", dry_run=False)

    assert result.created == []
    assert any("invalid" in e or "collides" in e for e in result.errors)
    # Absorbed skills must NOT be archived when the umbrella was rejected.
    assert _live(loader, "one") and _live(loader, "two")


@pytest.mark.asyncio
async def test_pinned_skill_not_archived(env):
    loader, ms, _, _ = env
    _add_agent_skill(loader, ms, "keep")
    _add_agent_skill(loader, ms, "filler")
    ms.set_pinned("keep", True)
    plan = {"consolidations": [], "archivals": [{"name": "keep", "reason": "r"}]}
    curator, _ = _make_curator(env, plan)

    result = await curator.run(trigger_source="test", dry_run=False)

    assert "keep" not in result.archived
    assert _live(loader, "keep")


@pytest.mark.asyncio
async def test_few_candidates_skips_llm(env):
    loader, ms, _, _ = env
    _add_agent_skill(loader, ms, "solo")
    curator, llm = _make_curator(env, {"consolidations": [], "archivals": []})

    result = await curator.run(trigger_source="test", dry_run=False)

    assert llm.calls == 0  # < 2 candidates → no LLM call
    assert result.plan is None


@pytest.mark.asyncio
async def test_disabled_returns_skipped(env):
    loader, ms, _, _ = env
    _add_agent_skill(loader, ms, "x")
    _add_agent_skill(loader, ms, "y")
    curator, llm = _make_curator(
        env, {"consolidations": [], "archivals": []}, skill_curator_enabled=False
    )

    result = await curator.run(trigger_source="test", dry_run=False)

    assert result.skipped == "disabled"
    assert llm.calls == 0
