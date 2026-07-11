"""launcher — 按 deploy 指针决定从哪份代码启动 app，然后 exec uvicorn。

current 指向变体 worktree 时：PYTHONPATH 前置该 worktree/src + cwd 切到 worktree（遮蔽主仓 editable 安装）。
current 为空（主仓）时：直接用主仓启动。这是让 current_ref 生效的引导层。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tianshu.universe.deployer import DeployPointer

DEFAULT_POINTER = Path("~/.tianshu/universes/deploy_ptr.json").expanduser()
SECURITY_BOUNDARY_VERSION = 1
SECURITY_MANIFEST = ".tianshu-security.json"


class SecurityBoundaryError(RuntimeError):
    """A selected secure-remote variant cannot prove boundary compatibility."""


def _verify_security_manifest(worktree: Path) -> None:
    manifest = worktree / SECURITY_MANIFEST
    try:
        if manifest.is_symlink() or not manifest.is_file():
            raise SecurityBoundaryError("variant security manifest is missing")
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecurityBoundaryError("variant security manifest is invalid") from exc
    version = payload.get("security_boundary_version") if isinstance(payload, dict) else None
    if type(version) is not int or version < SECURITY_BOUNDARY_VERSION:
        raise SecurityBoundaryError("variant security manifest is incompatible")


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
        _verify_security_manifest(wt)
    src = str(wt / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src}{os.pathsep}{existing}" if existing else src
    return str(wt), env


def main() -> None:
    pointer = DeployPointer(DEFAULT_POINTER)
    cwd, env = resolve_boot_plan(pointer)
    host = env.get("TIANSHU_HOST", "127.0.0.1")
    port = env.get("TIANSHU_PORT", "8000")
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
