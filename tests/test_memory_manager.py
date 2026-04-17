"""Tests for PersonaMemoryManager."""

from pathlib import Path

import pytest

from tianshu.models import Edict, Memorial, TaskStatus
from tianshu.persona.memory_manager import PersonaMemoryManager
from tianshu.persona.model import AgentPersona


class TestPersonaMemoryManager:
    @pytest.fixture
    def manager(self):
        return PersonaMemoryManager()

    def test_read_memory_empty(self, manager, tmp_path):
        result = manager.read_memory("nonexistent", tmp_path)
        assert result == ""

    def test_read_memory(self, manager, tmp_path):
        persona_dir = tmp_path / "bingbu"
        persona_dir.mkdir()
        mem_file = persona_dir / "MEMORY.md"
        mem_file.write_text("# Memory\n\n- entry 1\n")

        result = manager.read_memory("bingbu", tmp_path)
        assert "entry 1" in result

    def test_append_log(self, manager, tmp_path):
        persona_dir = tmp_path / "bingbu"
        persona_dir.mkdir()
        (persona_dir / "MEMORY.md").write_text("# Memory\n")

        manager.append_log("bingbu", tmp_path, "task completed", category="O")

        content = (persona_dir / "MEMORY.md").read_text()
        assert "task completed" in content
        assert "[O]" in content

    def test_append_log_creates_dir(self, manager, tmp_path):
        manager.append_log("newagent", tmp_path, "first entry")
        content = (tmp_path / "newagent" / "MEMORY.md").read_text()
        assert "first entry" in content

    async def test_on_agent_end(self, manager, tmp_path):
        persona_dir = tmp_path / "bingbu"
        persona_dir.mkdir()
        (persona_dir / "MEMORY.md").write_text("# Memory\n")

        persona = AgentPersona(
            id="bingbu",
            name="兵部",
            department="bingbu",
            soul_path=persona_dir / "SOUL.md",
            role_path=persona_dir / "ROLE.md",
            memory_path=persona_dir / "MEMORY.md",
        )

        edict = Edict(goal="test task")
        memorial = Memorial(
            edict_id=edict.id,
            status=TaskStatus.COMPLETED,
            summary="Done successfully",
        )

        await manager.on_agent_end(
            edict=edict,
            memorial=memorial,
            persona=persona,
            personas_dir=tmp_path,
        )

        content = (persona_dir / "MEMORY.md").read_text()
        assert "Done successfully" in content

    async def test_on_agent_end_no_persona(self, manager):
        # Should not raise when no persona provided
        result = await manager.on_agent_end(edict=None, memorial=None)
        assert result is None
