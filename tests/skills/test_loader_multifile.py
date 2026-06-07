"""Tests for SkillsLoader multi-file resource management (write/remove_skill_file)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.skills.loader import SkillsLoader

_SKILL_MD = "---\nname: {name}\ndescription: test skill\n---\n\n# {name}\n\nbody"


@pytest.fixture
def loader(tmp_path: Path) -> SkillsLoader:
    (tmp_path / "builtin").mkdir()
    (tmp_path / "user").mkdir()
    return SkillsLoader(
        builtin_dir=tmp_path / "builtin",
        user_dir=tmp_path / "user",
    )


def _create(loader: SkillsLoader, name: str) -> None:
    loader.create_skill(name, _SKILL_MD.format(name=name))


class TestWriteSkillFile:
    def test_write_scripts_file_ok(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        result = loader.write_skill_file("my-skill", "scripts/helper.py", "print('hi')")
        assert result["name"] == "my-skill"
        assert result["file"] == "scripts/helper.py"
        assert result["bytes"] > 0
        # File actually on disk
        skill_dir = loader._user_dir / "my-skill"
        assert (skill_dir / "scripts" / "helper.py").is_file()

    def test_write_references_file_ok(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        loader.write_skill_file("my-skill", "references/guide.md", "# Guide\n\ncontent")
        skill_dir = loader._user_dir / "my-skill"
        assert (skill_dir / "references" / "guide.md").is_file()

    def test_write_assets_file_ok(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        loader.write_skill_file("my-skill", "assets/image.png", "fake bytes")
        skill_dir = loader._user_dir / "my-skill"
        assert (skill_dir / "assets" / "image.png").is_file()

    def test_write_templates_file_ok(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        loader.write_skill_file("my-skill", "templates/tpl.txt", "template content")
        skill_dir = loader._user_dir / "my-skill"
        assert (skill_dir / "templates" / "tpl.txt").is_file()

    def test_path_traversal_dotdot_rejected(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        with pytest.raises(ValueError, match="path traversal"):
            loader.write_skill_file("my-skill", "../evil", "bad")

    def test_path_dotdot_in_middle_rejected(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        with pytest.raises(ValueError):
            loader.write_skill_file("my-skill", "scripts/../../x", "bad")

    def test_absolute_path_rejected(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        with pytest.raises(ValueError, match="invalid resource path"):
            loader.write_skill_file("my-skill", "/etc/passwd", "bad")

    def test_non_whitelisted_top_dir_rejected(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        with pytest.raises(ValueError, match="top dir must be one of"):
            loader.write_skill_file("my-skill", "secrets/x.txt", "bad")

    def test_pure_directory_path_rejected(self, loader: SkillsLoader) -> None:
        """Path with only top-level dir (len(parts) < 2) must be rejected."""
        _create(loader, "my-skill")
        with pytest.raises(ValueError):
            loader.write_skill_file("my-skill", "scripts", "bad")

    def test_backslash_in_path_rejected(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        with pytest.raises(ValueError, match="invalid resource path"):
            loader.write_skill_file("my-skill", "scripts\\evil.py", "bad")

    def test_over_1mib_raises(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        big = "x" * (1024 * 1024 + 1)
        with pytest.raises(ValueError, match="exceeds"):
            loader.write_skill_file("my-skill", "scripts/big.py", big)

    def test_skill_not_found_raises(self, loader: SkillsLoader) -> None:
        with pytest.raises(FileNotFoundError):
            loader.write_skill_file("nonexistent", "scripts/x.py", "content")

    def test_write_invalidates_l2_metadata_cache(self, loader: SkillsLoader) -> None:
        """write_skill_file must set _l2_metadata to None."""
        _create(loader, "my-skill")
        # Warm up L2 cache
        loader.list_all_metadata()
        assert loader._l2_metadata is not None
        # Writing a resource file should invalidate
        loader.write_skill_file("my-skill", "scripts/x.py", "print()")
        assert loader._l2_metadata is None


class TestRemoveSkillFile:
    def test_remove_existing_file_returns_true(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        loader.write_skill_file("my-skill", "scripts/helper.py", "print('hi')")
        result = loader.remove_skill_file("my-skill", "scripts/helper.py")
        assert result is True
        skill_dir = loader._user_dir / "my-skill"
        assert not (skill_dir / "scripts" / "helper.py").exists()

    def test_remove_nonexistent_file_returns_false(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        result = loader.remove_skill_file("my-skill", "scripts/missing.py")
        assert result is False

    def test_remove_with_traversal_raises(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        with pytest.raises(ValueError):
            loader.remove_skill_file("my-skill", "../evil")

    def test_remove_skill_not_found_raises(self, loader: SkillsLoader) -> None:
        with pytest.raises(FileNotFoundError):
            loader.remove_skill_file("nonexistent", "scripts/x.py")

    def test_remove_invalidates_l2_metadata_cache(self, loader: SkillsLoader) -> None:
        _create(loader, "my-skill")
        loader.write_skill_file("my-skill", "scripts/helper.py", "print()")
        loader.list_all_metadata()
        assert loader._l2_metadata is not None
        loader.remove_skill_file("my-skill", "scripts/helper.py")
        assert loader._l2_metadata is None
