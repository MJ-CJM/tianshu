"""Sandbox lifecycle converges through ExecutionHandle without orphan groups."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tianshu.executor.execution_gateway import ExecutionGateway
from tianshu.universe.execution import UniverseExecutionContextFactory
from tianshu.universe.sandbox import SandboxError, SandboxRunner


def _worktree(tmp_path: Path, *, exit_soon: bool = False) -> Path:
    worktree = tmp_path / "worktree"
    src = worktree / "src"
    src.mkdir(parents=True)
    if exit_soon:
        body = (
            "from pathlib import Path\n"
            "import os,time\n"
            "Path('leader.pid').write_text(str(os.getpid()))\n"
            "time.sleep(0.1)\n"
        )
    else:
        body = (
            "from pathlib import Path\n"
            "import os,subprocess,sys,time\n"
            "Path('leader.pid').write_text(str(os.getpid()))\n"
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
            "Path('child.pid').write_text(str(child.pid))\n"
            "time.sleep(60)\n"
        )
    (src / "uvicorn.py").write_text(body)
    return worktree


def _runner(*, startup: float = 1, runtime: float = 5, python_exe: str | None = None):
    return SandboxRunner(
        ExecutionGateway(termination_grace_seconds=0.1),
        context_factory=UniverseExecutionContextFactory(security_mode="trusted-local"),
        python_exe=python_exe,
        startup_timeout_s=startup,
        runtime_timeout_s=runtime,
    )


async def _wait_for_file(path: Path) -> int:
    for _ in range(100):
        if path.exists():
            return int(path.read_text())
        await asyncio.sleep(0.01)
    raise AssertionError(f"process marker was not created: {path}")


async def _assert_group_gone(process_group_id: int) -> None:
    for _ in range(100):
        try:
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"process group {process_group_id} is still alive")


@pytest.mark.asyncio
async def test_normal_stop_reaps_process_group_and_cleanup_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path)
    db = tmp_path / "sandbox.db"
    db.write_text("temporary")
    runner = _runner()
    monkeypatch.setattr(runner, "_probe_health", lambda _url: True)

    handle = await runner.start(worktree, db_path=db)
    await _wait_for_file(worktree / "child.pid")
    first = await runner.stop(handle)
    second = await runner.stop(handle)

    assert first is second
    assert first.receipt.status == "cancelled"
    assert first.receipt.sandbox_enforced is False
    assert not db.exists()
    await _assert_group_gone(handle.pid)


@pytest.mark.asyncio
async def test_startup_failure_retains_terminal_receipt_and_removes_db(tmp_path: Path) -> None:
    worktree = _worktree(tmp_path)
    db = tmp_path / "sandbox.db"
    db.write_text("temporary")
    runner = _runner(python_exe=str(tmp_path / "does-not-exist"))

    with pytest.raises(SandboxError) as error:
        await runner.start(worktree, db_path=db)

    assert error.value.receipt is not None
    assert error.value.receipt.status == "failed"
    assert runner.last_receipt is error.value.receipt
    assert not db.exists()


@pytest.mark.asyncio
async def test_health_failure_reaps_process_group_and_retains_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path)
    db = tmp_path / "sandbox.db"
    db.write_text("temporary")
    runner = _runner(startup=0.15)
    monkeypatch.setattr(runner, "_probe_health", lambda _url: False)

    with pytest.raises(SandboxError, match="healthy") as error:
        await runner.start(worktree, db_path=db)

    process_group_id = await _wait_for_file(worktree / "leader.pid")
    assert error.value.receipt is not None
    assert error.value.receipt.status == "cancelled"
    assert not db.exists()
    await _assert_group_gone(process_group_id)


@pytest.mark.asyncio
async def test_runtime_timeout_reaps_group_and_keeps_timed_out_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path)
    db = tmp_path / "sandbox.db"
    db.write_text("temporary")
    runner = _runner(runtime=0.2)
    monkeypatch.setattr(runner, "_probe_health", lambda _url: True)

    handle = await runner.start(worktree, db_path=db)
    await _wait_for_file(worktree / "child.pid")
    timed_out = await handle.monitor
    stopped = await runner.stop(handle)

    assert timed_out.receipt.status == "timed_out"
    assert stopped.receipt == timed_out.receipt
    assert not db.exists()
    await _assert_group_gone(handle.pid)


@pytest.mark.asyncio
async def test_start_cancellation_reaps_group_and_removes_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path)
    db = tmp_path / "sandbox.db"
    db.write_text("temporary")
    runner = _runner(startup=5)
    monkeypatch.setattr(runner, "_probe_health", lambda _url: False)

    start_task = asyncio.create_task(runner.start(worktree, db_path=db))
    process_group_id = await _wait_for_file(worktree / "leader.pid")
    start_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start_task

    assert runner.last_receipt is not None
    assert runner.last_receipt.status == "cancelled"
    assert not db.exists()
    await _assert_group_gone(process_group_id)


@pytest.mark.asyncio
async def test_normal_process_exit_is_terminal_and_cleanup_preserves_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path, exit_soon=True)
    db = tmp_path / "sandbox.db"
    db.write_text("temporary")
    runner = _runner()
    monkeypatch.setattr(runner, "_probe_health", lambda _url: True)

    handle = await runner.start(worktree, db_path=db)
    result = await handle.monitor
    stopped = await runner.stop(handle)

    assert result.receipt.status == "succeeded"
    assert stopped.receipt == result.receipt
    assert not db.exists()
    await _assert_group_gone(handle.pid)


@pytest.mark.asyncio
async def test_runner_shutdown_reaps_all_active_sandboxes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_worktree = _worktree(tmp_path / "first")
    second_worktree = _worktree(tmp_path / "second")
    runner = _runner()
    monkeypatch.setattr(runner, "_probe_health", lambda _url: True)

    first = await runner.start(first_worktree, db_path=tmp_path / "first.db")
    second = await runner.start(second_worktree, db_path=tmp_path / "second.db")
    await runner.shutdown()

    assert first.receipt is not None
    assert second.receipt is not None
    await _assert_group_gone(first.pid)
    await _assert_group_gone(second.pid)
