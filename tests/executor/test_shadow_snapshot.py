"""影子快照(迭代 3.5「客卿」)——独立 GIT_DIR、精确回滚、历史保留。"""

from __future__ import annotations

import shutil

import pytest

from tianshu.executor.shadow_snapshot import ShadowSnapshot

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


@pytest.fixture
def shadow(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    root = tmp_path / "shadow"
    return ShadowSnapshot(work, "edict-1", root=root), work


class TestShadowSnapshot:
    def test_independent_git_dir_not_in_worktree(self, shadow):
        ss, work = shadow
        (work / "a.txt").write_text("v1")
        assert ss.init()
        # 关键:工作区里不出现 .git,用户版本库毫发无损
        assert not (work / ".git").exists()
        assert ss.git_dir.exists()

    def test_snapshot_and_list(self, shadow):
        ss, work = shadow
        (work / "a.txt").write_text("v1")
        ss.init()
        s1 = ss.snapshot("node-1")
        assert s1 is not None and s1.label == "node-1"
        (work / "a.txt").write_text("v2")
        s2 = ss.snapshot("node-2")
        assert s2 is not None and s2.sha != s1.sha
        snaps = ss.list_snapshots()
        assert len(snaps) == 2
        assert snaps[0].sha == s2.sha  # 最新在前

    def test_revert_precise_removes_later_files(self, shadow):
        ss, work = shadow
        (work / "a.txt").write_text("v1")
        ss.init()
        s1 = ss.snapshot("node-1")
        (work / "a.txt").write_text("v2")
        (work / "b.txt").write_text("added after s1")
        ss.snapshot("node-2")
        # revert 到 s1:a.txt 回 v1,b.txt(s1 之后新增且已 commit)必须删除
        assert ss.revert(s1.sha)
        assert (work / "a.txt").read_text() == "v1"
        assert not (work / "b.txt").exists()

    def test_revert_preserves_history_can_go_forward(self, shadow):
        ss, work = shadow
        (work / "a.txt").write_text("v1")
        ss.init()
        s1 = ss.snapshot("n1")
        (work / "a.txt").write_text("v2")
        (work / "b.txt").write_text("x")
        s2 = ss.snapshot("n2")
        ss.revert(s1.sha)
        # revert 自身留了一个新节点,s2 仍在历史里,可再向前
        assert len(ss.list_snapshots()) == 3
        assert ss.revert(s2.sha)
        assert (work / "b.txt").exists()
        assert (work / "a.txt").read_text() == "v2"

    def test_empty_snapshot_allowed(self, shadow):
        ss, work = shadow
        ss.init()
        # 无文件变更也留快照锚点(执行节点时间线)
        s = ss.snapshot("empty node")
        assert s is not None
