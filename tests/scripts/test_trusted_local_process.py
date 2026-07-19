from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
HELPER_PATH = ROOT / "scripts" / "_trusted_local_process.py"


def _module():
    assert HELPER_PATH.exists(), "trusted-local process helper is missing"
    spec = importlib.util.spec_from_file_location("trusted_local_process", HELPER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_preserves_argv_cwd_env_merged_output_and_nonzero_exit(tmp_path: Path) -> None:
    module = _module()
    environment = dict(os.environ)
    environment["TIANSHU_PROCESS_TEST"] = "expected"
    program = (
        "import json, os, pathlib, sys; "
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': str(pathlib.Path.cwd()), "
        "'env': os.environ['TIANSHU_PROCESS_TEST']}), flush=True); "
        "print('stderr bytes', file=sys.stderr, flush=True); "
        "raise SystemExit(7)"
    )

    result = module.run_trusted_local_process(
        [sys.executable, "-c", program, "one", "two"],
        cwd=tmp_path,
        env=environment,
    )

    stdout_lines = result.stdout.splitlines()
    assert json.loads(stdout_lines[0]) == {
        "argv": ["one", "two"],
        "cwd": str(tmp_path),
        "env": "expected",
    }
    assert stdout_lines[1] == b"stderr bytes"
    assert result.stderr == b""
    assert result.output == result.stdout
    assert result.returncode == 7


def test_runner_uses_explicit_trusted_local_host_unrestricted_backend(
    monkeypatch,
) -> None:
    module = _module()
    calls: list[dict[str, object]] = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"stdout\nstderr\n", None

    class Backend:
        async def spawn(self, **kwargs):
            calls.append(kwargs)
            spawned = SimpleNamespace(process=Process())
            kwargs["on_spawned"](spawned)
            return spawned

    monkeypatch.setattr(module, "AsyncioProcessBackend", Backend)
    monkeypatch.setenv("TIANSHU_INHERITED_PROCESS_TEST", "inherited")

    result = module.run_trusted_local_process(["tool", "arg"], cwd=Path("/tmp"))

    assert len(calls) == 1
    call = calls[0]
    assert call["argv"] == ("tool", "arg")
    assert call["cwd"] == Path("/tmp")
    assert call["env"]["TIANSHU_INHERITED_PROCESS_TEST"] == "inherited"
    assert call["network"].model_dump() == {
        "mode": "unrestricted",
        "allowed_hosts": (),
        "enforcement_required": False,
    }
    assert call["sandbox"].model_dump() == {
        "trust_level": "trusted-local",
        "mode": "host",
        "allow_host": True,
        "backend": None,
    }
    assert call["stdin_mode"] == "null"
    assert call["stderr_mode"] == "stdout"
    assert callable(call["on_spawned"])
    assert result.output == b"stdout\nstderr\n"


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
@pytest.mark.asyncio
async def test_cancellation_terminates_and_reaps_real_leader_and_child(tmp_path: Path) -> None:
    module = _module()
    pid_path = tmp_path / "processes.pid"
    child_program = "import time; time.sleep(60)"
    leader_program = (
        "import os,pathlib,subprocess,sys,time;"
        f"child=subprocess.Popen([sys.executable,'-c',{child_program!r}]);"
        f"pathlib.Path({str(pid_path)!r}).write_text(f'{{os.getpid()}} {{child.pid}}');"
        "time.sleep(60)"
    )
    task = asyncio.create_task(
        module._run_trusted_local_process(
            [sys.executable, "-c", leader_program],
            cwd=tmp_path,
            env=dict(os.environ),
        )
    )

    for _ in range(200):
        if pid_path.exists():
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pytest.fail("leader did not publish child pid")

    leader_pid, child_pid = (int(value) for value in pid_path.read_text().split())
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        for _ in range(200):
            if not _process_exists(leader_pid) and not _process_exists(child_pid):
                break
            await asyncio.sleep(0.01)
        assert not _process_exists(leader_pid)
        assert not _process_exists(child_pid)
    finally:
        if _process_exists(leader_pid) or _process_exists(child_pid):
            with contextlib.suppress(ProcessLookupError):
                os.killpg(leader_pid, signal.SIGKILL)
