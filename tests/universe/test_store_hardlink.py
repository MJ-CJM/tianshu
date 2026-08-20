"""UniverseStore 的硬链接分流：只读资产共享 inode，可变文本必须真实拷贝。

核心不变量是**位面隔离**：改写任一位面（或 live）的文本，都不得波及其他位面。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.universe import store as store_mod
from tianshu.universe.store import UniverseStore

FONT = "canvas-design/canvas-fonts/WorkSans-Regular.ttf"
SCHEMA = "pptx/ooxml/schemas/sml.xsd"
SKILL_MD = "canvas-design/SKILL.md"


def _write(root: Path, rel: str, content: bytes | str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def live(tmp_path: Path) -> dict[str, Path]:
    personas = tmp_path / "personas"
    skills = tmp_path / "skills"
    _write(personas, "bingbu/SOUL.md", "soul-v1")
    _write(skills, FONT, b"\x00\x01FONT-BYTES")
    _write(skills, SCHEMA, "<schema/>")
    _write(skills, SKILL_MD, "skill-v1")
    return {"root": tmp_path, "personas": personas, "skills": skills}


@pytest.fixture
def store(live: dict[str, Path]) -> UniverseStore:
    return UniverseStore(live["root"] / "universes", live["personas"], live["skills"])


def _ino(path: Path) -> int:
    return path.stat().st_ino


def test_readonly_assets_share_inode_with_live(store: UniverseStore, live):
    """字体与 schema 永不变异，快照应与 live 共用同一份磁盘数据。"""
    store.snapshot_live("u1", {})

    assert _ino(store.skills_dir("u1") / FONT) == _ino(live["skills"] / FONT)
    assert _ino(store.skills_dir("u1") / SCHEMA) == _ino(live["skills"] / SCHEMA)


def test_mutable_text_is_really_copied(store: UniverseStore, live):
    """SKILL.md / SOUL.md 会被 curator 与 mutator 改写，必须各有各的 inode。"""
    store.snapshot_live("u1", {})

    assert _ino(store.skills_dir("u1") / SKILL_MD) != _ino(live["skills"] / SKILL_MD)
    assert _ino(store.personas_dir("u1") / "bingbu/SOUL.md") != _ino(
        live["personas"] / "bingbu/SOUL.md"
    )


def test_editing_one_universe_text_leaves_others_intact(store: UniverseStore, live):
    """隔离回归：改一个位面的 SKILL.md，另一个位面与 live 都不受影响。"""
    store.snapshot_live("u1", {})
    store.snapshot_live("u2", {})

    (store.skills_dir("u1") / SKILL_MD).write_text("mutated", encoding="utf-8")

    assert (store.skills_dir("u2") / SKILL_MD).read_text() == "skill-v1"
    assert (live["skills"] / SKILL_MD).read_text() == "skill-v1"


def test_branch_keeps_isolation_and_shares_assets(store: UniverseStore, live):
    """分支同样分流：资产共享、文本独立。"""
    store.snapshot_live("parent", {})
    store.branch_from("parent", "child")

    assert _ino(store.skills_dir("child") / FONT) == _ino(live["skills"] / FONT)
    assert _ino(store.skills_dir("child") / SKILL_MD) != _ino(store.skills_dir("parent") / SKILL_MD)

    (store.skills_dir("child") / SKILL_MD).write_text("child-only", encoding="utf-8")
    assert (store.skills_dir("parent") / SKILL_MD).read_text() == "skill-v1"


def test_restore_never_shares_inode_with_universe(store: UniverseStore, live):
    """live 会被人和系统随手改动，还原方向必须整份真实落盘。"""
    store.snapshot_live("u1", {})
    store.restore_to_live("u1")

    assert _ino(live["skills"] / FONT) != _ino(store.skills_dir("u1") / FONT)
    assert _ino(live["skills"] / SKILL_MD) != _ino(store.skills_dir("u1") / SKILL_MD)


def test_restored_live_edit_does_not_touch_universe(store: UniverseStore, live):
    """还原后改 live 的字体（原地覆盖），位面里的那份必须保持原样。"""
    store.snapshot_live("u1", {})
    store.restore_to_live("u1")

    (live["skills"] / FONT).write_bytes(b"REPLACED")

    assert (store.skills_dir("u1") / FONT).read_bytes() == b"\x00\x01FONT-BYTES"


def test_falls_back_to_copy_when_link_unavailable(store: UniverseStore, live, monkeypatch):
    """跨文件系统等场景 os.link 抛错，应回退拷贝而不是让快照失败。"""

    def _refuse(*_args, **_kwargs):
        raise OSError("cross-device link")

    monkeypatch.setattr(store_mod.os, "link", _refuse)

    store.snapshot_live("u1", {})

    snapshot_font = store.skills_dir("u1") / FONT
    assert snapshot_font.read_bytes() == b"\x00\x01FONT-BYTES"
    assert _ino(snapshot_font) != _ino(live["skills"] / FONT)


def test_snapshot_content_matches_live(store: UniverseStore, live):
    """无论走链接还是拷贝，内容都必须与 live 一致。"""
    store.snapshot_live("u1", {})

    for rel in (FONT, SCHEMA, SKILL_MD):
        assert (store.skills_dir("u1") / rel).read_bytes() == (live["skills"] / rel).read_bytes()
