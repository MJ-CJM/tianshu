"""Tests for immediate skill visibility after create/write — no watcher needed."""

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


class TestCreateSkillImmediateVisibility:
    def test_get_skill_after_create(self, loader: SkillsLoader) -> None:
        loader.create_skill("new-skill", _SKILL_MD.format(name="new-skill"))
        skill = loader.get_skill("new-skill")
        assert skill is not None
        assert skill["name"] == "new-skill"

    def test_list_all_metadata_after_create(self, loader: SkillsLoader) -> None:
        loader.create_skill("visible-skill", _SKILL_MD.format(name="visible-skill"))
        metadata = loader.list_all_metadata()
        names = [m["name"] for m in metadata]
        assert "visible-skill" in names

    def test_create_invalidates_l2_metadata(self, loader: SkillsLoader) -> None:
        """_l2_metadata must be None after create (watcher is not required)."""
        # Warm up L2 cache
        loader.list_all_metadata()
        assert loader._l2_metadata is not None
        loader.create_skill("fresh-skill", _SKILL_MD.format(name="fresh-skill"))
        assert loader._l2_metadata is None

    def test_second_list_after_create_reflects_new_skill(self, loader: SkillsLoader) -> None:
        """After create, a second list_all_metadata call must include the new skill."""
        loader.list_all_metadata()  # prime L2
        loader.create_skill("added-later", _SKILL_MD.format(name="added-later"))
        metadata = loader.list_all_metadata()
        assert any(m["name"] == "added-later" for m in metadata)

    def test_multiple_creates_all_visible(self, loader: SkillsLoader) -> None:
        for i in range(3):
            name = f"skill-{i}"
            loader.create_skill(name, _SKILL_MD.format(name=name))
        metadata = loader.list_all_metadata()
        names = {m["name"] for m in metadata}
        assert {"skill-0", "skill-1", "skill-2"} <= names


class TestWriteSkillFileImmediateVisibility:
    def test_write_skill_file_invalidates_l2_metadata(self, loader: SkillsLoader) -> None:
        loader.create_skill("my-skill", _SKILL_MD.format(name="my-skill"))
        loader.list_all_metadata()
        assert loader._l2_metadata is not None
        loader.write_skill_file("my-skill", "scripts/x.py", "print()")
        assert loader._l2_metadata is None

    def test_write_skill_file_does_not_corrupt_skill(self, loader: SkillsLoader) -> None:
        """Writing a resource file must not break get_skill on the parent skill."""
        loader.create_skill("parent-skill", _SKILL_MD.format(name="parent-skill"))
        loader.write_skill_file("parent-skill", "scripts/helper.py", "print('hi')")
        skill = loader.get_skill("parent-skill")
        assert skill is not None
        assert skill["name"] == "parent-skill"
