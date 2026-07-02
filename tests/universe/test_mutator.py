import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tianshu.universe.mutator import apply_mutation, parse_persona_target
from tianshu.universe.store import UniverseStore


@pytest.mark.parametrize(
    "target,expected",
    [
        ("persona:bingbu/ROLE.md", ("bingbu", "ROLE.md")),
        ("persona:neige/SOUL.md", ("neige", "SOUL.md")),
        ("persona:bingbu/MEMORY.md", None),  # not in whitelist
        ("persona:../etc/ROLE.md", None),  # traversal
        ("persona:bingbu", None),  # no file part
        ("policy", None),  # non-persona
        ("", None),
        (None, None),
    ],
)
def test_parse_persona_target(target, expected):
    assert parse_persona_target(target) == expected


def _store(tmp_path: Path) -> UniverseStore:
    (p := tmp_path / "u" / "child" / "personas" / "bingbu").mkdir(parents=True)
    (p / "ROLE.md").write_text("原始职责：被动执行")
    return UniverseStore(tmp_path / "u", tmp_path / "lp", tmp_path / "ls")


def _llm(content: str) -> AsyncMock:
    m = AsyncMock()
    m.chat.return_value = type("R", (), {"content": content})()
    return m


def test_apply_rewrites_persona_file(tmp_path):
    store = _store(tmp_path)
    r = asyncio.run(
        apply_mutation(
            store,
            "child",
            {"target": "persona:bingbu/ROLE.md", "reason": "更主动"},
            _llm("新职责：主动协同"),
        )
    )
    assert r["applied"] is True
    assert (store.personas_dir("child") / "bingbu" / "ROLE.md").read_text() == "新职责：主动协同"


def test_non_persona_target_no_op(tmp_path):
    store = _store(tmp_path)
    r = asyncio.run(apply_mutation(store, "child", {"target": "policy", "reason": "x"}, _llm("y")))
    assert r["applied"] is False
    assert (store.personas_dir("child") / "bingbu" / "ROLE.md").read_text() == "原始职责：被动执行"


def test_missing_file_no_op(tmp_path):
    store = _store(tmp_path)
    r = asyncio.run(
        apply_mutation(
            store, "child", {"target": "persona:bingbu/SOUL.md", "reason": "x"}, _llm("y")
        )
    )
    assert r["applied"] is False and "not found" in r["detail"]


def test_llm_failure_leaves_file_unchanged(tmp_path):
    store = _store(tmp_path)
    bad = AsyncMock()
    bad.chat.side_effect = RuntimeError("boom")
    r = asyncio.run(
        apply_mutation(store, "child", {"target": "persona:bingbu/ROLE.md", "reason": "x"}, bad)
    )
    assert r["applied"] is False
    assert (store.personas_dir("child") / "bingbu" / "ROLE.md").read_text() == "原始职责：被动执行"


def test_no_change_not_applied(tmp_path):
    store = _store(tmp_path)
    r = asyncio.run(
        apply_mutation(
            store,
            "child",
            {"target": "persona:bingbu/ROLE.md", "reason": "x"},
            _llm("原始职责：被动执行"),
        )
    )
    assert r["applied"] is False


def test_oversized_rewrite_rejected(tmp_path):
    store = _store(tmp_path)
    huge = "x" * (64 * 1024 + 1)
    r = asyncio.run(
        apply_mutation(
            store, "child", {"target": "persona:bingbu/ROLE.md", "reason": "x"}, _llm(huge)
        )
    )
    assert r["applied"] is False


def test_code_fence_is_stripped(tmp_path):
    store = _store(tmp_path)
    r = asyncio.run(
        apply_mutation(
            store,
            "child",
            {"target": "persona:bingbu/ROLE.md", "reason": "x"},
            _llm("```markdown\n新职责内容\n```"),
        )
    )
    assert r["applied"] is True
    assert (store.personas_dir("child") / "bingbu" / "ROLE.md").read_text() == "新职责内容"
