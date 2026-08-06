"""执行层沙箱认 allowed_paths（issue #35 端到端缺口）。

判定层（WorkspaceBoundaryRule）与执行层（safe_path）是两道独立的墙。
tests/executor/test_policy_profile_fallback.py 只验证了判定层放行，
而真实敕令仍然失败——read_file 内部的 safe_path 不认 allowed_paths，
把已被策略放行的路径又拒了一次：

    tool.failed | read_file | Path '/…/shared-demo/report.md' is outside workspace

本测试直接调 safe_path 与真实 read_file 工具，覆盖那一层。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tianshu.kernel.ambient import bind_edict
from tianshu.models.edict import Edict, PolicyProfilePayload
from tianshu.tools.path_utils import safe_path


def _edict_allowing(*globs: str) -> Edict:
    edict = Edict(goal="读工作区外的文件")
    edict.runtime.policy_profile = PolicyProfilePayload(
        allowed_paths=list(globs), template_name="persona:smg"
    )
    return edict


class TestSafePathHonoursAllowlist:
    def test_outside_path_rejected_without_allowlist(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        outside = tmp_path / "shared" / "report.md"
        outside.parent.mkdir()
        outside.write_text("x", encoding="utf-8")

        with pytest.raises(PermissionError, match="outside workspace"):
            safe_path(workspace, str(outside))

    def test_outside_path_allowed_by_absolute_glob(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        shared = tmp_path / "shared"
        shared.mkdir()
        outside = shared / "report.md"
        outside.write_text("x", encoding="utf-8")

        with bind_edict(_edict_allowing(f"{shared.resolve()}/**")):
            assert safe_path(workspace, str(outside)) == outside.resolve()

    def test_relative_glob_still_cannot_escape(self, tmp_path: Path) -> None:
        """#34 的语义在执行层同样成立：相对 glob 授权不了界外路径。"""
        workspace = tmp_path / "ws"
        workspace.mkdir()

        with bind_edict(_edict_allowing("**/*")), pytest.raises(PermissionError):
            safe_path(workspace, "/etc/passwd")

    def test_allowlist_does_not_widen_beyond_its_glob(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        shared = tmp_path / "shared"
        shared.mkdir()

        with bind_edict(_edict_allowing(f"{shared.resolve()}/**")), pytest.raises(PermissionError):
            safe_path(workspace, "/etc/passwd")

    def test_inside_workspace_unaffected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        inside = workspace / "a.txt"
        inside.write_text("x", encoding="utf-8")

        assert safe_path(workspace, "a.txt") == inside.resolve()


class TestReadFileEndToEnd:
    """真正调用 read_file 工具——上一轮 9 个测试全绿却漏掉的那一层。"""

    @pytest.mark.asyncio
    async def test_read_file_reads_allowed_outside_file(self, tmp_path: Path) -> None:
        from tianshu.tools.builtins import register_builtins
        from tianshu.tools.registry import ToolRegistry

        workspace = tmp_path / "ws"
        workspace.mkdir()
        shared = tmp_path / "shared"
        shared.mkdir()
        target = shared / "report.md"
        target.write_text("季度报表：一切正常", encoding="utf-8")

        registry = ToolRegistry()
        register_builtins(registry, workspace_dir=str(workspace))

        with bind_edict(_edict_allowing(f"{shared.resolve()}/**")):
            result = await registry.execute("read_file", {"path": str(target)})
        assert not result.is_error, result.content
        assert "季度报表" in result.content

    @pytest.mark.asyncio
    async def test_read_file_still_denies_unauthorised_outside_file(self, tmp_path: Path) -> None:
        from tianshu.tools.builtins import register_builtins
        from tianshu.tools.registry import ToolRegistry

        workspace = tmp_path / "ws"
        workspace.mkdir()
        other = tmp_path / "secret.txt"
        other.write_text("s3cret", encoding="utf-8")

        registry = ToolRegistry()
        register_builtins(registry, workspace_dir=str(workspace))

        result = await registry.execute("read_file", {"path": str(other)})
        assert result.is_error
        assert "outside workspace" in result.content
