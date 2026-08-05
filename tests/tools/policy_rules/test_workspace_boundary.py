"""WorkspaceBoundaryRule——越界放行的红队用例。

核心锚点：`allowed_paths` 的 glob 语义。相对 glob（`**/*`）只描述工作区内的
路径，不得放行界外目标；能授权界外的只有绝对 glob。此前用 `Path.match` 判定，
而它只比对路径尾部，`Path("/etc/passwd").match("**/*")` 恒为 True——两个内置
模板（refactor-in-place / trusted-automation）的 allowed_paths 正是 `("**/*",)`，
于是越界防护被完全绕过。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tianshu.tools.policy_profile import BUILTIN_TEMPLATES
from tianshu.tools.policy_rules.workspace_boundary import WorkspaceBoundaryRule
from tianshu.tools.types import ToolTier

WORKSPACE = Path("/tmp/tianshu-ws").resolve()


def _ctx(path: str, allowed: tuple[str, ...] = (), *, key: str = "path") -> SimpleNamespace:
    profile = SimpleNamespace(allowed_paths=allowed) if allowed else None
    return SimpleNamespace(
        tool_name="read_file",
        args={key: path},
        tool_tier=ToolTier.T0_READONLY,
        edict=SimpleNamespace(runtime=SimpleNamespace(policy_profile=profile)),
        memorial=None,
        workspace_root=WORKSPACE,
        iteration=0,
        recent_calls=(),
    )


async def _verdict(path: str, allowed: tuple[str, ...] = (), *, key: str = "path") -> str | None:
    decision = await WorkspaceBoundaryRule().evaluate(_ctx(path, allowed, key=key))
    return decision.verdict if decision else None


class TestInsideWorkspace:
    async def test_relative_path_abstains(self):
        # 工作区内 → 弃权（None），交后续规则裁决
        assert await _verdict("src/main.py") is None

    async def test_absolute_path_inside_abstains(self):
        assert await _verdict(f"{WORKSPACE}/src/main.py") is None

    async def test_workspace_root_itself_abstains(self):
        assert await _verdict(str(WORKSPACE)) is None


class TestOutsideWorkspace:
    async def test_absolute_outside_denied(self):
        assert await _verdict("/etc/passwd") == "deny"

    async def test_traversal_denied(self):
        assert await _verdict("../../../etc/passwd") == "deny"

    @pytest.mark.parametrize("key", ["path", "cwd", "file_path", "filename", "dir", "directory"])
    async def test_every_path_arg_key_is_checked(self, key: str):
        assert await _verdict("/etc/passwd", key=key) == "deny"


class TestAllowedPathsGlobSemantics:
    """回归：相对 glob 不得放行越界路径（2026-08-05 修复）。"""

    async def test_relative_glob_must_not_permit_escape(self):
        # `Path("/etc/passwd").match("**/*")` 为 True —— 修复前这里会放行
        assert await _verdict("/etc/passwd", ("**/*",)) == "deny"

    async def test_relative_glob_variants_must_not_permit_escape(self):
        for glob in ("**/*", "*", "**/*.py", "src/**"):
            assert await _verdict("/etc/passwd", (glob,)) == "deny", glob

    @pytest.mark.parametrize("template_name", ["refactor-in-place", "trusted-automation"])
    async def test_builtin_templates_do_not_leak(self, template_name: str):
        """两个内置模板的 allowed_paths 是 ("**/*",)，曾使任意越界路径放行。"""
        allowed = BUILTIN_TEMPLATES[template_name].allowed_paths
        assert await _verdict("/etc/passwd", allowed) == "deny"
        assert await _verdict("/root/.ssh/id_rsa", allowed) == "deny"

    async def test_absolute_glob_grants_access(self):
        # 事前声明的绝对 glob 才是授权界外路径的正当途径
        assert await _verdict("/data/shared/a.csv", ("/data/shared/**",)) is None

    async def test_absolute_glob_matches_nested_depth(self):
        # fnmatch 而非 Path.match：后者的 ** 不递归，表达不了"任意层级"
        assert await _verdict("/data/shared/x/y/z.csv", ("/data/shared/**",)) is None

    async def test_absolute_glob_does_not_overreach(self):
        assert await _verdict("/etc/passwd", ("/data/shared/**",)) == "deny"

    async def test_absolute_glob_is_not_a_prefix_match(self):
        # /data/shared-secret 不应被 /data/shared/** 命中
        assert await _verdict("/data/shared-secret/x", ("/data/shared/**",)) == "deny"
