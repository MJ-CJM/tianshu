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

# 私钥、云凭证与天枢自身的凭证库。无论 workspace_dir 设成什么、allowed_paths
# 写了什么，这些一律不可读——否则 workspace 一旦配成 ~，官员读到 .env 就能
# 解开凭证库里加密的 provider key，整套事前授权失效。
#
# 这些目录整体都是凭证，可按目录名封禁。
_SENSITIVE_DIR_NAMES = frozenset({".ssh", ".aws", ".gnupg", ".password-store", ".kube", ".docker"})
# 注意 ~/.tianshu 不整体封禁：它同时是 workspaces(staging) / artifacts / memory /
# personas 的家，封了目录等于禁止官员在自己的隔离工作区里干活。真正敏感的只有
# 凭证库本身，按文件名精确封禁。
_SENSITIVE_FILE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".pypirc",
        ".npmrc",
        ".git-credentials",
        "tianshu.db",
        "tianshu.db-wal",
        "tianshu.db-shm",
    }
)
_SENSITIVE_FILE_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa")


def is_sensitive_path(resolved: Path) -> bool:
    """该路径是否属于永不可读的敏感资产。

    按路径**部件**判断而非 glob：resolve() 后符号链接已展开（macOS 的
    /etc → /private/etc），部件比对不受链接与 glob 语义影响。
    """
    if _SENSITIVE_DIR_NAMES.intersection(resolved.parts):
        return True
    name = resolved.name
    return name in _SENSITIVE_FILE_NAMES or name.startswith(_SENSITIVE_FILE_PREFIXES)


def validate_allowed_path_glob(glob: str) -> str | None:
    """校验一条 allowed_paths 条目，返回中文错误原因；None 表示合法。

    写入时就拦住，而不是等运行时静默失效——相对 glob 在判定层一律不放行，
    用户填了却不报错会以为已授权。首段通配（``/**``、``/*``）等同于取消沙箱，
    直接拒绝。
    """
    if not glob.startswith("/"):
        return "必须是绝对路径 glob（以 / 开头），相对路径不会生效"
    segments = [s for s in glob.split("/") if s]
    if not segments:
        return "不能是根目录 /，等同于取消工作区隔离"
    if any(ch in segments[0] for ch in "*?["):
        return "首段不能是通配符，会放行整个文件系统"
    if _SENSITIVE_DIR_NAMES.intersection(segments):
        return "不能指向凭证目录（.ssh/.aws 等），这些路径永不可读"
    return None


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

    # 敏感资产先于工作区判定：黑名单管界内也管界外，因为 workspace 本身
    # 可被配成 ~，那时 ~/.ssh 就"在工作区内"。
    if is_sensitive_path(resolved):
        raise PermissionError(f"Path '{path_str}' is a protected credential path")

    workspace_resolved = workspace.resolve()
    workspace_prefix = str(workspace_resolved) + os.sep
    outside = resolved != workspace_resolved and not str(resolved).startswith(workspace_prefix)
    if outside and not path_allowed_outside(resolved, ambient_allowed_globs()):
        raise PermissionError(f"Path '{path_str}' is outside workspace")
    return resolved
