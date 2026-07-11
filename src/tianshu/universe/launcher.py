"""launcher — 按 deploy 指针决定从哪份代码启动 app，然后 exec uvicorn。

current 指向变体 worktree 时：PYTHONPATH 前置该 worktree/src + cwd 切到 worktree（遮蔽主仓 editable 安装）。
current 为空（主仓）时：直接用主仓启动。这是让 current_ref 生效的引导层。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tianshu.config import TianshuSettings
from tianshu.universe.deployer import DeployPointer

DEFAULT_POINTER = Path("~/.tianshu/universes/deploy_ptr.json").expanduser()


class SecurityBoundaryError(RuntimeError):
    """A selected secure-remote variant cannot prove boundary compatibility."""


def resolve_boot_plan(
    pointer: DeployPointer, base_env: dict | None = None
) -> tuple[str | None, dict]:
    """返回 (cwd_or_None, env)：current 指向变体则 cwd=worktree 且 PYTHONPATH 前置其 src；否则 (None, env 原样)。纯函数，可测。"""
    env = dict(base_env if base_env is not None else os.environ)
    current, _prev = pointer.read()
    if current is None or not current.worktree:
        return None, env
    wt = Path(current.worktree)
    if env.get("TIANSHU_SECURITY_MODE", "trusted-local") == "secure-remote":
        raise SecurityBoundaryError(
            "secure-remote worktree launch is disabled until the immutable G4 boundary exists"
        )
    src = str(wt / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    return str(wt), env


def main() -> None:
    pointer = DeployPointer(DEFAULT_POINTER)
    settings = TianshuSettings()
    runtime_env = dict(os.environ)
    # Pydantic also loads .env; make that resolved mode explicit before selecting code.
    runtime_env["TIANSHU_SECURITY_MODE"] = settings.security_mode
    cwd, env = resolve_boot_plan(pointer, runtime_env)
    host = settings.host
    port = settings.port
    if cwd:
        os.chdir(cwd)
    os.execvpe(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tianshu.app:create_app",
            "--factory",
            "--host",
            host,
            "--port",
            str(port),
        ],
        env,
    )


if __name__ == "__main__":
    main()
