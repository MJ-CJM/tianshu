"""代码变体门禁：静态编译 + import 冒烟 + 测试，逐级 fail-fast。

关键：变体跑在 worktree 上，但本仓是 editable 安装，import tianshu 默认解析到主仓 src。
所以所有子进程都注入 PYTHONPATH=<worktree>/src（前置遮蔽）+ cwd=<worktree>，确保检的是变体代码。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateResult:
    passed: bool
    stage: str   # "static" | "import" | "test" | "ok"
    detail: str  # 失败时尾部输出；成功为 ""


class Gate:
    def __init__(self, python_exe: str | None = None, *, timeout_s: int = 900) -> None:
        self._py = python_exe or sys.executable
        self._timeout = timeout_s

    def run(self, worktree: Path, *, run_tests: bool = True) -> GateResult:
        wt = Path(worktree)
        src = wt / "src"

        rc, out = self._exec([self._py, "-m", "compileall", "-q", str(src)], wt)
        if rc != 0:
            return GateResult(False, "static", _tail(out))

        rc, out = self._exec([self._py, "-c", "import tianshu"], wt)
        if rc != 0:
            return GateResult(False, "import", _tail(out))

        if run_tests:
            rc, out = self._exec([self._py, "-m", "pytest", "-q"], wt)
            if rc != 0:
                return GateResult(False, "test", _tail(out))

        return GateResult(True, "ok", "")

    def _exec(self, cmd: list[str], wt: Path) -> tuple[int, str]:
        env = dict(os.environ)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{wt / 'src'}{os.pathsep}{existing}" if existing else str(wt / "src")
        try:
            proc = subprocess.run(
                cmd, cwd=str(wt), env=env,
                capture_output=True, text=True, timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as e:
            return 124, f"timeout after {self._timeout}s: {e}"
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _tail(text: str, n: int = 2000) -> str:
    return text[-n:] if len(text) > n else text
