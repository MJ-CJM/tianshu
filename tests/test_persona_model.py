"""Tests for Persona model, loader, selector, and memory manager."""

from pathlib import Path

from tianshu.persona.loader import PersonaLoader
from tianshu.persona.model import AgentPersona
from tianshu.persona.selector import OfficialSelector
from tianshu.resources.overlay import packaged_defaults


class TestAgentPersona:
    def test_creation(self):
        p = AgentPersona(
            id="bingbu",
            name="兵部",
            department="bingbu",
            soul_path=Path("/tmp/SOUL.md"),
            role_path=Path("/tmp/ROLE.md"),
            memory_path=Path("/tmp/MEMORY.md"),
        )
        assert p.id == "bingbu"
        # 默认 4（T4，不设上限）：官员工具 ACL 强制后（#40），未声明 = 不限，
        # 0 成为可用的真实上限（纯只读官员）。
        assert p.tool_tier_max == 4
        assert p.can_delegate is False

    def test_with_tools(self):
        p = AgentPersona(
            id="test",
            name="Test",
            department="test",
            soul_path=Path("/tmp/SOUL.md"),
            role_path=Path("/tmp/ROLE.md"),
            memory_path=Path("/tmp/MEMORY.md"),
            tools_allowed=["read_file"],
            tools_denied=["shell_exec"],
            tool_tier_max=1,
        )
        assert "read_file" in p.tools_allowed
        assert "shell_exec" in p.tools_denied


class TestPersonaLoader:
    def test_load_all(self):
        personas_dir = packaged_defaults().personas_dir()
        loader = PersonaLoader(personas_dir)
        personas = loader.load_all()
        assert "bingbu" in personas
        assert "neige" in personas
        assert "ducha" in personas
        assert "tongzheng" in personas

    def test_get(self):
        personas_dir = packaged_defaults().personas_dir()
        loader = PersonaLoader(personas_dir)
        loader.load_all()
        persona = loader.get("bingbu")
        assert persona is not None
        assert persona.name == "兵部 (Ministry of War)"

    def test_get_nonexistent(self):
        personas_dir = packaged_defaults().personas_dir()
        loader = PersonaLoader(personas_dir)
        loader.load_all()
        assert loader.get("nonexistent") is None

    def test_empty_dir(self, tmp_path):
        loader = PersonaLoader(tmp_path)
        personas = loader.load_all()
        assert personas == {}


class TestOfficialSelector:
    def test_select(self):
        personas_dir = packaged_defaults().personas_dir()
        loader = PersonaLoader(personas_dir)
        loader.load_all()
        selector = OfficialSelector(loader)

        p = selector.select("execute")
        assert p is not None
        assert p.id == "bingbu"

    def test_select_plan(self):
        personas_dir = packaged_defaults().personas_dir()
        loader = PersonaLoader(personas_dir)
        loader.load_all()
        selector = OfficialSelector(loader)

        p = selector.select("plan")
        assert p is not None
        assert p.id == "neige"

    def test_select_unknown(self):
        personas_dir = packaged_defaults().personas_dir()
        loader = PersonaLoader(personas_dir)
        loader.load_all()
        selector = OfficialSelector(loader)

        # 未知意图回退到合理的兜底官员（而非 None）——确保任务总有官员承接
        assert selector.select("unknown") is not None
