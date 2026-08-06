"""WorkspaceBoundaryRule — 硬约束：越界路径直接 deny。

检查 path/cwd/file_path 类参数是否在 workspace_root 下。路径越界不走审批，
因为审批按钮本身就是诱导攻击面（攻击者可能社工用户点批准）——越界只能靠
**事前**声明的 ``policy_profile.allowed_paths`` 白名单放行，见下。

allowed_paths 的 glob 语义（2026-08-05 收紧）：

- **相对 glob**（如 ``**/*``）锚定在工作区内。执行到白名单判定时已确定路径
  越界，故相对 glob **一律不放行**。
- **绝对 glob**（如 ``/data/shared/**``）才能授权工作区外的路径，用 fnmatch
  匹配整个绝对路径。

此前用 ``Path.match(glob)`` 判定，而 ``Path.match`` 只比对路径尾部：
``Path("/etc/passwd").match("**/*")`` 为 True。于是内置模板
``refactor-in-place`` / ``trusted-automation``（两者 allowed_paths 都是
``("**/*",)``，本意是"工作区内任意文件"）会放行**任意**越界路径——
``/etc/passwd``、``~/.ssh/id_rsa`` 皆可读，越界防护被完全绕过。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tianshu.tools.path_utils import is_sensitive_path, path_allowed_outside
from tianshu.tools.policy import PolicyContext, PolicyDecision

# 参数名白名单：这些字段被当作 path 处理
PATH_ARG_KEYS = ("path", "cwd", "file_path", "filename", "dir", "directory")


@dataclass
class WorkspaceBoundaryRule:
    rule_id: str = "workspace_boundary"
    priority: int = 90
    allowed_globs: tuple[str, ...] = field(default_factory=tuple)

    async def evaluate(self, ctx: PolicyContext) -> PolicyDecision | None:
        workspace = ctx.workspace_root.resolve()
        extra_globs = self._resolve_profile_globs(ctx)

        for key in PATH_ARG_KEYS:
            if key not in ctx.args:
                continue
            raw = ctx.args[key]
            if not isinstance(raw, str) or not raw:
                continue

            resolved = self._resolve(raw, workspace)

            # 敏感资产先判：黑名单管界内也管界外，且不受 allowed_paths 影响。
            # 执行层 safe_path 同样会拒，此处先行是为了让官员早知道而非试错。
            if is_sensitive_path(resolved):
                return PolicyDecision(
                    verdict="deny",
                    rule_id=self.rule_id,
                    reason=f"path {raw!r} is a protected credential path",
                    metadata={"arg_key": key, "resolved": str(resolved)},
                )

            if self._is_inside(resolved, workspace):
                continue

            # 越界 — 再查 profile 白名单（仅绝对 glob 有效，理由见模块 docstring）
            if self._allowed_outside(resolved, extra_globs):
                continue

            return PolicyDecision(
                verdict="deny",
                rule_id=self.rule_id,
                reason=f"path {raw!r} resolved to {resolved} is outside workspace {workspace}",
                metadata={"arg_key": key, "resolved": str(resolved)},
            )

        return None  # 所有 path 参数都在 workspace 内 → 弃权

    @staticmethod
    def _allowed_outside(resolved: Path, globs: tuple[str, ...]) -> bool:
        """越界路径是否被 allowed_paths 显式授权。

        判定收敛在 path_utils，与执行层沙箱 ``safe_path`` 共用同一份逻辑——
        两层若各写一份，会漂移成"策略放行了但工具仍拒绝"。
        """
        return path_allowed_outside(resolved, globs)

    @staticmethod
    def _resolve(raw: str, workspace: Path) -> Path:
        p = Path(raw)
        if not p.is_absolute():
            p = workspace / p
        try:
            return p.resolve()
        except OSError:
            return p  # 无法 resolve 时用原始 path 继续判断

    @staticmethod
    def _is_inside(child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    @staticmethod
    def _resolve_profile_globs(ctx: PolicyContext) -> tuple[str, ...]:
        """读取 edict.runtime.policy_profile.allowed_paths（Step 4 填充）。"""
        runtime = getattr(ctx.edict, "runtime", None)
        profile = getattr(runtime, "policy_profile", None) if runtime else None
        if profile is None:
            return ()
        return tuple(getattr(profile, "allowed_paths", ()) or ())
