"""Tests for Gate — static / import / test 门禁。"""

import asyncio
import os
from pathlib import Path

import pytest

from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.universe.execution import UniverseExecutionContextFactory
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


def _gate(**kwargs) -> Gate:
    return Gate(
        ExecutionGateway(),
        context_factory=UniverseExecutionContextFactory(security_mode="trusted-local"),
        **kwargs,
    )


def test_gate_passes_clean_worktree(tmp_path: Path):
    res = _gate().run(_make_worktree(tmp_path))
    assert isinstance(res, GateResult)
    assert res.passed is True
    assert res.stage == "ok"
    assert [receipt.universe_stage for receipt in res.receipts] == [
        "gate:static",
        "gate:import",
        "gate:test",
    ]


def test_gate_fails_on_syntax_error(tmp_path: Path):
    res = _gate().run(_make_worktree(tmp_path, body="def broken(:\n"))
    assert res.passed is False
    assert res.stage == "static"
    assert "SyntaxError" in res.detail
    assert res.receipts[-1].status == "failed"


def test_gate_fails_on_import_error(tmp_path: Path):
    # 语法合法但 import 期抛错 → compileall 过、import 失败
    res = _gate().run(_make_worktree(tmp_path, body="raise RuntimeError('boom')\n"))
    assert res.passed is False
    assert res.stage == "import"
    assert "RuntimeError: boom" in res.detail


def test_gate_fails_on_failing_test(tmp_path: Path):
    res = _gate().run(_make_worktree(tmp_path, test_ok=False))
    assert res.passed is False
    assert res.stage == "test"
    assert "assert 1 == 999" in res.detail


def test_gate_skips_tests_when_disabled(tmp_path: Path):
    res = _gate().run(_make_worktree(tmp_path, test_ok=False), run_tests=False)
    assert res.passed is True
    assert res.stage == "ok"


def test_gate_startup_failure_retains_terminal_receipt(tmp_path: Path):
    result = _gate(python_exe=str(tmp_path / "missing-python")).run(_make_worktree(tmp_path))

    assert result.passed is False
    assert result.stage == "static"
    assert result.receipts[-1].status == "failed"


@pytest.mark.asyncio
async def test_gate_cancellation_reaps_test_process_group(tmp_path: Path):
    wt = _make_worktree(tmp_path)
    marker = wt / "gate-process-group.txt"
    (wt / "tests" / "test_smoke.py").write_text(
        "import os,time\n"
        "from pathlib import Path\n"
        "def test_wait():\n"
        "    Path('gate-process-group.txt').write_text(str(os.getpgrp()))\n"
        "    time.sleep(60)\n"
    )
    task = asyncio.create_task(_gate(timeout_s=30).run_async(wt))
    for _ in range(300):
        if marker.exists():
            break
        await asyncio.sleep(0.01)
    assert marker.exists()
    process_group_id = int(marker.read_text())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    for _ in range(100):
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail(f"gate process group {process_group_id} survived cancellation")
