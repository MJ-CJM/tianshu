from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections import OrderedDict
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

import tianshu.evolution.promotion as promotion_module
import tianshu.skills.loader as loader_module
from tests.tools.test_workspace_runtime_tools import _bound
from tianshu.executor.workspace_context import bind_workspace
from tianshu.models.canonical import canonical_sha256
from tianshu.models.evolution_candidate import CandidateKind
from tianshu.models.frozen_content import (
    FrozenContentViewsV1,
    FrozenSkillsViewV1,
    FrozenSkillV1,
    frozen_skill_digest,
    frozen_skills_view_digest,
)
from tianshu.skills.loader import (
    SkillsLoader,
    SkillsWatcher,
    bind_frozen_content_views,
    current_frozen_content_views,
)
from tianshu.tools.registry import ToolRegistry
from tianshu.tools.skill_tools import _skill_list, _skill_view, register_skill_tools


def _skill_md(description: str, body: str) -> str:
    return (
        f"---\ndescription: {description}\nmetadata:\n  openclaw:\n    always: true\n---\n{body}\n"
    )


def _write_skill(root: Path, *, description: str = "alpha", body: str = "body-a") -> Path:
    skill_dir = root / "sample"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(_skill_md(description, body), encoding="utf-8")
    return skill_file


def _replace_same_size_and_restore_mtime(path: Path, content: str) -> None:
    before = path.stat()
    replacement = path.with_suffix(".next")
    replacement.write_text(content, encoding="utf-8")
    assert replacement.stat().st_size == before.st_size
    os.replace(replacement, path)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def _views(loader: SkillsLoader) -> FrozenContentViewsV1:
    return FrozenContentViewsV1(skills=loader.freeze_view())


def test_frozen_models_are_deeply_immutable() -> None:
    metadata = {"name": "sample", "nested": {"items": ["one"]}}
    digest = frozen_skill_digest(
        content="body",
        metadata=metadata,
    )
    skill = FrozenSkillV1(digest=digest, content="body", metadata=metadata)
    load_all_entries = (("sample", "body"),)
    view = FrozenSkillsViewV1(
        source_digest="1" * 64,
        effective_digest=frozen_skills_view_digest(
            skills={"sample": skill},
            load_all_entries=load_all_entries,
        ),
        skills={"sample": skill},
        load_all_entries=load_all_entries,
    )

    with pytest.raises(TypeError, match="immutable"):
        view.skills["other"] = skill  # type: ignore[index]
    with pytest.raises(TypeError, match="immutable"):
        skill.metadata["name"] = "changed"  # type: ignore[index]
    nested = skill.metadata["nested"]
    assert isinstance(nested, dict)
    with pytest.raises(TypeError, match="immutable"):
        nested["items"] = []
    items = nested["items"]
    assert isinstance(items, list)
    with pytest.raises(TypeError, match="immutable"):
        items.append("two")


async def test_bound_view_hides_mid_run_disk_changes_across_all_read_paths(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin)
    loader = SkillsLoader(builtin)
    views = _views(loader)
    original_digest = views.skills.source_digest

    with bind_frozen_content_views(views):
        _replace_same_size_and_restore_mtime(
            skill_file,
            _skill_md("bravo", "body-b"),
        )
        loader.invalidate_cache()

        assert loader.content_digest() == original_digest
        assert loader.get_skill("sample")["content"] == "body-a"
        assert loader.list_all_metadata()[0]["description"] == "alpha"
        assert "sample: alpha" in loader.load_index()
        assert "body-a" in loader.load_always()
        assert "body-a" in loader.load_all()
        assert "body-b" not in loader.load_all()
        listed = json.loads((await _skill_list(loader)).content)
        viewed = json.loads((await _skill_view(loader, "sample")).content)
        assert listed[0]["description"] == "alpha"
        assert viewed["content"] == "body-a"

    assert loader.content_digest() != original_digest
    assert loader.get_skill("sample")["content"] == "body-b"
    assert loader.list_all_metadata()[0]["description"] == "bravo"


def test_freeze_bypasses_l1_l2_and_digest_cache_after_same_stat_replace(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin)
    loader = SkillsLoader(builtin)

    cached_digest = loader.content_digest()
    assert loader.get_skill("sample")["content"] == "body-a"
    assert loader.list_all_metadata()[0]["description"] == "alpha"
    _replace_same_size_and_restore_mtime(skill_file, _skill_md("bravo", "body-b"))

    assert loader.content_digest() == cached_digest
    assert loader.get_skill("sample")["content"] == "body-a"
    assert loader.list_all_metadata()[0]["description"] == "alpha"

    view = loader.freeze_view()
    assert view.source_digest != cached_digest
    assert view.skills["sample"].content == "body-b"
    assert view.skills["sample"].metadata["description"] == "bravo"


def test_freeze_identity_and_content_come_from_the_same_aba_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin)
    loader = SkillsLoader(builtin)
    capture = loader._capture_source_state  # noqa: SLF001
    captured_digest: list[str] = []
    calls = 0

    def _capture_during_aba():
        nonlocal calls
        calls += 1
        _replace_same_size_and_restore_mtime(skill_file, _skill_md("bravo", "body-b"))
        batch = capture()
        captured_digest.append(batch.source_digest)
        _replace_same_size_and_restore_mtime(skill_file, _skill_md("alpha", "body-a"))
        return batch

    monkeypatch.setattr(loader, "_capture_source_state", _capture_during_aba)
    view = loader.freeze_view()

    assert calls == 1
    assert skill_file.read_text(encoding="utf-8") == _skill_md("alpha", "body-a")
    assert view.source_digest == captured_digest[0]
    assert view.skills["sample"].content == "body-b"
    assert view.skills["sample"].metadata["description"] == "bravo"
    assert view.source_digest != capture().source_digest


def test_freeze_retries_when_promotion_exchanges_and_cleans_captured_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = (tmp_path / "builtin").resolve()
    skill_file = _write_skill(builtin, description="old", body="OLD")
    references = skill_file.parent / "references"
    references.mkdir()
    references.joinpath("data.txt").write_text("OLD-RESOURCE", encoding="utf-8")
    stage = builtin / ".promotion-stage-sample-test"
    stage.mkdir()
    stage.joinpath("SKILL.md").write_text(
        _skill_md("new", "NEW"),
        encoding="utf-8",
    )
    stage.joinpath("references").mkdir()
    stage.joinpath("references", "data.txt").write_text(
        "NEW-RESOURCE",
        encoding="utf-8",
    )
    loader = SkillsLoader(builtin)
    old_view = loader.freeze_view()
    promotion_module._preflight_atomic_exchange(builtin)  # noqa: SLF001
    real_read = loader_module._read_regular_file_at  # noqa: SLF001
    exchanged = False

    def exchange_after_skill_read(directory_fd: int, relative_path: str) -> bytes | None:
        nonlocal exchanged
        raw = real_read(directory_fd, relative_path)
        if relative_path == "SKILL.md" and not exchanged:
            exchanged = True
            promotion_module._atomic_exchange(stage, skill_file.parent)  # noqa: SLF001
            shutil.rmtree(stage)
        return raw

    monkeypatch.setattr(loader_module, "_read_regular_file_at", exchange_after_skill_read)
    captured_during_exchange = loader.freeze_view()
    new_view = loader.freeze_view()

    assert exchanged is True
    assert captured_during_exchange.source_digest != old_view.source_digest
    assert captured_during_exchange.skills["sample"].content == "NEW"
    assert new_view.source_digest != old_view.source_digest
    assert captured_during_exchange.source_digest == new_view.source_digest
    assert new_view.skills["sample"].content == "NEW"


def test_freeze_retries_when_files_change_in_place_mid_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin, description="old", body="OLD")
    reference = skill_file.parent / "references" / "data.txt"
    reference.parent.mkdir()
    reference.write_text("OLD-RESOURCE", encoding="utf-8")
    loader = SkillsLoader(builtin)
    old_view = loader.freeze_view()
    real_read = loader_module._read_regular_file_at  # noqa: SLF001
    updated = False

    def update_after_old_skill_read(directory_fd: int, relative_path: str) -> bytes | None:
        nonlocal updated
        raw = real_read(directory_fd, relative_path)
        if relative_path == "SKILL.md" and not updated:
            updated = True
            skill_file.write_text(_skill_md("new", "NEW"), encoding="utf-8")
            reference.write_text("NEW-RESOURCE", encoding="utf-8")
        return raw

    monkeypatch.setattr(loader_module, "_read_regular_file_at", update_after_old_skill_read)
    captured = loader.freeze_view()
    stable_new = loader.freeze_view()

    assert updated is True
    assert captured.source_digest != old_view.source_digest
    assert captured.source_digest == stable_new.source_digest
    assert captured.skills["sample"].content == "NEW"


def test_freeze_retries_when_multiple_skills_change_between_directory_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill_files: dict[str, Path] = {}
    for name in ("alpha", "beta"):
        skill_dir = builtin / name
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(_skill_md(f"old-{name}", f"OLD-{name}"), encoding="utf-8")
        skill_files[name] = skill_file
    loader = SkillsLoader(builtin)
    old_view = loader.freeze_view()
    beta_identity = skill_files["beta"].parent.stat()
    real_read = loader_module._read_regular_file_at  # noqa: SLF001
    updated = False

    def update_before_beta_read(directory_fd: int, relative_path: str) -> bytes | None:
        nonlocal updated
        directory_identity = os.fstat(directory_fd)
        if (
            relative_path == "SKILL.md"
            and directory_identity.st_dev == beta_identity.st_dev
            and directory_identity.st_ino == beta_identity.st_ino
            and not updated
        ):
            updated = True
            for name, skill_file in skill_files.items():
                skill_file.write_text(
                    _skill_md(f"new-{name}", f"NEW-{name}"),
                    encoding="utf-8",
                )
        return real_read(directory_fd, relative_path)

    monkeypatch.setattr(loader_module, "_read_regular_file_at", update_before_beta_read)
    captured = loader.freeze_view()
    stable_new = loader.freeze_view()

    assert updated is True
    assert captured.source_digest != old_view.source_digest
    assert captured.source_digest == stable_new.source_digest
    assert captured.skills["alpha"].content == "NEW-alpha"
    assert captured.skills["beta"].content == "NEW-beta"


def test_repeated_aba_mutation_cannot_publish_a_mixed_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin, description="old", body="OLD")
    reference = skill_file.parent / "references" / "data.txt"
    reference.parent.mkdir()
    reference.write_text("OLD-RESOURCE", encoding="utf-8")
    loader = SkillsLoader(builtin)
    stable_read = loader_module._read_stable_regular_file_at  # noqa: SLF001
    transitions = 0

    def repeat_aba(
        directory_fd: int,
        relative_path: str,
        *,
        logical_path: str,
    ):
        nonlocal transitions
        captured = stable_read(
            directory_fd,
            relative_path,
            logical_path=logical_path,
        )
        if captured is None:
            return None
        if relative_path == "SKILL.md":
            skill_file.write_text(_skill_md("new", "NEW"), encoding="utf-8")
            reference.write_text("NEW-RESOURCE", encoding="utf-8")
            transitions += 1
        elif relative_path == "data.txt":
            reference.write_text("OLD-RESOURCE", encoding="utf-8")
            skill_file.write_text(_skill_md("old", "OLD"), encoding="utf-8")
            transitions += 1
        return captured

    monkeypatch.setattr(loader_module, "_read_stable_regular_file_at", repeat_aba)

    with pytest.raises(RuntimeError, match="skills changed while freezing content view"):
        loader.freeze_view()

    assert transitions >= 4
    assert skill_file.read_text(encoding="utf-8") == _skill_md("old", "OLD")
    assert reference.read_text(encoding="utf-8") == "OLD-RESOURCE"


def test_unrelated_ancestor_sibling_churn_does_not_reject_stable_skill_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)
    loader = SkillsLoader(builtin)
    capture_once = loader._capture_source_state_once  # noqa: SLF001
    capture_calls = 0

    def _capture_with_unrelated_churn():
        nonlocal capture_calls
        captured = capture_once()
        capture_calls += 1
        if capture_calls % 2:
            (tmp_path / f"unrelated-{capture_calls}").write_text("noise", encoding="utf-8")
        return captured

    monkeypatch.setattr(loader, "_capture_source_state_once", _capture_with_unrelated_churn)

    frozen = loader.freeze_view()

    assert capture_calls == 2
    assert frozen.skills["sample"].content == "body-a"


def test_symlink_skill_member_fails_closed(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    external = tmp_path / "external"
    old = external / "old"
    new = external / "new"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    for name in ("alpha", "beta"):
        skill_dir = builtin / name
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").symlink_to(external / "current" / f"{name}.md")
        old.joinpath(f"{name}.md").write_text(
            _skill_md(f"old-{name}", f"OLD-{name}"),
            encoding="utf-8",
        )
        new.joinpath(f"{name}.md").write_text(
            _skill_md(f"new-{name}", f"NEW-{name}"),
            encoding="utf-8",
        )
    current = external / "current"
    current.symlink_to(old, target_is_directory=True)
    loader = SkillsLoader(builtin)

    with pytest.raises(RuntimeError, match="skills changed while freezing content view"):
        loader.freeze_view()

    assert current.resolve() == old.resolve()


def test_symlink_search_path_fails_closed(tmp_path: Path) -> None:
    external = tmp_path / "external"
    old = external / "old"
    _write_skill(old / "builtin")
    current = external / "current"
    current.symlink_to(old, target_is_directory=True)
    loader = SkillsLoader(current / "builtin")

    with pytest.raises(RuntimeError, match="skills changed while freezing content view"):
        loader.freeze_view()


def test_nested_symlink_resource_directory_fails_closed(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin)
    external = tmp_path / "external"
    external.mkdir()
    external.joinpath("data.txt").write_text("outside", encoding="utf-8")
    references = skill_file.parent / "references"
    references.mkdir()
    references.joinpath("linked").symlink_to(external, target_is_directory=True)

    with pytest.raises(RuntimeError, match="skills changed while freezing content view"):
        SkillsLoader(builtin).freeze_view()


def test_injected_registration_order_cannot_change_same_source_execution_view(
    tmp_path: Path,
) -> None:
    loader = SkillsLoader(tmp_path / "builtin", char_budget=4)
    loader.register_skill("alpha", "AAAA")
    loader.register_skill("beta", "BBBB")
    first_live = loader.load_all()
    first = loader.freeze_view()

    assert loader.unregister_skill("alpha") is True
    assert loader.unregister_skill("beta") is True
    loader.register_skill("beta", "BBBB")
    loader.register_skill("alpha", "AAAA")
    second_live = loader.load_all()
    second = loader.freeze_view()

    assert "AAAA" in first_live and "BBBB" not in first_live
    assert second_live == first_live
    assert second.source_digest == first.source_digest
    assert second.effective_digest == first.effective_digest
    assert (
        second.load_all_entries
        == first.load_all_entries
        == (
            ("alpha", "AAAA"),
            ("beta", "BBBB"),
        )
    )


def test_frozen_load_all_preserves_lower_eligible_content_when_override_fails_requirements(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_skill(builtin, body="base-body")
    user_skill = user / "sample"
    user_skill.mkdir(parents=True)
    user_skill.joinpath("SKILL.md").write_text(
        "---\ndescription: user\nmetadata:\n  openclaw:\n"
        "    requires:\n      bins: [definitely-not-installed-p7]\n---\nuser-body\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(builtin, user_dir=user)

    assert loader.get_skill("sample")["content"] == "user-body"
    assert "base-body" in loader.load_all()
    assert "user-body" not in loader.load_all()

    views = _views(loader)
    with bind_frozen_content_views(views):
        assert loader.get_skill("sample")["content"] == "user-body"
        assert "base-body" in loader.load_all()
        assert "user-body" not in loader.load_all()


def test_requirement_environment_is_part_of_frozen_source_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill_dir = builtin / "sample"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\ndescription: gated\nmetadata:\n  openclaw:\n"
        "    requires:\n      env: [TIANSHU_P7_TEST_ENV]\n---\ngated-body\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(builtin)

    monkeypatch.setenv("TIANSHU_P7_TEST_ENV", "enabled")
    eligible = loader.freeze_view()
    monkeypatch.delenv("TIANSHU_P7_TEST_ENV")
    ineligible = loader.freeze_view()

    assert eligible.source_digest != ineligible.source_digest
    assert eligible.effective_digest != ineligible.effective_digest
    assert eligible.load_all_entries == (("sample", "gated-body"),)
    assert ineligible.load_all_entries == ()


def test_runtime_overlay_requirements_preserve_lower_load_all_live_and_frozen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin, description="base", body="base-body")
    loader = SkillsLoader(builtin)
    package = {
        "state": "present",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": (
                    "---\ndescription: candidate\nmetadata:\n  openclaw:\n"
                    "    requires:\n      bins: [definitely-not-installed-p7]\n"
                    "---\ncandidate-body\n"
                ),
            }
        ],
    }
    overlay = SimpleNamespace(
        canonical_digest=canonical_sha256(package),
        kind=CandidateKind.SKILL,
        subject_key="skill:sample",
    )
    runtime = SimpleNamespace(
        validate_subject_views=lambda: None,
        overlays={"skill:skill:sample": overlay},
        payloads={"skill:skill:sample": package},
        overlay=None,
        selected_payload=None,
    )
    monkeypatch.setattr(
        "tianshu.evolution.runtime_context.current_evolution_runtime",
        lambda: runtime,
    )

    assert loader.get_skill("sample")["content"] == "candidate-body"
    assert loader.list_all_metadata() == [
        {
            "name": "sample",
            "description": "candidate",
            "source": "evolution-overlay",
            "always": False,
            "tool_tier": None,
            "path": "",
            "content_length": len("candidate-body"),
        }
    ]
    assert "base-body" in loader.load_all()
    assert "candidate-body" not in loader.load_all()

    with bind_frozen_content_views(_views(loader)):
        assert loader.get_skill("sample")["content"] == "candidate-body"
        assert "base-body" in loader.load_all()
        assert "candidate-body" not in loader.load_all()


def test_oversized_runtime_overlay_is_not_load_all_eligible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin, description="base", body="base-body")
    loader = SkillsLoader(builtin, char_budget=512 * 1024)
    oversized_body = "x" * (256 * 1024)
    package = {
        "state": "present",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": _skill_md("candidate", oversized_body),
            }
        ],
    }
    overlay = SimpleNamespace(
        canonical_digest=canonical_sha256(package),
        kind=CandidateKind.SKILL,
        subject_key="skill:sample",
    )
    runtime = SimpleNamespace(
        validate_subject_views=lambda: None,
        overlays={"skill:skill:sample": overlay},
        payloads={"skill:skill:sample": package},
        overlay=None,
        selected_payload=None,
    )
    monkeypatch.setattr(
        "tianshu.evolution.runtime_context.current_evolution_runtime",
        lambda: runtime,
    )

    assert loader.get_skill("sample")["content"] == oversized_body
    assert "base-body" in loader.load_all()
    assert oversized_body not in loader.load_all()
    with bind_frozen_content_views(_views(loader)):
        assert loader.get_skill("sample")["content"] == oversized_body
        assert "base-body" in loader.load_all()
        assert oversized_body not in loader.load_all()


def test_live_and_frozen_metadata_collapse_overrides_to_one_effective_skill(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_skill(builtin, description="builtin", body="builtin-body")
    _write_skill(user, description="user", body="user-body")
    loader = SkillsLoader(builtin, user_dir=user)
    loader.register_skill("sample", "injected-body")

    live_metadata = loader.list_all_metadata()
    live_index = loader.load_index()
    views = _views(loader)
    with bind_frozen_content_views(views):
        frozen_metadata = loader.list_all_metadata()
        frozen_index = loader.load_index()

    expected = [
        {
            "name": "sample",
            "description": "",
            "source": "injected",
            "always": False,
            "tool_tier": None,
            "path": "",
            "content_length": len("injected-body"),
        }
    ]
    assert live_metadata == frozen_metadata == expected
    assert live_index == frozen_index
    assert live_index.count("- sample:") == 1


def test_invalid_high_priority_metadata_hides_lower_entry_live_and_frozen(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_skill(builtin, description="builtin", body="builtin-body")
    user_skill = user / "sample"
    user_skill.mkdir(parents=True)
    user_skill.joinpath("SKILL.md").write_bytes(b"---\ninvalid: [\xff\n---\n")
    loader = SkillsLoader(builtin, user_dir=user)

    assert loader.get_skill("sample") is None
    assert loader.list_all_metadata() == []
    assert loader.load_index() == ""
    with bind_frozen_content_views(_views(loader)):
        assert loader.get_skill("sample") is None
        assert loader.list_all_metadata() == []
        assert loader.load_index() == ""


def test_workspace_overlay_load_all_includes_parent_fallback_live_and_frozen(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    staging = tmp_path / "staging"
    builtin.mkdir()
    staging.mkdir()
    _write_skill(user, description="user", body="only-user")
    loader = SkillsLoader(builtin, user_dir=user)
    staging_loader = loader.for_workspace_overlay(staging)

    live = staging_loader.load_all()
    with bind_frozen_content_views(_views(loader)):
        frozen = staging_loader.load_all()

    assert "only-user" in live
    assert frozen == live


def test_frozen_load_all_preserves_runtime_overlay_order_at_budget_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    for name, body in (("alpha", "AAAA"), ("beta", "BBBB")):
        skill_dir = builtin / name
        skill_dir.mkdir(parents=True)
        skill_dir.joinpath("SKILL.md").write_text(
            _skill_md(name, body),
            encoding="utf-8",
        )
    loader = SkillsLoader(builtin, char_budget=4)
    package = {
        "state": "present",
        "members": [
            {
                "path": "SKILL.md",
                "kind": "file",
                "content": _skill_md("evolved", "CCCC"),
            }
        ],
    }
    overlay = SimpleNamespace(
        canonical_digest=canonical_sha256(package),
        kind=CandidateKind.SKILL,
        subject_key="skill:alpha",
    )
    runtime = SimpleNamespace(
        validate_subject_views=lambda: None,
        overlays={"skill:skill:alpha": overlay},
        payloads={"skill:skill:alpha": package},
        overlay=None,
        selected_payload=None,
    )
    monkeypatch.setattr(
        "tianshu.evolution.runtime_context.current_evolution_runtime",
        lambda: runtime,
    )

    live = loader.load_all()
    views = _views(loader)
    with bind_frozen_content_views(views):
        frozen = loader.load_all()

    assert "CCCC" in live and "BBBB" not in live
    assert frozen == live


def test_nested_and_error_contexts_restore_previous_binding(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin)
    loader = SkillsLoader(builtin)
    outer = _views(loader)
    _replace_same_size_and_restore_mtime(skill_file, _skill_md("bravo", "body-b"))
    inner = _views(loader)

    assert current_frozen_content_views() is None
    with bind_frozen_content_views(outer):
        assert current_frozen_content_views() is outer
        with pytest.raises(RuntimeError, match="stop"), bind_frozen_content_views(inner):
            assert current_frozen_content_views() is inner
            raise RuntimeError("stop")
        assert current_frozen_content_views() is outer
    assert current_frozen_content_views() is None


async def test_cancelled_task_restores_frozen_context(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)
    views = _views(SkillsLoader(builtin))
    started = asyncio.Event()
    restored: list[object | None] = []

    async def _worker() -> None:
        try:
            with bind_frozen_content_views(views):
                started.set()
                await asyncio.Event().wait()
        finally:
            restored.append(current_frozen_content_views())

    task = asyncio.create_task(_worker())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert restored == [None]
    assert current_frozen_content_views() is None


def test_unbound_reads_stay_live_and_mutators_return_live_results(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    skill_file = _write_skill(builtin)
    user.mkdir()
    loader = SkillsLoader(builtin, user_dir=user)
    views = _views(loader)

    _replace_same_size_and_restore_mtime(skill_file, _skill_md("bravo", "body-b"))
    loader.invalidate_cache()
    assert loader.get_skill("sample")["content"] == "body-b"

    with bind_frozen_content_views(views):
        saved = loader.save_skill("sample", "body-c")
        created = loader.create_skill("created", _skill_md("newer", "body-n"))
        assert saved["content"] == "body-c"
        assert created["content"] == "body-n"
        assert loader.get_skill("sample")["content"] == "body-a"
        assert loader.get_skill("created") is None

    assert loader.get_skill("sample")["content"] == "body-c"
    assert loader.get_skill("created")["content"] == "body-n"


def test_workspace_staging_loader_stays_live_inside_frozen_run(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    staging = tmp_path / "staging"
    _write_skill(builtin)
    (staging / "skills").mkdir(parents=True)
    loader = SkillsLoader(builtin)
    staging_loader = loader.for_workspace_overlay(staging)
    views = _views(loader)

    with bind_frozen_content_views(views):
        saved = staging_loader.save_skill("sample", "staged-body")
        assert saved["content"] == "staged-body"
        assert staging_loader.get_skill("sample")["content"] == "staged-body"
        assert loader.get_skill("sample")["content"] == "body-a"


def test_workspace_staging_keeps_frozen_load_all_fallback_for_failed_requirements(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    staging = tmp_path / "staging"
    _write_skill(builtin)
    staged_skill = staging / "skills" / "sample"
    staged_skill.mkdir(parents=True)
    staged_skill.joinpath("SKILL.md").write_text(
        "---\ndescription: staged\nmetadata:\n  openclaw:\n"
        "    requires:\n      bins: [definitely-not-installed-p7]\n---\nstaged-body\n",
        encoding="utf-8",
    )
    loader = SkillsLoader(builtin)
    staging_loader = loader.for_workspace_overlay(staging)

    with bind_frozen_content_views(_views(loader)):
        assert staging_loader.get_skill("sample")["content"] == "staged-body"
        assert staging_loader.list_all_metadata()[0]["description"] == "staged"
        assert "body-a" in staging_loader.load_all()
        assert "staged-body" not in staging_loader.load_all()


async def test_registered_workspace_tools_use_staging_then_frozen_fallback(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    staging = tmp_path / "staging"
    source = tmp_path / "source"
    skill_file = _write_skill(builtin)
    source.mkdir()
    loader = SkillsLoader(builtin)
    views = _views(loader)
    registry = ToolRegistry()
    register_skill_tools(registry, loader)
    bound = _bound(staging, source)

    _replace_same_size_and_restore_mtime(skill_file, _skill_md("bravo", "body-b"))
    loader.invalidate_cache()
    with bind_frozen_content_views(views), bind_workspace(bound):
        viewed = json.loads((await registry.execute("skill_view", {"name": "sample"})).content)
        listed = json.loads((await registry.execute("skill_list", {})).content)
        assert viewed["content"] == "body-a"
        assert listed[0]["description"] == "alpha"

        _write_skill(staging / "skills", description="charlie", body="body-c")
        viewed = json.loads((await registry.execute("skill_view", {"name": "sample"})).content)
        listed = json.loads((await registry.execute("skill_list", {})).content)
        assert viewed["content"] == "body-c"
        assert listed[0]["description"] == "charlie"


async def test_registered_workspace_tools_cannot_override_frozen_injected_skill(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    staging = tmp_path / "staging"
    source = tmp_path / "source"
    builtin.mkdir()
    source.mkdir()
    loader = SkillsLoader(builtin)
    loader.register_skill("sample", "injected-body")
    views = _views(loader)
    _write_skill(staging / "skills", description="staged", body="staged-body")
    registry = ToolRegistry()
    register_skill_tools(registry, loader)
    bound = _bound(staging, source)

    with bind_frozen_content_views(views), bind_workspace(bound):
        viewed = json.loads((await registry.execute("skill_view", {"name": "sample"})).content)
        listed = json.loads((await registry.execute("skill_list", {})).content)
        staging_loader = loader.for_workspace_overlay(staging)
        assert viewed["content"] == "injected-body"
        assert listed == [
            {
                "name": "sample",
                "description": "",
                "source": "injected",
                "status": "healthy",
            }
        ]
        assert "injected-body" in staging_loader.load_all()
        assert "staged-body" not in staging_loader.load_all()


@pytest.mark.parametrize("runtime_state", ["present", "absent"])
async def test_registered_workspace_tools_preserve_frozen_runtime_overlay_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_state: str,
) -> None:
    builtin = tmp_path / "builtin"
    staging = tmp_path / "staging"
    source = tmp_path / "source"
    _write_skill(builtin)
    source.mkdir()
    _write_skill(staging / "skills", description="staged", body="staged-body")
    loader = SkillsLoader(builtin)
    package = (
        {
            "state": "present",
            "members": [
                {
                    "path": "SKILL.md",
                    "kind": "file",
                    "content": _skill_md("runtime", "runtime-body"),
                }
            ],
        }
        if runtime_state == "present"
        else {"state": "absent", "members": []}
    )
    overlay = SimpleNamespace(
        canonical_digest=canonical_sha256(package),
        kind=CandidateKind.SKILL,
        subject_key="skill:sample",
    )
    runtime = SimpleNamespace(
        validate_subject_views=lambda: None,
        overlays={"skill:skill:sample": overlay},
        payloads={"skill:skill:sample": package},
        overlay=None,
        selected_payload=None,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            "tianshu.evolution.runtime_context.current_evolution_runtime",
            lambda: runtime,
        )
        views = _views(loader)

    registry = ToolRegistry()
    register_skill_tools(registry, loader)
    bound = _bound(staging, source)
    with bind_frozen_content_views(views), bind_workspace(bound):
        viewed = await registry.execute("skill_view", {"name": "sample"})
        listed = json.loads((await registry.execute("skill_list", {})).content)
        staging_loader = loader.for_workspace_overlay(staging)
        if runtime_state == "present":
            assert json.loads(viewed.content)["content"] == "runtime-body"
            assert listed[0]["description"] == "runtime"
            assert "runtime-body" in staging_loader.load_all()
            assert "runtime-body" in staging_loader.load_always()
        else:
            assert viewed.is_error
            assert listed == []
            assert staging_loader.get_skill("sample") is None
            assert staging_loader.list_all_metadata() == []
            assert staging_loader.load_all() == ""
            assert staging_loader.load_always() == ""


def test_concurrent_invalidation_cannot_split_l1_read(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)
    loader = SkillsLoader(builtin)
    assert loader.get_skill("sample")["content"] == "body-a"

    class _BlockingCache(OrderedDict[str, dict]):
        def __init__(self, values: OrderedDict[str, dict]) -> None:
            super().__init__(values)
            self.entered = Event()
            self.release = Event()

        def __contains__(self, key: object) -> bool:
            present = super().__contains__(key)
            self.entered.set()
            assert self.release.wait(5)
            return present

    cache = _BlockingCache(loader._l1_cache)  # noqa: SLF001
    loader._l1_cache = cache  # noqa: SLF001
    results: list[dict | None] = []
    errors: list[BaseException] = []
    invalidated = Event()

    def _read() -> None:
        try:
            results.append(loader.get_skill("sample"))
        except BaseException as exc:  # noqa: BLE001 - thread assertion captures any regression
            errors.append(exc)

    def _invalidate() -> None:
        loader.invalidate_cache()
        invalidated.set()

    reader = Thread(target=_read)
    reader.start()
    assert cache.entered.wait(5)
    invalidator = Thread(target=_invalidate)
    invalidator.start()
    assert not invalidated.wait(0.05)
    cache.release.set()
    reader.join(5)
    invalidator.join(5)

    assert errors == []
    assert results[0]["content"] == "body-a"  # type: ignore[index]
    assert invalidated.is_set()


def test_concurrent_invalidation_prevents_stale_digest_repopulation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)
    loader = SkillsLoader(builtin)
    started = Event()
    release = Event()
    results: list[str] = []

    def _slow_digest() -> str:
        started.set()
        assert release.wait(5)
        return "a" * 64

    monkeypatch.setattr(loader, "_calculate_content_digest", _slow_digest)
    reader = Thread(target=lambda: results.append(loader.content_digest()))
    reader.start()
    assert started.wait(5)
    loader.invalidate_cache()
    monkeypatch.setattr(loader, "_calculate_content_digest", lambda: "b" * 64)
    release.set()
    reader.join(5)

    assert results == ["a" * 64]
    assert loader.content_digest() == "b" * 64


def test_injected_skill_changes_are_isolated_until_next_view(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    loader = SkillsLoader(builtin)
    loader.register_skill("plugin-skill", "plugin-a")
    views = _views(loader)

    with bind_frozen_content_views(views):
        assert loader.unregister_skill("plugin-skill") is True
        loader.register_skill("plugin-skill", "plugin-b")
        assert loader.get_skill("plugin-skill")["content"] == "plugin-a"
        assert "plugin-a" in loader.load_all()
        assert "plugin-b" not in loader.load_all()

    assert loader.get_skill("plugin-skill")["content"] == "plugin-b"
    assert _views(loader).skills.skills["plugin-skill"].content == "plugin-b"


async def test_absent_runtime_overlay_freezes_hidden_state_for_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)
    loader = SkillsLoader(builtin)
    package = {"state": "absent", "members": []}
    overlay = SimpleNamespace(
        canonical_digest=canonical_sha256(package),
        kind=CandidateKind.SKILL,
        subject_key="skill:sample",
    )
    runtime = SimpleNamespace(
        validate_subject_views=lambda: None,
        overlays={"skill:skill:sample": overlay},
        payloads={"skill:skill:sample": package},
        overlay=None,
        selected_payload=None,
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            "tianshu.evolution.runtime_context.current_evolution_runtime",
            lambda: runtime,
        )
        views = _views(loader)

    assert loader.get_skill("sample")["content"] == "body-a"
    with bind_frozen_content_views(views):
        assert loader.get_skill("sample") is None
        assert loader.list_all_metadata() == []
        assert json.loads((await _skill_list(loader)).content) == []
        assert (await _skill_view(loader, "sample")).is_error


async def test_selected_base_absence_reveals_lower_skill_layer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)
    loader = SkillsLoader(builtin)
    package = {"state": "absent", "members": []}
    overlay = SimpleNamespace(
        canonical_digest=canonical_sha256(package),
        kind=CandidateKind.SKILL,
        subject_key="skill:sample",
    )
    champion_ref = SimpleNamespace(artifact_digest="base")
    assignment = SimpleNamespace(
        kind=CandidateKind.SKILL,
        subject_key="skill:sample",
        champion_ref=champion_ref,
        selected_ref=champion_ref,
    )
    runtime = SimpleNamespace(
        validate_subject_views=lambda: None,
        assignments=(assignment,),
        assignment=None,
        overlays={"skill:skill:sample": overlay},
        payloads={"skill:skill:sample": package},
        overlay=None,
        selected_payload=None,
    )
    monkeypatch.setattr(
        "tianshu.evolution.runtime_context.current_evolution_runtime",
        lambda: runtime,
    )

    assert loader.get_skill("sample")["content"] == "body-a"
    assert "body-a" in loader.load_all()
    with bind_frozen_content_views(_views(loader)):
        assert loader.get_skill("sample")["content"] == "body-a"
        assert loader.list_all_metadata()[0]["source"] == "builtin"
        assert "body-a" in loader.load_all()
        assert json.loads((await _skill_view(loader, "sample")).content)["content"] == "body-a"


async def test_watcher_invalidates_only_and_reports_debounced_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin)
    loader = SkillsLoader(builtin)
    old_digest = loader.content_digest()
    changed: list[list[str]] = []

    def _unexpected_load_all(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("watcher must not eagerly load active skills")

    monkeypatch.setattr(loader, "load_all", _unexpected_load_all)
    monkeypatch.setattr(SkillsWatcher, "DEBOUNCE_SECONDS", 0)
    watcher = SkillsWatcher(loader, on_change=changed.append)
    watcher.start()

    skill_file.write_text(_skill_md("bravo", "body-b"), encoding="utf-8")
    watcher._debounced_reload([str(skill_file), str(skill_file)])
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    watcher.stop()

    assert changed == [[str(skill_file)]]
    assert loader.content_digest() != old_digest


def test_watcher_uses_polling_observer_in_every_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)

    class _Observer:
        def __init__(self) -> None:
            self.scheduled: list[tuple[str, bool]] = []
            self.started = False
            self.stopped = False
            self.joined = False

        def schedule(self, _handler: object, path: str, *, recursive: bool) -> None:
            self.scheduled.append((path, recursive))

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

        def join(self) -> None:
            self.joined = True

    observers: list[_Observer] = []

    def _new_observer() -> _Observer:
        observer = _Observer()
        observers.append(observer)
        return observer

    monkeypatch.setattr(
        "watchdog.observers.polling.PollingObserver",
        _new_observer,
    )

    for on_change in (None, lambda _paths: None):
        watcher = SkillsWatcher(SkillsLoader(builtin), on_change=on_change)
        watcher.start()
        watcher.stop()

    assert len(observers) == 2
    for observer in observers:
        assert observer.scheduled == [(str(builtin), True)]
        assert observer.started is True
        assert observer.stopped is True
        assert observer.joined is True


def test_watcher_start_failure_cleans_up_and_allows_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)

    class _Observer:
        def __init__(self, *, fail_start: bool) -> None:
            self.fail_start = fail_start
            self.started = False
            self.stopped = False
            self.joined = False

        def schedule(self, *_args: object, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            self.started = True
            if self.fail_start:
                raise RuntimeError("observer start failed")

        def stop(self) -> None:
            self.stopped = True

        def join(self) -> None:
            self.joined = True

    start_failures = [True, False]
    observers: list[_Observer] = []

    def _new_observer() -> _Observer:
        observer = _Observer(fail_start=start_failures.pop(0))
        observers.append(observer)
        return observer

    monkeypatch.setattr(
        "watchdog.observers.polling.PollingObserver",
        _new_observer,
    )
    watcher = SkillsWatcher(SkillsLoader(builtin))

    with pytest.raises(RuntimeError, match="observer start failed"):
        watcher.start()

    failed = observers[0]
    assert failed.started is True
    assert failed.stopped is True
    assert failed.joined is True
    assert watcher._running is False
    assert watcher._observer is None
    assert watcher._loop is None
    assert watcher._pending_paths == set()

    watcher.start()
    assert watcher._running is True
    watcher.stop()
    assert watcher._running is False
    assert observers[1].stopped is True
    assert observers[1].joined is True


@pytest.mark.asyncio
async def test_watcher_stop_rejects_queued_callback_from_old_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin)
    callbacks: list[list[str]] = []
    queued: list[tuple[object, tuple[object, ...]]] = []

    class _QueuedLoop:
        def call_soon_threadsafe(self, callback: object, *args: object) -> None:
            queued.append((callback, args))

    class _Observer:
        def schedule(self, *_args: object, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def join(self) -> None:
            return None

    monkeypatch.setattr(
        "watchdog.observers.polling.PollingObserver",
        _Observer,
    )
    monkeypatch.setattr(asyncio, "get_event_loop", _QueuedLoop)
    watcher = SkillsWatcher(SkillsLoader(builtin), on_change=callbacks.append)
    watcher.start()
    watcher._schedule_reload([str(skill_file)])

    assert len(queued) == 1
    watcher.stop()
    callback, args = queued.pop()
    callback(*args)  # type: ignore[operator]
    await asyncio.sleep(0)

    assert watcher._debounce_task is None
    assert callbacks == []
    assert watcher._pending_paths == set()


async def test_watcher_without_callback_preserves_legacy_reload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builtin = tmp_path / "builtin"
    skill_file = _write_skill(builtin)
    loader = SkillsLoader(builtin)
    reloads = 0
    load_all = loader.load_all

    def _load_all(*args: object, **kwargs: object) -> str:
        nonlocal reloads
        reloads += 1
        return load_all(*args, **kwargs)

    monkeypatch.setattr(loader, "load_all", _load_all)
    monkeypatch.setattr(SkillsWatcher, "DEBOUNCE_SECONDS", 0)
    watcher = SkillsWatcher(loader)
    watcher.start()
    watcher._debounced_reload([str(skill_file)])
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    watcher.stop()

    assert reloads == 1
