"""Builtin 技能的 copy-on-write：任何写路径不得触碰 builtin 目录。"""

import hashlib
from pathlib import Path

import pytest

from tianshu.skills.loader import SkillsLoader


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture
def loaders(tmp_path):
    builtin = tmp_path / "builtin"
    (builtin / "demo-skill").mkdir(parents=True)
    (builtin / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\n---\noriginal body", encoding="utf-8"
    )
    (builtin / "demo-skill" / "references").mkdir()
    (builtin / "demo-skill" / "references" / "doc.md").write_text("ref", encoding="utf-8")
    user = tmp_path / "user-skills"
    user.mkdir()
    loader = SkillsLoader(builtin_dir=builtin, user_dir=user)
    return loader, builtin, user


def test_save_skill_on_builtin_materializes_into_user_overlay(loaders):
    loader, builtin, user = loaders
    before = _tree_digest(builtin)

    loader.save_skill("demo-skill", "edited body")

    assert _tree_digest(builtin) == before, "save_skill 不得改写 builtin 目录"
    user_copy = user / "demo-skill" / "SKILL.md"
    assert user_copy.is_file()
    assert "edited body" in user_copy.read_text(encoding="utf-8")
    # 读取优先级：user overlay 覆盖 builtin
    assert "edited body" in loader.get_skill("demo-skill")["content"]


def test_write_skill_resource_on_builtin_materializes_into_user_overlay(loaders):
    loader, builtin, user = loaders
    before = _tree_digest(builtin)

    loader.write_skill_file("demo-skill", "references/new.md", "new resource")

    assert _tree_digest(builtin) == before, "write_skill_file 不得写 builtin 目录"
    assert (user / "demo-skill" / "references" / "new.md").is_file()


def test_remove_skill_resource_on_builtin_materializes_and_removes_overlay_copy(loaders):
    loader, builtin, user = loaders
    before = _tree_digest(builtin)

    removed = loader.remove_skill_file("demo-skill", "references/doc.md")

    assert _tree_digest(builtin) == before, "remove_skill_file 不得删 builtin 文件"
    assert removed is True
    # overlay 副本中该资源被移除，builtin 原件保留
    assert not (user / "demo-skill" / "references" / "doc.md").exists()
    assert (builtin / "demo-skill" / "references" / "doc.md").is_file()
