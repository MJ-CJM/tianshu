"""Tests for SkillInstaller —— 安全安装管线(正常安装 / 路径穿越 / symlink / zip 炸弹 / 缺失 frontmatter)。"""

from __future__ import annotations

import hashlib
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


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode())
        if path.is_file():
            digest.update(b"file\0")
            digest.update(path.read_bytes())
        elif path.is_dir():
            digest.update(b"directory\0")
        elif path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(path.readlink().as_posix().encode())
    return digest.hexdigest()


# ---------- 公开入口已收口到候选服务 ----------


def test_legacy_install_valid_source_does_not_create_missing_live_target(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    result = installer.install(_skill_dir(tmp_path))

    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not target.exists()


def test_legacy_install_invalid_source_preserves_existing_live_tree(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    (target / "existing" / "nested").mkdir(parents=True)
    (target / "existing" / "SKILL.md").write_text(_VALID_SKILL, encoding="utf-8")
    (target / "existing" / "nested" / "data.bin").write_bytes(b"immutable-live-tree")
    before = _tree_digest(target)

    result = installer.install(_skill_dir(tmp_path, content="invalid"))

    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert _tree_digest(target) == before


def test_legacy_install_malicious_archive_does_not_create_live_target(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    source = _zip(tmp_path, {"SKILL.md": _VALID_SKILL, "../escape": "malicious"})

    result = installer.install(source)

    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not target.exists()
    assert not (tmp_path / "escape").exists()


def test_install_from_directory(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    result = installer.install(_skill_dir(tmp_path))
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not (target / "demo-skill").exists()


def test_install_from_flat_zip(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    result = installer.install(_zip(tmp_path, {"SKILL.md": _VALID_SKILL}))
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not (target / "demo-skill").exists()


def test_install_from_wrapped_zip(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    """常见 GitHub 导出布局:内容包在单层文件夹里。"""
    result = installer.install(_zip(tmp_path, {"demo-skill/SKILL.md": _VALID_SKILL}))
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not (target / "demo-skill").exists()


def test_install_carries_bundled_resource(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    src = _skill_dir(tmp_path)
    (src / "scripts").mkdir()
    (src / "scripts" / "helper.py").write_text("print('hi')", encoding="utf-8")
    result = installer.install(src)
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not (target / "demo-skill").exists()


def test_install_rejects_trailing_newline_name_without_landing_files(
    installer: SkillInstaller,
    target: Path,
    tmp_path: Path,
) -> None:
    content = _VALID_SKILL.replace("name: demo-skill", 'name: "valid\\n"')

    result = installer.install(_skill_dir(tmp_path, content=content))

    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not target.exists()


# ---------- 路径穿越拒绝 ----------


def test_zip_path_traversal_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    zip_path = _zip(tmp_path, {"SKILL.md": _VALID_SKILL, "../evil.txt": "pwned"})
    result = installer.install(zip_path)
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not (target / "demo-skill").exists()  # 失败安全:未落地
    assert not (tmp_path / "evil.txt").exists()


def test_zip_absolute_member_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    result = installer.install(_zip(tmp_path, {"SKILL.md": _VALID_SKILL, "/etc/evil": "x"}))
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not target.exists()


# ---------- symlink 拒绝 ----------


def test_dir_symlink_rejected(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    src = _skill_dir(tmp_path)
    (src / "link").symlink_to(src / "SKILL.md")
    result = installer.install(src)
    assert result == InstallResult(False, None, "governed_skill_service_required")
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
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not target.exists()


# ---------- zip 炸弹拒绝 ----------


def test_total_size_bomb_rejected(target: Path, tmp_path: Path) -> None:
    installer = SkillInstaller(target, max_total_bytes=32)  # 收紧到 32B
    result = installer.install(_zip(tmp_path, {"SKILL.md": _VALID_SKILL}))
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not (target / "demo-skill").exists()


def test_member_count_bomb_rejected(target: Path, tmp_path: Path) -> None:
    installer = SkillInstaller(target, max_members=1)
    members = {"SKILL.md": _VALID_SKILL, "extra.txt": "x"}
    result = installer.install(_zip(tmp_path, members))
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not target.exists()


def test_single_file_bomb_rejected(target: Path, tmp_path: Path) -> None:
    installer = SkillInstaller(target, max_file_bytes=16)
    result = installer.install(_zip(tmp_path, {"SKILL.md": _VALID_SKILL}))
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not target.exists()


# ---------- frontmatter 缺失拒绝 ----------


def test_missing_frontmatter_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    src = _skill_dir(tmp_path, content="# Just a title\n\nNo frontmatter here.\n")
    result = installer.install(src)
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not (target / "demo-skill").exists()


def test_missing_skill_md_rejected(installer: SkillInstaller, target: Path, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("no skill here", encoding="utf-8")
    result = installer.install(src)
    assert result == InstallResult(False, None, "governed_skill_service_required")
    assert not target.exists()


# ---------- 其它安全网 ----------


def test_nonexistent_source_rejected(installer: SkillInstaller, tmp_path: Path) -> None:
    result = installer.install(tmp_path / "nope")
    assert result == InstallResult(False, None, "governed_skill_service_required")


def test_duplicate_install_rejected(
    installer: SkillInstaller, target: Path, tmp_path: Path
) -> None:
    assert installer.install(_skill_dir(tmp_path)).reason == "governed_skill_service_required"
    second = installer.install(_skill_dir(tmp_path / "again"))
    assert second.installed is False
    assert second.reason == "governed_skill_service_required"
    assert not (target / "demo-skill").exists()


def test_result_is_frozen_dataclass() -> None:
    result = InstallResult(True, "x", "ok")
    with pytest.raises((AttributeError, TypeError)):
        result.installed = False  # type: ignore[misc]
