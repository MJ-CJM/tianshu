"""tianshu.resources.catalog 定位器契约：稳定、只读、与 cwd/HOME 无关。"""

import hashlib
import os
import stat
from pathlib import Path

import pytest

from tianshu.resources import catalog

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCES_SHA256 = "aed63f2352fc4a200c44e59c0fb07c17250771b965351d669f24548e0771a4fe"
_LICENSE_SHA256 = "83a6e789ba078378ff8e6db0df0827b85191e14d9c1863fa90f63bf1ece0a978"


def test_persona_defaults_exposes_six_departments_and_court() -> None:
    root = catalog.persona_defaults()
    names = {entry.name for entry in root.iterdir() if entry.is_dir()}
    assert names == {"bingbu", "ducha", "hubu", "neige", "tongzheng", "wenyuan", "court"}
    for department in sorted(names - {"court"}):
        files = {entry.name for entry in (root / department).iterdir()}
        assert files == {"SOUL.md", "ROLE.md", "MEMORY.md"}, department
    assert {entry.name for entry in (root / "court").iterdir()} == {"COURT.md", "MEMORY.md"}


def test_persona_templates_exposes_exact_language_counts_and_sources() -> None:
    root = catalog.persona_templates()
    sources = (root / "SOURCES.md").read_bytes()
    assert hashlib.sha256(sources).hexdigest() == _SOURCES_SHA256

    def count_markdown(base: object) -> int:
        total = 0
        stack = [base]
        while stack:
            node = stack.pop()
            for entry in node.iterdir():  # type: ignore[attr-defined]
                if entry.is_dir():
                    stack.append(entry)
                elif entry.name.endswith(".md"):
                    total += 1
        return total

    assert count_markdown(root / "en") == 191
    assert count_markdown(root / "zh") == 204


def test_builtin_skills_exposes_exactly_file_ops_and_shell() -> None:
    root = catalog.builtin_skills()
    names = {entry.name for entry in root.iterdir() if entry.is_dir()}
    assert names == {"file-ops", "shell"}
    for skill in names:
        assert (root / skill / "SKILL.md").is_file()


def test_executor_templates_expose_real_markdown_not_fallback() -> None:
    root = catalog.executor_templates()
    names = {entry.name for entry in (root / "edict").iterdir() if entry.name.endswith(".md")}
    assert names == {"completion_audit.md", "continuation.md", "wind_down.md"}
    body = (root / "edict" / "continuation.md").read_text(encoding="utf-8")
    assert body.strip(), "packaged template must be a real file, not an empty fallback"


def test_license_file_matches_repo_root_license() -> None:
    packaged = catalog.license_file().read_bytes()
    assert hashlib.sha256(packaged).hexdigest() == _LICENSE_SHA256


def test_version_agrees_with_public_dunder() -> None:
    from tianshu import __version__

    assert catalog.version() == __version__


def test_catalog_reads_are_independent_of_cwd_and_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    weird_home = tmp_path / "带 空格 の家"
    weird_home.mkdir()
    monkeypatch.setenv("HOME", str(weird_home))
    monkeypatch.chdir(tmp_path)
    assert (catalog.persona_defaults() / "neige" / "SOUL.md").read_bytes()
    assert (catalog.builtin_skills() / "shell" / "SKILL.md").read_bytes()
    assert catalog.license_file().read_bytes()


def test_catalog_reads_survive_read_only_package_tree() -> None:
    resource_root = Path(str(catalog.persona_defaults())).parent
    assert resource_root.name == "resources"
    restore: list[tuple[Path, int]] = []
    try:
        for directory in [resource_root, *resource_root.rglob("*")]:
            if directory.is_dir():
                mode = stat.S_IMODE(directory.stat().st_mode)
                restore.append((directory, mode))
                os.chmod(directory, mode & ~0o222)
        assert (catalog.persona_defaults() / "bingbu" / "ROLE.md").read_bytes()
        assert (catalog.persona_templates() / "SOURCES.md").read_bytes()
    finally:
        for directory, mode in reversed(restore):
            os.chmod(directory, mode)


def test_package_digest_is_stable_and_relocation_safe() -> None:
    first = catalog.package_digest()
    second = catalog.package_digest()
    assert first == second
    assert set(first) == {
        "personas",
        "persona_templates",
        "builtin_skills",
        "executor_templates",
        "license",
    }
    assert all(len(value) == 64 for value in first.values())
