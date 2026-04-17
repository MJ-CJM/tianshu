"""Tests for PromptBuilder."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tianshu.models import Edict
from tianshu.persona.loader import PersonaLoader
from tianshu.persona.model import AgentPersona
from tianshu.persona.prompt_builder import PromptBuilder
from tianshu.skills.loader import SkillsLoader


class TestPromptBuilder:
    @pytest.fixture
    def skills(self, tmp_path):
        return SkillsLoader(builtin_dir=tmp_path, char_budget=1000)

    @pytest.fixture
    def personas_dir(self):
        return Path(__file__).parent.parent / "personas"

    @pytest.fixture
    def builder(self, personas_dir, skills):
        return PromptBuilder(personas_dir=personas_dir, skills_loader=skills)

    @pytest.mark.asyncio
    async def test_build_without_persona(self, builder):
        edict = Edict(goal="test task")
        prompt = await builder.build(edict)
        assert "Tianshu" in prompt
        assert edict.id in prompt

    @pytest.mark.asyncio
    async def test_build_with_persona(self, builder, personas_dir):
        loader = PersonaLoader(personas_dir)
        loader.load_all()
        persona = loader.get("bingbu")

        edict = Edict(goal="test task")
        prompt = await builder.build(edict, persona=persona)
        assert "Tianshu" in prompt
        assert "兵部" in prompt or "Ministry of War" in prompt
        assert edict.id in prompt

    @pytest.mark.asyncio
    async def test_build_includes_court(self, builder, personas_dir):
        loader = PersonaLoader(personas_dir)
        loader.load_all()
        persona = loader.get("neige")

        edict = Edict(goal="plan something")
        prompt = await builder.build(edict, persona=persona)
        assert "Imperial Court" in prompt or "朝廷" in prompt
