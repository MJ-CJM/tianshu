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


class TestSensitivePathsAreNeverReachable:
    """凭证黑名单：workspace 与 allowed_paths 怎么配都读不到。

    workspace_dir 可在网页上改成 ~，那时 ~/.ssh 就"在工作区内"——若黑名单
    只管界外，等于没管。故黑名单先于工作区判定，且不受 allowed_paths 影响。
    """

    @pytest.mark.parametrize(
        "rel",
        [".ssh/id_rsa", ".aws/credentials", ".tianshu/tianshu.db", "proj/.env", ".gnupg/secring"],
        ids=["ssh-key", "aws-creds", "tianshu-db", "dotenv", "gnupg"],
    )
    def test_sensitive_path_denied_even_inside_workspace(self, tmp_path: Path, rel: str) -> None:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("secret", encoding="utf-8")

        # workspace 就是 tmp_path，目标在工作区**内**
        with pytest.raises(PermissionError, match="protected credential path"):
            safe_path(tmp_path, rel)

    def test_allowlist_cannot_unlock_sensitive_path(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        secrets_dir = tmp_path / ".ssh"
        secrets_dir.mkdir()

        with (
            bind_edict(_edict_allowing(f"{tmp_path.resolve()}/**")),
            pytest.raises(PermissionError, match="protected credential path"),
        ):
            safe_path(workspace, str(secrets_dir / "id_rsa"))

    def test_ordinary_dotfiles_unaffected(self, tmp_path: Path) -> None:
        """只挡凭证，不要误伤普通点文件。"""
        for name in [".gitignore", ".editorconfig", ".python-version"]:
            (tmp_path / name).write_text("x", encoding="utf-8")
            assert safe_path(tmp_path, name) == (tmp_path / name).resolve()


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
