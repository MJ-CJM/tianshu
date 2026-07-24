"""三方合并核心测试:base/ours/theirs 分类 + git merge-file 自动合并 + 冲突上报。

测试注入 subprocess 版 git merge-file 运行器(生产由 git_backend sanctioned 站点提供;
tests 目录豁免架构守卫的进程启动限制)。"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tianshu.executor.three_way_merge import (
    FileConflict,
    classify_three_way,
    merge_text,
    resolve_file,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git required for merge-file")


def _git_merge_file(base: str, ours: str, theirs: str) -> tuple[int, str]:
    """测试用注入运行器:git merge-file -p ours base theirs → (returncode, stdout)。"""
    with tempfile.TemporaryDirectory() as d:
        dp = Path(d)
        (dp / "base").write_text(base)
        (dp / "ours").write_text(ours)
        (dp / "theirs").write_text(theirs)
        r = subprocess.run(
            ["git", "merge-file", "-p", str(dp / "ours"), str(dp / "base"), str(dp / "theirs")],
            capture_output=True,
            text=True,
        )
        return r.returncode, r.stdout


class TestClassify:
    def test_ours_unchanged_takes_theirs(self):
        assert classify_three_way("A", "A", "B") == "take_theirs"

    def test_theirs_unchanged_keeps_ours(self):
        assert classify_three_way("A", "B", "A") == "keep_ours"

    def test_identical_skips(self):
        assert classify_three_way("A", "B", "B") == "skip"
        assert classify_three_way("A", "A", "A") == "skip"

    def test_both_changed_merges(self):
        assert classify_three_way("A", "B", "C") == "merge"


class TestMergeText:
    def test_non_overlapping_changes_auto_merge(self):
        base = "line1\nline2\nline3\n"
        ours = "line1-mod\nline2\nline3\n"  # 改第一行
        theirs = "line1\nline2\nline3-mod\n"  # 改第三行
        out = merge_text(base, ours, theirs, git_merge_file=_git_merge_file)
        assert out.conflict is False
        assert "line1-mod" in out.merged and "line3-mod" in out.merged

    def test_overlapping_changes_conflict(self):
        base = "shared\n"
        ours = "ours-version\n"
        theirs = "theirs-version\n"
        out = merge_text(base, ours, theirs, git_merge_file=_git_merge_file)
        assert out.conflict is True


class TestResolveFile:
    def test_take_theirs(self):
        content, conflict = resolve_file("f", base="A", ours="A", theirs="B", git_merge_file=_git_merge_file)
        assert content == "B" and conflict is None

    def test_keep_ours_returns_skip(self):
        content, conflict = resolve_file("f", base="A", ours="B", theirs="A", git_merge_file=_git_merge_file)
        assert content is None and conflict is None

    def test_clean_merge_lands(self):
        content, conflict = resolve_file(
            "f", base="l1\nl2\nl3\n", ours="l1x\nl2\nl3\n", theirs="l1\nl2\nl3x\n", git_merge_file=_git_merge_file
        )
        assert conflict is None and "l1x" in content and "l3x" in content

    def test_overlap_returns_structured_conflict(self):
        content, conflict = resolve_file("f", base="s\n", ours="o\n", theirs="t\n", git_merge_file=_git_merge_file)
        assert content is None
        assert isinstance(conflict, FileConflict)
        assert conflict.path == "f" and conflict.ours == "o\n" and conflict.theirs == "t\n"

    def test_add_add_asymmetric_is_conflict(self):
        # 双方都新增(base None)且内容不同 → 交人工裁决
        content, conflict = resolve_file("f", base=None, ours="o\n", theirs="t\n", git_merge_file=_git_merge_file)
        assert content is None and isinstance(conflict, FileConflict)
