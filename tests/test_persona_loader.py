"""Tests for PersonaLoader detailed behavior."""

from pathlib import Path

import pytest

from tianshu.persona.loader import PersonaLoader


class TestPersonaLoaderEdgeCases:
    def test_missing_soul_md(self, tmp_path):
        """Persona dir without SOUL.md should be skipped."""
        persona_dir = tmp_path / "bad_persona"
        persona_dir.mkdir()
        (persona_dir / "ROLE.md").write_text("# Role")
        # No SOUL.md

        loader = PersonaLoader(tmp_path)
        personas = loader.load_all()
        assert len(personas) == 0

    def test_missing_role_md(self, tmp_path):
        """Persona dir without ROLE.md should be skipped."""
        persona_dir = tmp_path / "bad_persona"
        persona_dir.mkdir()
        (persona_dir / "SOUL.md").write_text("# Soul")
        # No ROLE.md

        loader = PersonaLoader(tmp_path)
        personas = loader.load_all()
        assert len(personas) == 0

    def test_valid_persona(self, tmp_path):
        """Valid persona with both files should load."""
        persona_dir = tmp_path / "test_agent"
        persona_dir.mkdir()
        (persona_dir / "SOUL.md").write_text(
            "---\nname: Test Agent\ndepartment: testing\n---\n# Soul"
        )
        (persona_dir / "ROLE.md").write_text("# Role")
        (persona_dir / "MEMORY.md").write_text("# Memory")

        loader = PersonaLoader(tmp_path)
        personas = loader.load_all()
        assert "test_agent" in personas
        assert personas["test_agent"].name == "Test Agent"

    def test_court_dir_excluded(self, tmp_path):
        """The 'court' directory should be excluded from persona loading."""
        court_dir = tmp_path / "court"
        court_dir.mkdir()
        (court_dir / "SOUL.md").write_text("# Soul")
        (court_dir / "ROLE.md").write_text("# Role")

        loader = PersonaLoader(tmp_path)
        personas = loader.load_all()
        assert "court" not in personas

    def test_nonexistent_dir(self):
        loader = PersonaLoader(Path("/nonexistent"))
        personas = loader.load_all()
        assert personas == {}
