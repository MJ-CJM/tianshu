"""Slow exact-Wheel Lean Preview golden-demo black box."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

_ROOT = Path(__file__).resolve().parents[2]
_WHEEL_DIR = _ROOT / "dist" / "lean-preview"
_EVIDENCE_ROOT = _ROOT / "docs" / "cc-fable-v1" / "evidence" / "lean-preview"
_SCENARIO = _ROOT / "examples" / "lean-governed-evolution" / "scenario.json"
_BASE_PYTHON = _ROOT / ".venv" / "bin" / "python"
_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
_NETWORK_PROFILE = " ".join(
    (
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        '(allow network-inbound (local ip "localhost:*"))',
        '(allow network-outbound (remote ip "localhost:*"))',
    )
)


def _clean_sigterm_exit(returncode: int, shutdown_output: str) -> bool:
    if returncode == 0:
        return True
    return returncode == -signal.SIGTERM and all(
        marker in shutdown_output
        for marker in (
            "INFO:     Application shutdown complete.",
            "INFO:     Finished server process",
        )
    )


@pytest.mark.parametrize(
    ("returncode", "shutdown_output", "expected"),
    [
        (0, "", True),
        (
            -signal.SIGTERM,
            "INFO:     Application shutdown complete.\nINFO:     Finished server process [123]\n",
            True,
        ),
        (-signal.SIGTERM, "INFO:     Application shutdown complete.\n", False),
        (-signal.SIGKILL, "INFO:     Finished server process [123]\n", False),
    ],
)
def test_clean_sigterm_exit_requires_a_completed_server_shutdown(
    returncode: int, shutdown_output: str, expected: bool
) -> None:
    assert _clean_sigterm_exit(returncode, shutdown_output) is expected


def _run(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 900,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _sandboxed(argv: list[str]) -> list[str]:
    return [str(_SANDBOX_EXEC), "-p", _NETWORK_PROFILE, *argv]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_ready(proc: subprocess.Popen[str], base_url: str, log_path: Path) -> None:
    deadline = time.time() + 90
    last_error = "no response"
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"installed server exited early:\n{log_path.read_text()[-6000:]}")
        try:
            with urllib.request.urlopen(f"{base_url}/health/ready", timeout=2) as response:
                payload = json.loads(response.read())
                if payload.get("status") == "ready":
                    return
                last_error = repr(payload)
        except urllib.error.HTTPError as exc:
            last_error = exc.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
            last_error = repr(exc)
        time.sleep(0.25)
    pytest.fail(f"installed server did not become ready: {last_error}")


def _runtime_environment(home: Path, workspace: Path, venv: Path, token: str) -> dict[str, str]:
    env = {
        "PATH": f"{venv / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(home),
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
        "TIANSHU_STARTUP_PROFILE": "demo",
        "TIANSHU_DB_PATH": str(home / ".tianshu" / "tianshu.db"),
        "TIANSHU_ARTIFACT_DIR": str(home / ".tianshu" / "artifacts"),
        "TIANSHU_MEMORY_DIR": str(home / ".tianshu" / "memory"),
        "TIANSHU_RUNTIME_PERSONAS_DIR": str(home / ".tianshu" / "personas"),
        "TIANSHU_LOG_DIR": str(home / ".tianshu" / "logs"),
        "TIANSHU_PLUGINS_DIR": str(home / ".tianshu" / "plugins"),
        "TIANSHU_WORKSPACE_DIR": str(workspace),
        "TIANSHU_WORKSPACE_STAGING_ROOT": str(home / ".tianshu" / "workspaces"),
        "TIANSHU_UNIVERSE_REPO_ROOT": str(workspace),
        "TIANSHU_TELEMETRY": "off",
        "TIANSHU_AUTH_BOOTSTRAP_TOKEN_HASH": (
            "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
        ),
        "TIANSHU_EVOLUTION_ROUTING_SECRET": "lean-preview-fresh-wheel-routing-secret",
    }
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "VIRTUAL_ENV" not in env
    return env


def test_exact_wheel_golden_demo_from_fresh_home(tmp_path: Path) -> None:
    """Run one installed Wheel under a loopback-only descendant-process sandbox."""

    assert _BASE_PYTHON.is_file()
    assert _SANDBOX_EXEC.is_file(), "this retained local evidence requires macOS sandbox-exec"
    batch_id = os.environ.get("BATCH_ID", "")
    expected_source_commit = os.environ.get("TIANSHU_LEAN_WHEEL_SOURCE_COMMIT", "")
    if not batch_id and not expected_source_commit:
        pytest.skip("exact-Wheel evidence inputs were not supplied")
    assert batch_id, "BATCH_ID must be an immutable caller-supplied value"
    assert expected_source_commit, "TIANSHU_LEAN_WHEEL_SOURCE_COMMIT is mandatory"
    assert batch_id.endswith(expected_source_commit[:12])
    current_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current_commit == expected_source_commit, "Wheel source commit is not current HEAD"

    wheels = sorted(_WHEEL_DIR.glob("tianshu-*.whl"))
    assert len(wheels) == 1, f"expected exactly one prebuilt Wheel, got {wheels}"
    wheel = wheels[0].resolve()
    wheel_sha256 = _sha256(wheel)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "tianshu/lean_preview_demo.py" in names
    assert "tianshu/web/static/index.html" in names

    outside = tmp_path / "天枢 Golden Demo with spaces"
    home = outside / "fresh HOME 用户"
    workspace = outside / "workspace 工作区"
    venv = outside / "python 3.12 install"
    home.mkdir(parents=True)
    workspace.mkdir()
    assert not str(outside.resolve()).startswith(str(_ROOT.resolve()))

    created = _run(
        [str(_BASE_PYTHON), "-m", "venv", str(venv)],
        cwd=outside,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home), "PYTHONNOUSERSITE": "1"},
    )
    assert created.returncode == 0, created.stdout + created.stderr
    python = venv / "bin" / "python"
    install_env = {
        "PATH": f"{venv / 'bin'}:/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONNOUSERSITE": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_REQUIRE_VIRTUALENV": "1",
    }
    installed = _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--only-binary=:all:",
            f"tianshu[cli] @ {wheel.as_uri()}",
        ],
        cwd=outside,
        env=install_env,
        timeout=1200,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    version = _run(
        [str(python), "-c", "import sys; assert sys.version_info[:2] == (3, 12)"],
        cwd=outside,
        env=install_env,
    )
    assert version.returncode == 0, version.stderr

    token = "lean-preview-fresh-wheel-bootstrap-token"
    runtime_env = _runtime_environment(home, workspace, venv, token)
    import_probe = _run(
        [
            str(python),
            "-s",
            "-c",
            "import json,sys,tianshu;print(json.dumps({'file':tianshu.__file__,'path':sys.path}))",
        ],
        cwd=outside,
        env=runtime_env,
    )
    assert import_probe.returncode == 0, import_probe.stderr
    imported = json.loads(import_probe.stdout)
    assert "site-packages" in imported["file"]
    assert all(not str(entry).startswith(str(_ROOT)) for entry in imported["path"])

    git = shutil.which("git", path=runtime_env["PATH"])
    assert git is not None
    for argv in (
        [git, "init", "-q"],
        [git, "config", "user.email", "golden-demo@example.invalid"],
        [git, "config", "user.name", "Lean Preview"],
        [git, "add", "-A"],
        [git, "commit", "--allow-empty", "-q", "-m", "golden demo baseline"],
    ):
        result = _run(argv, cwd=workspace, env=runtime_env)
        assert result.returncode == 0, result.stdout + result.stderr

    child_network_probe = (
        "import subprocess,sys;"
        "code='import socket,sys;s=socket.socket();sys.exit(0 if s.connect_ex((\\\"93.184.216.34\\\",80)) in (1,13) else 7)';"
        "raise SystemExit(subprocess.run([sys.executable,'-c',code]).returncode)"
    )
    blocked = _run(
        _sandboxed([str(python), "-c", child_network_probe]),
        cwd=outside,
        env=runtime_env,
    )
    assert blocked.returncode == 0, "descendant process escaped the external-network denial"

    digest_command = [
        str(python),
        "-c",
        (
            "import json;from tianshu.resources import catalog;"
            "print(json.dumps(catalog.package_digest(),sort_keys=True))"
        ),
    ]
    digest_before = _run(_sandboxed(digest_command), cwd=outside, env=runtime_env)
    assert digest_before.returncode == 0, digest_before.stderr

    scenario = outside / "scenario.json"
    shutil.copyfile(_SCENARIO, scenario)
    installed_python_version = _run(
        [str(python), "-c", "import platform;print(platform.python_version())"],
        cwd=outside,
        env=runtime_env,
    )
    assert installed_python_version.returncode == 0, installed_python_version.stderr
    environment_facts = {
        "architecture": os.uname().machine,
        "dependency_lock_hash": "0" * 64,
        "platform": os.uname().sysname,
        "python_version": installed_python_version.stdout.strip(),
        "tianshu_version": "0.4.2",
        "workspace_base_revision": None,
    }
    environment_fingerprint = hashlib.sha256(
        json.dumps(environment_facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    runtime_env.update(
        {
            "TIANSHU_BOOTSTRAP_TOKEN": token,
            "TIANSHU_LEAN_SOURCE_COMMIT": expected_source_commit,
            "TIANSHU_LEAN_WHEEL_SHA256": wheel_sha256,
            "TIANSHU_LEAN_ENVIRONMENT_FINGERPRINT": environment_fingerprint,
            "TIANSHU_LEAN_FIXTURE": "false",
        }
    )

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    server_log = outside / "installed-server.log"
    proc: subprocess.Popen[str] | None = None
    log_stream = server_log.open("w", encoding="utf-8")
    shutdown_output = ""
    try:
        proc = subprocess.Popen(
            _sandboxed(
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "tianshu.app:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ]
            ),
            cwd=outside,
            env={**runtime_env, "PYTHONWARNINGS": "always::ResourceWarning"},
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        _wait_for_ready(proc, base_url, server_log)
        with urllib.request.urlopen(f"{base_url}/", timeout=5) as response:
            index = response.read()
        assert b'<div id="root">' in index
        assert b"fonts.googleapis" not in index

        demo = _run(
            _sandboxed(
                [
                    str(venv / "bin" / "tianshu-lean-demo"),
                    "--base-url",
                    base_url,
                    "--scenario",
                    str(scenario),
                    "--batch-id",
                    batch_id,
                    "--output-root",
                    str(_EVIDENCE_ROOT),
                ]
            ),
            cwd=outside,
            env=runtime_env,
            timeout=600,
        )
        assert demo.returncode == 0, demo.stdout + demo.stderr

        report_path = _EVIDENCE_ROOT / batch_id / "demo-report.json"
        artifact_root = report_path.parent / "artifacts"
        verifier = _run(
            [
                str(_BASE_PYTHON),
                str(_ROOT / "scripts" / "verify_lean_preview_evidence.py"),
                "--report",
                str(report_path),
                "--artifact-root",
                str(artifact_root),
                "--expected-source-commit",
                expected_source_commit,
                "--expected-wheel-sha256",
                wheel_sha256,
            ],
            cwd=_ROOT,
            env={
                "PATH": f"{_ROOT / '.venv' / 'bin'}:/usr/bin:/bin",
                "HOME": str(home),
                "PYTHONNOUSERSITE": "1",
            },
        )
        assert verifier.returncode == 0, verifier.stdout + verifier.stderr
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["source_commit"] == expected_source_commit
        assert report["wheel_sha256"] == wheel_sha256
        assert report["environment_fingerprint"] == environment_fingerprint
        assert report["fixture"] is False
        assert [step["status"] for step in report["steps"]] == ["passed"] * 13
        demo_artifact = workspace / "DEMO.md"
        assert demo_artifact.is_file(), "demo provider did not materialize DEMO.md"
        changed = _run([git, "status", "--porcelain"], cwd=workspace, env=runtime_env)
        assert changed.returncode == 0, changed.stderr
        assert any(line.endswith("DEMO.md") for line in changed.stdout.splitlines())
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        log_stream.close()
        if server_log.is_file():
            shutdown_output = server_log.read_text(encoding="utf-8")

    assert proc is not None and _clean_sigterm_exit(proc.returncode, shutdown_output), (
        shutdown_output[-6000:]
    )
    assert "ResourceWarning" not in shutdown_output, shutdown_output[-6000:]
    assert "unclosed" not in shutdown_output.lower(), shutdown_output[-6000:]
    with pytest.raises(ProcessLookupError):
        os.killpg(proc.pid, 0)
    with socket.socket() as probe:
        probe.settimeout(2)
        assert probe.connect_ex(("127.0.0.1", port)) != 0

    db_path = Path(runtime_env["TIANSHU_DB_PATH"])
    quick_check = _run(
        [
            str(python),
            "-c",
            (
                "import sqlite3,sys;"
                f"c=sqlite3.connect({str(db_path)!r});"
                "result=c.execute('PRAGMA quick_check').fetchone()[0];c.close();print(result)"
            ),
        ],
        cwd=outside,
        env=runtime_env,
    )
    assert quick_check.returncode == 0, quick_check.stderr
    assert quick_check.stdout.strip() == "ok"
    digest_after = _run(_sandboxed(digest_command), cwd=outside, env=runtime_env)
    assert digest_after.returncode == 0, digest_after.stderr
    assert json.loads(digest_after.stdout) == json.loads(digest_before.stdout)
    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "environment_fingerprint": environment_fingerprint,
                "network": "loopback-only across sandboxed descendant processes",
                "package_resource_digest_unchanged": True,
                "python": "3.12",
                "shutdown": "SIGTERM clean",
                "sqlite_quick_check": "ok",
                "source_commit": expected_source_commit,
                "verifier": "passed with mandatory expected source/Wheel",
                "wheel_sha256": wheel_sha256,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
