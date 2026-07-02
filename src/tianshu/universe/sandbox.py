"""SandboxRunner — 在隔离子进程里跑一个代码变体（临时端口 + 隔离 DB + EVAL_MODE + 资源闸）。

变体 untrusted：绝不接 prod DB/端口。editable 安装下用 PYTHONPATH 前置遮蔽确保跑的是变体代码。
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxHandle:
    port: int
    base_url: str
    db_path: Path
    proc: subprocess.Popen


class SandboxError(RuntimeError):
    pass


class SandboxRunner:
    def __init__(
        self,
        python_exe: str | None = None,
        *,
        host: str = "127.0.0.1",
        startup_timeout_s: int = 60,
        mem_mb: int = 2048,
    ) -> None:
        self._py = python_exe or sys.executable
        self._host = host
        self._startup_timeout = startup_timeout_s
        self._mem_mb = mem_mb

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _build_env(self, worktree: Path, db_path: Path, port: int) -> dict:
        env = dict(os.environ)
        src = str(Path(worktree) / "src")
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
        env["TIANSHU_DB_PATH"] = str(db_path)
        env["TIANSHU_PORT"] = str(port)
        env["TIANSHU_HOST"] = self._host
        env["TIANSHU_EVAL_MODE"] = "1"
        return env

    def _preexec(self):
        try:
            import resource

            limit = self._mem_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except Exception:  # noqa: BLE001
            pass

    def start(self, worktree: Path, *, db_path: Path) -> SandboxHandle:
        """拉起变体子进程并等待 /health 健康；失败则清理并抛 SandboxError。"""
        wt = Path(worktree)
        port = self._free_port()
        env = self._build_env(wt, db_path, port)
        preexec = self._preexec if os.name == "posix" else None
        proc = subprocess.Popen(
            [
                self._py,
                "-m",
                "uvicorn",
                "tianshu.app:create_app",
                "--factory",
                "--host",
                self._host,
                "--port",
                str(port),
            ],
            cwd=str(wt),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=preexec,
        )
        base_url = f"http://{self._host}:{port}"
        if not self._await_health(proc, base_url):
            self._kill(proc)
            raise SandboxError(f"sandbox failed to become healthy on {base_url}")
        logger.info("sandbox up: %s (pid=%s, db=%s)", base_url, proc.pid, db_path)
        return SandboxHandle(port=port, base_url=base_url, db_path=Path(db_path), proc=proc)

    def _await_health(self, proc: subprocess.Popen, base_url: str) -> bool:
        deadline = time.monotonic() + self._startup_timeout
        url = f"{base_url}/health"
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                    if resp.status == 200:
                        return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        return False

    def stop(self, handle: SandboxHandle) -> None:
        self._kill(handle.proc)
        with contextlib.suppress(FileNotFoundError):
            Path(handle.db_path).unlink()

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                with contextlib.suppress(Exception):
                    proc.wait(timeout=5)

    @contextlib.contextmanager
    def session(self, worktree: Path, *, db_path: Path):
        handle = self.start(worktree, db_path=db_path)
        try:
            yield handle
        finally:
            self.stop(handle)
