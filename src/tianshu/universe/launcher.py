"""launcher — 按 deploy 指针决定从哪份代码启动 app，然后 exec uvicorn。

current 指向变体 worktree 时：PYTHONPATH 前置该 worktree/src + cwd 切到 worktree（遮蔽主仓 editable 安装）。
current 为空（主仓）时：直接用主仓启动。这是让 current_ref 生效的引导层。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from tianshu.universe.deployer import DeployPointer

DEFAULT_POINTER = Path("~/.tianshu/universes/deploy_ptr.json").expanduser()


def resolve_boot_plan(pointer: DeployPointer, base_env: dict | None = None) -> tuple[str | None, dict]:
    """返回 (cwd_or_None, env)：current 指向变体则 cwd=worktree 且 PYTHONPATH 前置其 src；否则 (None, env 原样)。纯函数，可测。"""
    env = dict(base_env if base_env is not None else os.environ)
    current, _prev = pointer.read()
    if current is None or not current.worktree:
        return None, env
    wt = Path(current.worktree)
    src = str(wt / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    return str(wt), env


def main() -> None:
    pointer = DeployPointer(DEFAULT_POINTER)
    cwd, env = resolve_boot_plan(pointer)
    host = env.get("TIANSHU_HOST", "0.0.0.0")
    port = env.get("TIANSHU_PORT", "8000")
    if cwd:
        os.chdir(cwd)
    os.execvpe(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "tianshu.app:create_app",
         "--factory", "--host", host, "--port", str(port)],
        env,
    )


if __name__ == "__main__":
    main()
