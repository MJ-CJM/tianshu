"""Tests for SkillInstaller —— 安全安装管线(正常安装 / 路径穿越 / symlink / zip 炸弹 / 缺失 frontmatter)。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tianshu.skills.installer import InstallResult, SkillInstaller

_VALID_SKILL = (
    "---\n"
    "name: demo-skill\n"
    "description: A demo skill for testing.\n"
    "---\n\n"
    "# Demo\n\n"
    "Body content.\n"
)


@pytest.fixture
def target(tmp_path: Path) -> Path:
    return tmp_path / "user_skills"


@pytest.fixture
def installer(target: Path) -> SkillInstaller:
    return SkillInstaller(target)


def _skill_dir(tmp_path: Path, content: str = _VALID_SKILL) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "SKILL.md").write_text(content, encoding="utf-8")
    return src


def _zip(tmp_path: Path, members: dict[str, str]) -> Path:
    zip_path = tmp_path / "skill.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return zip_path


# ---------- 正常安装 ----------


def test_install_from_directory(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    result = installer.install(_skill_dir(tmp_path))
    assert result.installed is True
    assert result.skill_name == "demo-skill"
    assert (target / "demo-skill" / "SKILL.md").is_file()


def test_install_from_flat_zip(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    result = installer.install(_zip(tmp_path, {"SKILL.md": _VALID_SKILL}))
    assert result.installed is True
    assert (target / "demo-skill" / "SKILL.md").is_file()


def test_install_from_wrapped_zip(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    """常见 GitHub 导出布局:内容包在单层文件夹里。"""
    result = installer.install(_zip(tmp_path, {"demo-skill/SKILL.md": _VALID_SKILL}))
    assert result.installed is True
    assert (target / "demo-skill" / "SKILL.md").is_file()


def test_install_carries_bundled_resource(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    src = _skill_dir(tmp_path)
    (src / "scripts").mkdir()
    (src / "scripts" / "helper.py").write_text("print('hi')", encoding="utf-8")
    result = installer.install(src)
    assert result.installed is True
    assert (target / "demo-skill" / "scripts" / "helper.py").is_file()


# ---------- 路径穿越拒绝 ----------


def test_zip_path_traversal_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    zip_path = _zip(tmp_path, {"SKILL.md": _VALID_SKILL, "../evil.txt": "pwned"})
    result = installer.install(zip_path)
    assert result.installed is False
    assert "穿越" in result.reason
    assert not (target / "demo-skill").exists()  # 失败安全:未落地
    assert not (tmp_path / "evil.txt").exists()


def test_zip_absolute_member_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    result = installer.install(_zip(tmp_path, {"SKILL.md": _VALID_SKILL, "/etc/evil": "x"}))
    assert result.installed is False
    assert "绝对" in result.reason


# ---------- symlink 拒绝 ----------


def test_dir_symlink_rejected(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    src = _skill_dir(tmp_path)
    (src / "link").symlink_to(src / "SKILL.md")
    result = installer.install(src)
    assert result.installed is False
    assert "symlink" in result.reason
    assert not (target / "demo-skill").exists()


def test_zip_symlink_member_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    import stat as _stat

    zip_path = tmp_path / "skill.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SKILL.md", _VALID_SKILL)
        info = zipfile.ZipInfo("link")
        info.external_attr = (_stat.S_IFLNK | 0o777) << 16  # symlink 模式位
        zf.writestr(info, "/etc/passwd")
    result = installer.install(zip_path)
    assert result.installed is False
    assert "symlink" in result.reason


# ---------- zip 炸弹拒绝 ----------


def test_total_size_bomb_rejected(target: Path, tmp_path: Path) -> None:
    installer = SkillInstaller(target, max_total_bytes=32)  # 收紧到 32B
    result = installer.install(_zip(tmp_path, {"SKILL.md": _VALID_SKILL}))
    assert result.installed is False
    assert "超限" in result.reason
    assert not (target / "demo-skill").exists()


def test_member_count_bomb_rejected(target: Path, tmp_path: Path) -> None:
    installer = SkillInstaller(target, max_members=1)
    members = {"SKILL.md": _VALID_SKILL, "extra.txt": "x"}
    result = installer.install(_zip(tmp_path, members))
    assert result.installed is False
    assert "成员数超限" in result.reason


def test_single_file_bomb_rejected(target: Path, tmp_path: Path) -> None:
    installer = SkillInstaller(target, max_file_bytes=16)
    result = installer.install(_zip(tmp_path, {"SKILL.md": _VALID_SKILL}))
    assert result.installed is False
    assert "单文件超限" in result.reason


# ---------- frontmatter 缺失拒绝 ----------


def test_missing_frontmatter_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    src = _skill_dir(tmp_path, content="# Just a title\n\nNo frontmatter here.\n")
    result = installer.install(src)
    assert result.installed is False
    assert result.reason == "结构校验未通过"
    assert result.findings  # 携带结构化 findings
    assert not (target / "demo-skill").exists()


def test_missing_skill_md_rejected(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("no skill here", encoding="utf-8")
    result = installer.install(src)
    assert result.installed is False
    assert "SKILL.md" in result.reason


# ---------- 其它安全网 ----------


def test_nonexistent_source_rejected(installer: SkillInstaller, tmp_path: Path) -> None:
    result = installer.install(tmp_path / "nope")
    assert result.installed is False
    assert "源不存在" in result.reason


def test_duplicate_install_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    assert installer.install(_skill_dir(tmp_path)).installed is True
    second = installer.install(_skill_dir(tmp_path / "again"))
    assert second.installed is False
    assert "已存在" in second.reason


def test_result_is_frozen_dataclass() -> None:
    result = InstallResult(True, "x", "ok")
    with pytest.raises((AttributeError, TypeError)):
        result.installed = False  # type: ignore[misc]
