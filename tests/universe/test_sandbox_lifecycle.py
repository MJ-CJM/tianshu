"""Sandbox lifecycle converges through ExecutionHandle without orphan groups."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import pytest

from tianshu.executor.execution_gateway import ExecutionGateway, SpawnedProcess
from tianshu.universe.execution import UniverseExecutionContextFactory
from tianshu.universe.sandbox import SandboxError, SandboxRunner


def _worktree(
    tmp_path: Path,
    *,
    exit_soon: bool = False,
    detached_child_on_exit: bool = False,
) -> Path:
    worktree = tmp_path / "worktree"
    src = worktree / "src"
    src.mkdir(parents=True)
    if detached_child_on_exit:
        body = (
            "from pathlib import Path\n"
            "import os,subprocess,sys\n"
            "Path('leader.pid').write_text(str(os.getpid()))\n"
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            "Path('child.pid').write_text(str(child.pid))\n"
        )
    elif exit_soon:
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


@pytest.mark.asyncio
async def test_completed_leader_stop_reaps_descendants_without_rewriting_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path, detached_child_on_exit=True)
    runner = _runner()
    monkeypatch.setattr(runner, "_probe_health", lambda _url: True)

    handle = await runner.start(worktree, db_path=tmp_path / "sandbox.db")
    child_pid = await _wait_for_file(worktree / "child.pid")
    succeeded = await handle.monitor
    try:
        stopped = await runner.stop(handle)
        assert stopped.receipt == succeeded.receipt
        assert stopped.receipt.status == "succeeded"
        await _assert_group_gone(handle.pid)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.asyncio
async def test_cleanup_failure_stays_tracked_and_can_be_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path)
    db = tmp_path / "sandbox.db"
    db.write_text("temporary")
    runner = _runner()
    monkeypatch.setattr(runner, "_probe_health", lambda _url: True)
    handle = await runner.start(worktree, db_path=db)
    original_unlink = Path.unlink
    failed_once = False

    def flaky_unlink(path: Path, *args, **kwargs):
        nonlocal failed_once
        if path == db and not failed_once:
            failed_once = True
            raise PermissionError("database busy")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)

    with pytest.raises(PermissionError, match="database busy"):
        await runner.stop(handle)
    assert handle.pid in runner._active

    result = await runner.stop(handle)
    assert result.receipt.status == "cancelled"
    assert handle.pid not in runner._active
    assert not db.exists()


@pytest.mark.asyncio
async def test_shutdown_closes_barrier_and_cancels_in_flight_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path)
    runner = _runner()
    entered = asyncio.Event()
    release = asyncio.Event()
    original_start = runner.execution_gateway.start

    async def blocked_start(request):
        entered.set()
        await release.wait()
        return await original_start(request)

    monkeypatch.setattr(runner.execution_gateway, "start", blocked_start)
    start_task = asyncio.create_task(runner.start(worktree, db_path=tmp_path / "race.db"))
    await entered.wait()
    await runner.shutdown()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await start_task
    with pytest.raises(SandboxError, match="closing"):
        await runner.start(worktree, db_path=tmp_path / "after-close.db")
    assert runner._active == {}


@pytest.mark.asyncio
async def test_stop_and_start_failure_remove_sqlite_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worktree = _worktree(tmp_path)
    runner = _runner()
    monkeypatch.setattr(runner, "_probe_health", lambda _url: True)
    db = tmp_path / "sandbox.db"
    sidecars = (db, Path(f"{db}-wal"), Path(f"{db}-shm"))
    for path in sidecars:
        path.write_text("temporary")

    handle = await runner.start(worktree, db_path=db)
    await runner.stop(handle)
    assert all(not path.exists() for path in sidecars)

    failed_db = tmp_path / "failed.db"
    failed_sidecars = (failed_db, Path(f"{failed_db}-wal"), Path(f"{failed_db}-shm"))
    for path in failed_sidecars:
        path.write_text("temporary")
    failed_runner = _runner(python_exe=str(tmp_path / "missing-python"))

    with pytest.raises(SandboxError):
        await failed_runner.start(worktree, db_path=failed_db)
    assert all(not path.exists() for path in failed_sidecars)


@pytest.mark.asyncio
async def test_shutdown_reaps_process_published_before_backend_returns(
    tmp_path: Path,
) -> None:
    class _PublishedThenBlockedBackend:
        backend_id = "published-then-blocked"
        supports_sandbox = False
        supports_network_enforcement = False

        def __init__(self) -> None:
            self.published = asyncio.Event()
            self.spawn_call_settled = asyncio.Event()
            self.process: asyncio.subprocess.Process | None = None

        async def spawn(self, **kwargs):
            self.process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time;time.sleep(60)",
                cwd=kwargs["cwd"],
                env=kwargs["env"],
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            spawned = SpawnedProcess(
                process=self.process,
                backend_id=self.backend_id,
                network_enforced=False,
                sandbox_enforced=False,
            )
            on_spawned = kwargs.get("on_spawned")
            if on_spawned is not None:
                on_spawned(spawned)
            self.published.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.spawn_call_settled.set()
            return spawned

    backend = _PublishedThenBlockedBackend()
    gateway = ExecutionGateway(backend=backend, termination_grace_seconds=0.1)
    runner = SandboxRunner(
        gateway,
        context_factory=UniverseExecutionContextFactory(security_mode="trusted-local"),
        startup_timeout_s=1,
        runtime_timeout_s=5,
    )
    worktree = _worktree(tmp_path)
    db = tmp_path / "cancelled-acquisition.db"
    db.write_text("temporary")
    start_task = asyncio.create_task(runner.start(worktree, db_path=db))
    try:
        await asyncio.wait_for(backend.published.wait(), timeout=5)
        assert backend.process is not None
        await asyncio.wait_for(runner.shutdown(), timeout=1)

        with pytest.raises(asyncio.CancelledError) as cancelled:
            await start_task
        assert getattr(cancelled.value, "receipt", None) is not None
        assert cancelled.value.receipt.status == "cancelled"
        assert runner.last_receipt is cancelled.value.receipt
        assert backend.spawn_call_settled.is_set()
        assert not db.exists()
        await _assert_group_gone(backend.process.pid)
    finally:
        if not start_task.done():
            start_task.cancel()
            await asyncio.gather(start_task, return_exceptions=True)
        if backend.process is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(backend.process.pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError, TimeoutError):
                await asyncio.wait_for(backend.process.wait(), timeout=1)
