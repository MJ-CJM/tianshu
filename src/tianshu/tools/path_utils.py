"""Shared path sandbox utilities.

工作区外的路径有**两道**独立的墙，二者必须用同一份放行判定，否则会漂移成
"策略放行了但工具仍拒绝"（issue #35 的端到端表现）：

1. 判定层 —— ``WorkspaceBoundaryRule``，在工具调用前给出 deny/allow
2. 执行层 —— 本模块的 ``safe_path``，被 read_file / edit_file / grep /
   list_dir / find_files / shell_exec 直接调用

放行判定收敛在 ``path_allowed_outside``，两层共用。
"""

from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path

from tianshu.kernel.ambient import get_current_edict


def path_allowed_outside(resolved: Path, globs: tuple[str, ...]) -> bool:
    """越界路径是否被 allowed_paths 显式授权。

    只认绝对 glob：相对 glob 描述的是工作区内的路径，拿它放行界外路径属于
    语义错配（也正是此前 ``**/*`` 放行 /etc/passwd 的原因）。
    用 fnmatch 而非 Path.match —— 后者在 Python 3.12 只比对路径尾部且 ``**``
    不递归，无法表达"某目录下任意层级"。
    """
    target = str(resolved)
    return any(glob.startswith("/") and fnmatch(target, glob) for glob in globs)


def ambient_allowed_globs() -> tuple[str, ...]:
    """当前敕令 runtime 上声明的 allowed_paths。

    从 ambient ContextVar 取而非走参数：``safe_path`` 的 8 个调用点都在工具
    函数内部，它们的签名由 LLM tool schema 决定，拿不到敕令。判定层
    (``WorkspaceBoundaryRule._resolve_profile_globs``) 读的是同一处
    ``edict.runtime.policy_profile``，两层取值同源。

    无敕令上下文时（CLI 直调、单测）返回空 —— 沙箱行为与本机制引入前一致。
    """
    edict = get_current_edict()
    runtime = getattr(edict, "runtime", None) if edict else None
    profile = getattr(runtime, "policy_profile", None) if runtime else None
    if profile is None:
        return ()
    return tuple(getattr(profile, "allowed_paths", ()) or ())


def safe_path(workspace: Path, path_str: str) -> Path:
    """Ensure path does not escape the workspace, unless explicitly allowed."""
    resolved = (workspace / path_str).resolve()
    workspace_resolved = workspace.resolve()
    workspace_prefix = str(workspace_resolved) + os.sep
    outside = resolved != workspace_resolved and not str(resolved).startswith(workspace_prefix)
    if outside and not path_allowed_outside(resolved, ambient_allowed_globs()):
        raise PermissionError(f"Path '{path_str}' is outside workspace")
    return resolved
