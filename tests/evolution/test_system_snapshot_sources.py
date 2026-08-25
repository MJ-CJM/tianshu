from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tianshu.executor.adapters import DelegatingExecutorAdapter, ExecutorAdapterRegistry
from tianshu.executor.capabilities import native_manifest
from tianshu.persona.loader import PersonaLoader
from tianshu.providers.registry import ModelProviderRegistry
from tianshu.skills.loader import SkillsLoader, SkillsWatcher
from tianshu.tools.policy_rules import ruleset_digest


def _write_skill(root: Path, body: str = "body") -> None:
    skill_dir = root / "sample"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: sample\n---\n" + body,
        encoding="utf-8",
    )


def test_skills_digest_uses_logical_paths_resources_and_injected_content(
    tmp_path: Path,
) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_skill(builtin, "builtin")
    _write_skill(user, "user")
    loader = SkillsLoader(builtin, user_dir=user)

    initial = loader.content_digest()
    asset = user / "sample" / "assets" / "note.txt"
    asset.parent.mkdir()
    asset.write_text("one", encoding="utf-8")
    assert loader.content_digest() == initial

    loader.invalidate_cache()
    resource_digest = loader.content_digest()
    assert resource_digest != initial

    loader.register_skill("plugin-skill", "plugin body")
    injected_digest = loader.content_digest()
    assert injected_digest != resource_digest

    loader.write_skill_file("sample", "assets/note.txt", "two")
    assert loader.content_digest() != injected_digest


def test_skills_digest_does_not_depend_on_absolute_root(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _write_skill(left, "same")
    _write_skill(right, "same")
    (left / "sample" / "references").mkdir()
    (right / "sample" / "references").mkdir()
    (left / "sample" / "references" / "ref.md").write_text("same", encoding="utf-8")
    (right / "sample" / "references" / "ref.md").write_text("same", encoding="utf-8")
    empty_workspace = tmp_path / "empty-workspace"
    (empty_workspace / "skills").mkdir(parents=True)

    expected = SkillsLoader(left).content_digest()
    assert expected == SkillsLoader(right).content_digest()
    assert expected == SkillsLoader(right, workspace_dir=empty_workspace).content_digest()


async def test_skills_watcher_resource_event_invalidates_digest_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    builtin = tmp_path / "builtin"
    _write_skill(builtin)
    resource = builtin / "sample" / "templates" / "prompt.md"
    resource.parent.mkdir()
    resource.write_text("before", encoding="utf-8")
    loader = SkillsLoader(builtin)
    initial = loader.content_digest()

    class _Observer:
        handler = None

        def schedule(self, handler, _path: str, *, recursive: bool) -> None:
            assert recursive is True
            self.handler = handler

        def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        def join(self) -> None:
            return None

    observer = _Observer()
    monkeypatch.setattr("watchdog.observers.Observer", lambda: observer)
    watcher = SkillsWatcher(loader)
    monkeypatch.setattr(watcher, "_schedule_reload", loader.invalidate_cache)
    watcher.start()

    resource.write_text("after", encoding="utf-8")
    assert observer.handler is not None
    observer.handler.on_any_event(SimpleNamespace(src_path=str(resource), dest_path=""))
    assert loader.content_digest() != initial
    watcher.stop()


class _PersonaStorage:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def list_personas(self) -> list[dict]:
        return list(self.rows)


def _persona_row() -> dict:
    return {
        "id": "official-1",
        "name": "Official",
        "department": "court",
        "title": None,
        "tools_allowed": ["read", "write"],
        "tools_denied": ["admin"],
        "allowed_paths": ["/b", "/a"],
        "workspace_dir": "",
        "skills_allowed": ["beta", "alpha"],
        "tool_tier_max": 3,
        "can_delegate": True,
        "memory_global_read": False,
        "delegates_to": ["official-3", "official-2"],
        "llm_config_name": None,
        "soul_path": "/ignored/SOUL.md",
        "role_path": "/ignored/ROLE.md",
        "created_at": "old",
        "updated_at": "old",
    }


def test_persona_digest_ignores_timestamp_path_and_list_order_noise(
    tmp_path: Path,
) -> None:
    row = _persona_row()
    storage = _PersonaStorage([row])
    runtime = tmp_path / "runtime"
    identity = runtime / "official-1"
    identity.mkdir(parents=True)
    (identity / "SOUL.md").write_text("soul", encoding="utf-8")
    (identity / "ROLE.md").write_text("role", encoding="utf-8")
    loader = PersonaLoader(tmp_path / "templates", storage, runtime)

    initial = loader.content_digest()
    row["updated_at"] = "new"
    row["soul_path"] = "/different/path"
    row["tools_allowed"] = list(reversed(row["tools_allowed"]))
    assert loader.content_digest() == initial

    (identity / "SOUL.md").write_text("changed", encoding="utf-8")
    assert loader.content_digest() != initial


class _ProviderStorage:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def list_model_providers(self) -> list[dict]:
        return list(self.rows)


def test_provider_digest_is_semantic_and_secret_free() -> None:
    row = {
        "id": "provider-1",
        "profile_id": "openai",
        "display_name": "Primary",
        "base_url": "https://example.invalid/v1",
        "api_key_ref": "credential",
        "enabled": 1,
        "api_key": "secret-one",
        "created_at": "old",
        "updated_at": "old",
    }
    storage = _ProviderStorage([row])
    registry = ModelProviderRegistry(storage, object())  # type: ignore[arg-type]

    initial = registry.content_digest()
    row["api_key"] = "secret-two"
    row["updated_at"] = "new"
    assert registry.content_digest() == initial

    row["base_url"] = "https://other.invalid/v1"
    assert registry.content_digest() != initial


def test_policy_and_executor_digest_seams_are_deterministic_and_read_only() -> None:
    assert ruleset_digest() == ruleset_digest()

    original = native_manifest()
    registry = ExecutorAdapterRegistry(
        (
            DelegatingExecutorAdapter(
                adapter_id=original.adapter_id,
                manifest=original,
                delegate=object(),
            ),
        )
    )
    digests = registry.manifest_digests()
    assert digests == {original.adapter_id: original.content_hash}
    digests[original.adapter_id] = "0" * 64
    assert registry.manifest_digests()[original.adapter_id] == original.content_hash

    replacement = original.model_copy(update={"display_name": "Replacement"})
    assert replacement.content_hash != original.content_hash
    registry.replace(
        DelegatingExecutorAdapter(
            adapter_id=replacement.adapter_id,
            manifest=replacement,
            delegate=object(),
        )
    )
    assert registry.manifest_digests()[original.adapter_id] == replacement.content_hash
