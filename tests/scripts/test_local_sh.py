import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest


def write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    path.chmod(0o755)


def process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process(pid: int) -> None:
    if not process_is_alive(pid):
        return

    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if not process_is_alive(pid):
            return
        time.sleep(0.01)

    if process_is_alive(pid):
        os.kill(pid, signal.SIGKILL)


def fake_bin(env: dict[str, str]) -> Path:
    return Path(env["PATH"].split(os.pathsep, maxsplit=1)[0])


def run_local(project: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    command = ["/bin/bash", str(project / "scripts" / "local.sh"), *args]
    process = subprocess.Popen(
        command,
        cwd=project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def output_pid(output: str, label: str) -> int:
    match = re.search(rf"{label} PID: (\d+)", output)
    assert match is not None, output
    return int(match.group(1))


def runtime_pids(runtime: Path) -> set[int]:
    pids: set[int] = set()
    for name in ("uvicorn.pid", "vite.pid"):
        path = runtime / name
        if path.exists():
            pids.add(int(path.read_text().strip()))
    return pids


def configure_long_running_uvicorn(project: Path) -> None:
    write_executable(
        project / ".venv" / "bin" / "uvicorn",
        """#!/usr/bin/env bash
trap 'exit 0' TERM INT
while :; do
    /bin/sleep 0.1
done
""",
    )


@pytest.fixture
def isolated_project(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    project = tmp_path / "project"
    local_sh = project / "scripts" / "local.sh"
    local_sh.parent.mkdir(parents=True)
    shutil.copy2(Path(__file__).parents[2] / "scripts" / "local.sh", local_sh)

    write_executable(
        project / ".venv" / "bin" / "uvicorn",
        """#!/usr/bin/env bash
echo "simulated startup failure"
/bin/sleep 0.05
exit 1
""",
    )
    write_executable(
        project / "web" / "node_modules" / ".bin" / "vite",
        """#!/usr/bin/env bash
trap 'exit 0' TERM INT
while :; do
    /bin/sleep 0.1
done
""",
    )

    fake_bin = tmp_path / "fake-bin"
    write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nexit 22\n")
    write_executable(fake_bin / "lsof", "#!/usr/bin/env bash\nexit 1\n")
    write_executable(
        fake_bin / "sleep",
        """#!/usr/bin/env bash
printf '%s\n' "${1:-}" >> "${TIANSHU_TEST_SLEEP_LOG:?}"
/bin/sleep 0.01
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TIANSHU_HOST": "127.0.0.1",
            "TIANSHU_PORT": "49152",
            "VITE_PORT": "49153",
            "TIANSHU_TEST_SLEEP_LOG": str(tmp_path / "sleep-calls.log"),
        }
    )
    return project, env


def test_dev_start_fails_fast_and_cleans_up_when_uvicorn_exits(
    isolated_project: tuple[Path, dict[str, str]],
) -> None:
    project, env = isolated_project
    runtime = project / ".tianshu"
    vite_pid: int | None = None

    try:
        write_executable(
            project / ".venv" / "bin" / "uvicorn",
            """#!/usr/bin/env bash
for number in $(seq 1 70); do
    echo "startup-log-${number}"
done
/bin/sleep 0.05
exit 1
""",
        )
        result = run_local(project, env, "start", "--dev")
        vite_pid = output_pid(result.stdout, "Vite")

        assert result.returncode != 0
        assert "uvicorn exited before becoming healthy" in result.stdout.lower()
        startup_log_lines = [
            line for line in result.stdout.splitlines() if line.startswith("startup-log-")
        ]
        assert 1 <= len(startup_log_lines) <= 50
        assert "startup-log-70" in startup_log_lines
        assert "startup-log-1" not in startup_log_lines
        health_sleeps = Path(env["TIANSHU_TEST_SLEEP_LOG"]).read_text().splitlines().count("0.5")
        assert health_sleeps < 10
        assert not (runtime / "uvicorn.pid").exists()
        assert not (runtime / "vite.pid").exists()
        assert not process_is_alive(vite_pid)
    finally:
        if vite_pid is None and (runtime / "vite.pid").exists():
            vite_pid = int((runtime / "vite.pid").read_text().strip())
        if vite_pid is not None:
            terminate_process(vite_pid)


def test_dev_start_timeout_cleans_up_only_started_processes(
    isolated_project: tuple[Path, dict[str, str]],
) -> None:
    project, env = isolated_project
    runtime = project / ".tianshu"
    configure_long_running_uvicorn(project)
    started_pids: list[int] = []

    try:
        result = run_local(project, env, "start", "--dev")
        started_pids = [
            output_pid(result.stdout, "Uvicorn"),
            output_pid(result.stdout, "Vite"),
        ]

        assert result.returncode != 0
        assert "backend not healthy after 30s" in result.stdout
        assert not (runtime / "uvicorn.pid").exists()
        assert not (runtime / "vite.pid").exists()
        assert all(not process_is_alive(pid) for pid in started_pids)
    finally:
        started_pids.extend(runtime_pids(runtime) - set(started_pids))
        for pid in started_pids:
            terminate_process(pid)


def test_production_failure_preserves_unrelated_vite_pid(
    isolated_project: tuple[Path, dict[str, str]],
) -> None:
    project, env = isolated_project
    runtime = project / ".tianshu"
    runtime.mkdir(parents=True)
    unrelated_vite = subprocess.Popen(["/bin/sleep", "60"])
    (runtime / "vite.pid").write_text(str(unrelated_vite.pid))

    try:
        result = run_local(project, env, "start")

        assert result.returncode != 0
        assert unrelated_vite.poll() is None
        assert (runtime / "vite.pid").read_text().strip() == str(unrelated_vite.pid)
        assert not (runtime / "uvicorn.pid").exists()
    finally:
        unrelated_vite.terminate()
        unrelated_vite.wait(timeout=5)


def test_dev_start_success_keeps_started_processes_and_pid_files(
    isolated_project: tuple[Path, dict[str, str]],
) -> None:
    project, env = isolated_project
    runtime = project / ".tianshu"
    configure_long_running_uvicorn(project)
    write_executable(fake_bin(env) / "curl", "#!/usr/bin/env bash\nexit 0\n")
    started_pids: list[int] = []

    try:
        result = run_local(project, env, "start", "--dev")
        started_pids = [
            output_pid(result.stdout, "Uvicorn"),
            output_pid(result.stdout, "Vite"),
        ]

        assert result.returncode == 0, result.stdout
        assert "Backend healthy" in result.stdout
        assert "Services started" in result.stdout
        assert int((runtime / "uvicorn.pid").read_text()) == started_pids[0]
        assert int((runtime / "vite.pid").read_text()) == started_pids[1]
        assert all(process_is_alive(pid) for pid in started_pids)
    finally:
        started_pids.extend(runtime_pids(runtime) - set(started_pids))
        for pid in started_pids:
            terminate_process(pid)


def test_failure_does_not_kill_pid_that_concurrently_replaces_uvicorn_pid_file(
    isolated_project: tuple[Path, dict[str, str]],
) -> None:
    project, env = isolated_project
    runtime = project / ".tianshu"
    unrelated_process = subprocess.Popen(["/bin/sleep", "60"])
    write_executable(
        fake_bin(env) / "curl",
        f"""#!/usr/bin/env bash
printf '%s\n' '{unrelated_process.pid}' > '{runtime / "uvicorn.pid"}'
exit 22
""",
    )
    vite_pid: int | None = None

    try:
        result = run_local(project, env, "start", "--dev")
        vite_pid = output_pid(result.stdout, "Vite")

        assert result.returncode != 0
        assert unrelated_process.poll() is None
        assert (runtime / "uvicorn.pid").read_text().strip() == str(unrelated_process.pid)
        assert not (runtime / "vite.pid").exists()
        assert not process_is_alive(vite_pid)
    finally:
        for pid in runtime_pids(runtime):
            if pid != unrelated_process.pid:
                terminate_process(pid)
        if vite_pid is not None:
            terminate_process(vite_pid)
        unrelated_process.terminate()
        unrelated_process.wait(timeout=5)


def test_live_started_process_is_stopped_but_overwritten_pid_file_is_preserved(
    isolated_project: tuple[Path, dict[str, str]],
) -> None:
    project, env = isolated_project
    runtime = project / ".tianshu"
    configure_long_running_uvicorn(project)
    unrelated_process = subprocess.Popen(["/bin/sleep", "60"])
    write_executable(
        fake_bin(env) / "curl",
        f"""#!/usr/bin/env bash
printf '%s\n' '{unrelated_process.pid}' > '{runtime / "uvicorn.pid"}'
exit 22
""",
    )
    started_pids: set[int] = set()

    try:
        result = run_local(project, env, "start", "--dev")
        started_pids = {
            output_pid(result.stdout, "Uvicorn"),
            output_pid(result.stdout, "Vite"),
        }

        assert result.returncode != 0
        assert "backend not healthy after 30s" in result.stdout
        assert unrelated_process.poll() is None
        assert (runtime / "uvicorn.pid").read_text().strip() == str(unrelated_process.pid)
        assert all(not process_is_alive(pid) for pid in started_pids)
        assert not (runtime / "vite.pid").exists()
    finally:
        for pid in started_pids | runtime_pids(runtime):
            if pid != unrelated_process.pid:
                terminate_process(pid)
        unrelated_process.terminate()
        unrelated_process.wait(timeout=5)
