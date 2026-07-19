"""Authoritative live skill package snapshots are complete and fail closed."""

from __future__ import annotations

from pathlib import Path

import frontmatter
import pytest

from tianshu.skills.installer import (
    SkillPackageMember,
    SkillPackageRenderError,
    SkillPackageSnapshotError,
    render_skill_document_body,
    snapshot_skill_package,
)


def _skill(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "snapshot-skill"
    raw = "---\nname: snapshot-skill\ndescription: Snapshot\n---\n\nSafe."
    root.mkdir()
    skill_file = root / "SKILL.md"
    skill_file.write_text(raw, encoding="utf-8")
    return skill_file, raw


def test_snapshot_includes_raw_skill_and_allowed_resource_tree(tmp_path: Path) -> None:
    skill_file, raw = _skill(tmp_path)
    root = skill_file.parent
    (root / "scripts" / "nested").mkdir(parents=True)
    (root / "scripts" / "nested" / "run.py").write_text("print('safe')\n", encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes" / "private.md").write_text("not a package resource", encoding="utf-8")

    members = snapshot_skill_package(skill_file, expected_name="snapshot-skill")

    assert members == (
        SkillPackageMember(path="SKILL.md", kind="file", content=raw),
        SkillPackageMember(path="scripts", kind="directory", content=None),
        SkillPackageMember(path="scripts/nested", kind="directory", content=None),
        SkillPackageMember(path="scripts/nested/run.py", kind="file", content="print('safe')\n"),
    )


def test_snapshot_rejects_symlink_resource(tmp_path: Path) -> None:
    skill_file, _raw = _skill(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (skill_file.parent / "assets").mkdir()
    (skill_file.parent / "assets" / "escape.txt").symlink_to(outside)

    with pytest.raises(SkillPackageSnapshotError, match="symlink"):
        snapshot_skill_package(skill_file, expected_name="snapshot-skill")


def test_snapshot_rejects_oversized_resource(tmp_path: Path) -> None:
    skill_file, _raw = _skill(tmp_path)
    (skill_file.parent / "references").mkdir()
    (skill_file.parent / "references" / "large.txt").write_text("12345", encoding="utf-8")

    with pytest.raises(SkillPackageSnapshotError, match="file size"):
        snapshot_skill_package(
            skill_file,
            expected_name="snapshot-skill",
            max_file_bytes=4,
        )


def test_snapshot_rejects_illegal_resource_path(tmp_path: Path) -> None:
    skill_file, _raw = _skill(tmp_path)
    (skill_file.parent / "templates").mkdir()
    (skill_file.parent / "templates" / "bad\nname.txt").write_text("unsafe", encoding="utf-8")

    with pytest.raises(SkillPackageSnapshotError, match="path"):
        snapshot_skill_package(skill_file, expected_name="snapshot-skill")


def test_snapshot_rejects_loader_path_for_different_skill(tmp_path: Path) -> None:
    skill_file, _raw = _skill(tmp_path)

    with pytest.raises(SkillPackageSnapshotError, match="identity"):
        snapshot_skill_package(skill_file, expected_name="other-skill")


def test_render_body_preserves_trusted_metadata_and_does_not_trust_nested_frontmatter() -> None:
    raw = (
        "---\n# trusted comment\nname: snapshot-skill\ndescription: Snapshot\n"
        "metadata:\n  openclaw:\n    always: true\n---\n\nOriginal."
    )
    body = "---\nname: attacker-skill\ndescription: Forged\n---\n\nUpdated."

    rendered = render_skill_document_body(raw, body, expected_name="snapshot-skill")

    trusted = frontmatter.loads(raw)
    candidate = frontmatter.loads(rendered)
    assert candidate.metadata == trusted.metadata
    assert candidate.content == body
    trusted_header = raw.rsplit("\n\nOriginal.", maxsplit=1)[0]
    assert rendered.startswith(f"{trusted_header}\n\n")


def test_render_body_rejects_document_without_frontmatter() -> None:
    with pytest.raises(SkillPackageRenderError, match="frontmatter"):
        render_skill_document_body(
            "Original body only.",
            "Updated body.",
            expected_name="snapshot-skill",
        )
