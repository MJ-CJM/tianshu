"""WorkspaceBoundaryRule — 硬约束：越界路径直接 deny。

检查 path/cwd/file_path 类参数是否在 workspace_root 下。路径越界不走审批，
因为审批按钮本身就是诱导攻击面（攻击者可能社工用户点批准）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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

            if self._is_inside(resolved, workspace):
                continue

            # 越界 — 再查 profile 白名单
            if any(resolved.match(glob) for glob in extra_globs):
                continue

            return PolicyDecision(
                verdict="deny",
                rule_id=self.rule_id,
                reason=f"path {raw!r} resolved to {resolved} is outside workspace {workspace}",
                metadata={"arg_key": key, "resolved": str(resolved)},
            )

        return None  # 所有 path 参数都在 workspace 内 → 弃权

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
