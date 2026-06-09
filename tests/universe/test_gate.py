"""Tests for Gate — static / import / test 门禁。"""
from pathlib import Path

from tianshu.universe.gate import Gate, GateResult


def _make_worktree(tmp_path: Path, *, body: str = "VALUE = 1\n", test_ok: bool = True) -> Path:
    wt = tmp_path / "wt"
    (wt / "src" / "tianshu").mkdir(parents=True)
    (wt / "src" / "tianshu" / "__init__.py").write_text(body)
    (wt / "tests").mkdir()
    assertion = "assert VALUE == 1" if test_ok else "assert VALUE == 999"
    (wt / "tests" / "test_smoke.py").write_text(
        f"from tianshu import VALUE\n\ndef test_v():\n    {assertion}\n"
    )
    return wt


def test_gate_passes_clean_worktree(tmp_path: Path):
    res = Gate().run(_make_worktree(tmp_path))
    assert isinstance(res, GateResult)
    assert res.passed is True
    assert res.stage == "ok"


def test_gate_fails_on_syntax_error(tmp_path: Path):
    res = Gate().run(_make_worktree(tmp_path, body="def broken(:\n"))
    assert res.passed is False
    assert res.stage == "static"


def test_gate_fails_on_import_error(tmp_path: Path):
    # 语法合法但 import 期抛错 → compileall 过、import 失败
    res = Gate().run(_make_worktree(tmp_path, body="raise RuntimeError('boom')\n"))
    assert res.passed is False
    assert res.stage == "import"


def test_gate_fails_on_failing_test(tmp_path: Path):
    res = Gate().run(_make_worktree(tmp_path, test_ok=False))
    assert res.passed is False
    assert res.stage == "test"


def test_gate_skips_tests_when_disabled(tmp_path: Path):
    res = Gate().run(_make_worktree(tmp_path, test_ok=False), run_tests=False)
    assert res.passed is True
    assert res.stage == "ok"
