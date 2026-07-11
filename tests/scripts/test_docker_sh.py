from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _isolated_docker_project(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    project = tmp_path / "project"
    script = project / "scripts" / "docker.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(Path(__file__).parents[2] / "scripts" / "docker.sh", script)
    (project / ".env").write_text("", encoding="utf-8")

    call_log = tmp_path / "docker-calls.log"
    fake_bin = tmp_path / "fake-bin"
    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
printf '%s\n' "$*" >> "${TIANSHU_TEST_DOCKER_LOG:?}"
if [[ "${1:-}" == "network" && "${2:-}" == "inspect" ]]; then
  printf '%s\n' "172.17.0.1"
fi
if [[ "${1:-}" == "run" ]]; then
  printf '%s\n' "fake-container-id"
fi
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "TIANSHU_TEST_DOCKER_LOG": str(call_log),
        }
    )
    return project, env, call_log


def _run_start(project: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(project / "scripts" / "docker.sh"), "start"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_trusted_local_container_refuses_non_loopback_host_publish(tmp_path: Path) -> None:
    project, env, call_log = _isolated_docker_project(tmp_path)
    env.update(
        {
            "TIANSHU_SECURITY_MODE": "trusted-local",
            "TIANSHU_DOCKER_BIND_HOST": "0.0.0.0",
        }
    )

    result = _run_start(project, env)

    assert result.returncode != 0
    assert "loopback" in (result.stdout + result.stderr).lower()
    assert not call_log.exists()


def test_secure_remote_container_may_publish_on_explicit_remote_host(tmp_path: Path) -> None:
    project, env, call_log = _isolated_docker_project(tmp_path)
    env.update(
        {
            "TIANSHU_SECURITY_MODE": "secure-remote",
            "TIANSHU_DOCKER_BIND_HOST": "0.0.0.0",
        }
    )

    result = _run_start(project, env)

    assert result.returncode == 0
    calls = call_log.read_text(encoding="utf-8")
    assert "TIANSHU_TRUSTED_LOCAL_CONTAINER_BOUNDARY=false" in calls
    assert "-p 0.0.0.0:8000:8000" in calls


def test_trusted_local_container_passes_only_exact_bridge_gateway(tmp_path: Path) -> None:
    project, env, call_log = _isolated_docker_project(tmp_path)
    env["TIANSHU_SECURITY_MODE"] = "trusted-local"

    result = _run_start(project, env)

    assert result.returncode == 0
    calls = call_log.read_text(encoding="utf-8")
    assert "network inspect bridge" in calls
    assert "TIANSHU_TRUSTED_LOCAL_CONTAINER_GATEWAY=172.17.0.1" in calls
    assert "-p 127.0.0.1:8000:8000" in calls
