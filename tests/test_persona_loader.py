"""Tests for PersonaLoader detailed behavior."""

from pathlib import Path

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


class TestPersonaLoaderDeleteOverlayBoundary:
    def test_delete_never_touches_packaged_defaults_dir(self, tmp_path):
        """delete() 只清 runtime overlay 与 DB；packaged 默认目录不可变。"""
        import hashlib

        packaged = tmp_path / "packaged"
        (packaged / "bingbu").mkdir(parents=True)
        (packaged / "bingbu" / "SOUL.md").write_text(
            "---\nname: 兵部\ndepartment: bingbu\n---\n# Soul", encoding="utf-8"
        )
        (packaged / "bingbu" / "ROLE.md").write_text("# Role", encoding="utf-8")
        runtime = tmp_path / "runtime"
        (runtime / "bingbu").mkdir(parents=True)
        (runtime / "bingbu" / "SOUL.md").write_text("# runtime soul", encoding="utf-8")

        class _FakeStorage:
            def delete_persona(self, pid):
                return True

            def list_personas(self):
                return []

        loader = PersonaLoader(packaged, storage=_FakeStorage(), runtime_personas_dir=runtime)

        digest_before = {
            str(p.relative_to(packaged)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(packaged.rglob("*"))
            if p.is_file()
        }
        assert loader.delete("bingbu") is True

        digest_after = {
            str(p.relative_to(packaged)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(packaged.rglob("*"))
            if p.is_file()
        }
        assert digest_after == digest_before, "packaged 默认目录被 delete() 篡改"
        assert not (runtime / "bingbu").exists(), "runtime overlay 目录应被清除"
