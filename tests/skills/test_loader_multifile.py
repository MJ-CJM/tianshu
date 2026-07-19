"""Tests for SkillsLoader multi-file resource management (write/remove_skill_file)."""

from __future__ import annotations

import logging
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


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, str], ...]:
    entries: list[tuple[str, str, str]] = []
    for entry in sorted(root.rglob("*")):
        relative = entry.relative_to(root).as_posix()
        if entry.is_symlink():
            entries.append((relative, "symlink", str(entry.readlink())))
        elif entry.is_dir():
            entries.append((relative, "dir", ""))
        else:
            entries.append((relative, "file", entry.read_bytes().hex()))
    return tuple(entries)


def _write_skill_tree(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        _SKILL_MD.format(name="fixture") + "\nbefore\n",
        encoding="utf-8",
    )
    resource = path / "scripts" / "run.py"
    resource.parent.mkdir(parents=True, exist_ok=True)
    resource.write_text("before\n", encoding="utf-8")


def _invalid_skill_name(case: str, staging: Path) -> str:
    return {
        "empty": "",
        "dot": ".",
        "dotdot": "..",
        "parent": "../victim",
        "absolute": str((staging / "victim").resolve()),
        "slash": "nested/name",
        "backslash": "nested\\name",
        "unicode": "技能",
        "uppercase": "Uppercase",
        "too-long": "a" * 65,
    }[case]


def _invoke_named_api(loader: SkillsLoader, operation: str, name: str) -> object:
    if operation == "create":
        return loader.create_skill(name, _SKILL_MD.format(name="created"))
    if operation == "edit":
        return loader.save_skill(name, "edited")
    if operation == "patch":
        return loader.patch_skill(name, "before", "after")
    if operation == "write":
        return loader.write_skill_file(name, "scripts/new.py", "changed\n")
    if operation == "remove":
        return loader.remove_skill_file(name, "scripts/run.py")
    if operation == "read":
        return loader.get_skill(name)
    if operation == "register":
        loader.register_skill(name, "injected")
        return None
    if operation == "delete":
        return loader.delete_skill(name)
    if operation == "archive":
        return loader.archive_skill(name)
    if operation == "restore":
        return loader.restore_skill(name)
    raise AssertionError(f"unknown operation: {operation}")


@pytest.mark.parametrize(
    "name_case",
    (
        "empty",
        "dot",
        "dotdot",
        "parent",
        "absolute",
        "slash",
        "backslash",
        "unicode",
        "uppercase",
        "too-long",
    ),
)
@pytest.mark.parametrize(
    "operation",
    (
        "create",
        "edit",
        "patch",
        "write",
        "remove",
        "read",
        "register",
        "delete",
        "archive",
        "restore",
    ),
)
def test_workspace_overlay_named_apis_reject_invalid_identifiers_without_changes(
    tmp_path: Path,
    operation: str,
    name_case: str,
) -> None:
    builtin = tmp_path / "builtin"
    staging = tmp_path / "staging"
    skills = staging / "skills"
    builtin.mkdir()
    skills.mkdir(parents=True)
    name = _invalid_skill_name(name_case, staging)

    candidates = (skills / name, skills / ".archive" / name)
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(staging.resolve()):
            _write_skill_tree(candidate)

    loader = SkillsLoader(builtin_dir=builtin).for_workspace_overlay(staging)
    before = _tree_snapshot(tmp_path)
    error: Exception | None = None
    try:
        _invoke_named_api(loader, operation, name)
    except Exception as exc:  # noqa: BLE001 - assert the public boundary below
        error = exc

    assert _tree_snapshot(tmp_path) == before
    assert isinstance(error, ValueError)
    assert "invalid skill name" in str(error).lower()


@pytest.mark.parametrize(
    "name_case",
    (
        "empty",
        "dot",
        "dotdot",
        "parent",
        "absolute",
        "slash",
        "backslash",
        "unicode",
        "uppercase",
        "too-long",
    ),
)
@pytest.mark.parametrize("operation", ("load_index", "load_always", "load_all"))
def test_filter_names_reject_invalid_identifiers_before_read_without_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    name_case: str,
) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    staging = tmp_path / "staging"
    builtin.mkdir()
    user.mkdir()
    staging.mkdir()
    _write_skill_tree(builtin / "fixture")
    loader = SkillsLoader(builtin_dir=builtin, user_dir=user)
    name = _invalid_skill_name(name_case, staging)
    reads: list[str] = []

    if operation in {"load_index", "load_always"}:
        monkeypatch.setattr(
            loader,
            "list_all_metadata",
            lambda: reads.append("metadata") or [],
        )
    else:
        monkeypatch.setattr(
            loader,
            "_scan_dir",
            lambda *_args: reads.append("directory"),
        )

    before = _tree_snapshot(tmp_path)
    for filter_names in ([name, "valid"], ["valid", name]):
        with pytest.raises(ValueError, match="invalid skill name"):
            getattr(loader, operation)(filter_names=filter_names)

    assert reads == []
    assert _tree_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "name",
    (
        "0skill",
        "skill.with.dot",
        "skill_with_underscore",
        "a" * 64,
    ),
)
def test_named_apis_accept_canonical_identifier_boundaries(
    loader: SkillsLoader,
    name: str,
) -> None:
    created = loader.create_skill(name, _SKILL_MD.format(name=name))

    assert created["name"] == name
    assert loader.get_skill(name) is not None


def _write_discovered_skill(path: Path, marker: str, *, always: bool) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\n"
        "name: fixture\n"
        "description: discovered fixture\n"
        "metadata:\n"
        "  openclaw:\n"
        f"    always: {str(always).lower()}\n"
        "---\n\n"
        f"{marker}\n",
        encoding="utf-8",
    )


def test_discovery_skips_noncanonical_directory_names_for_all_load_paths(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    workspace = tmp_path / "workspace"
    (workspace / "skills").mkdir(parents=True)
    _write_discovered_skill(builtin / "valid-skill", "VALID_MARKER", always=True)
    _write_discovered_skill(builtin / "bad\nbuiltin", "INVALID_BUILTIN", always=True)
    _write_discovered_skill(user / "Uppercase", "INVALID_USER", always=True)
    _write_discovered_skill(workspace / "skills" / "技能", "INVALID_WORKSPACE", always=True)
    loader = SkillsLoader(
        builtin_dir=builtin,
        user_dir=user,
        workspace_dir=workspace,
    )
    caplog.set_level(logging.WARNING, logger="tianshu.skills.loader")

    metadata = loader.list_all_metadata()
    index = loader.load_index()
    loaded = loader.load_all()
    always = loader.load_always()

    assert {item["name"] for item in metadata} == {"valid-skill"}
    assert "valid-skill" in index
    assert "VALID_MARKER" in loaded
    assert "VALID_MARKER" in always
    for marker in ("INVALID_BUILTIN", "INVALID_USER", "INVALID_WORKSPACE"):
        assert marker not in index
        assert marker not in loaded
        assert marker not in always
    assert "bad\nbuiltin" not in caplog.text
    assert "bad\\nbuiltin" in caplog.text


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
