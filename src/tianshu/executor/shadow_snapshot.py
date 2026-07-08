"""影子快照 —— 放手四保险第③条(迭代 3.5「客卿」最小版)。

问题:天枢派出去的执行器(自研 agent 或客卿 Claude Code/Codex)会改工作区文件。
"敢放手"需要一条随时可回滚的退路,但**不能碰用户自己的 .git**(用户可能有
正经版本历史,天枢无权提交/回滚)。

方案:**独立 GIT_DIR**。快照仓的 .git 放在工作区**之外**
(``~/.tianshu/shadow/<edict_id>/gitdir``),通过 ``git --git-dir=<shadow>
--work-tree=<workdir>`` 操作——工作区里不出现 .git,用户的版本库毫发无损。
每个执行节点打一次快照(add -A + commit),CLI 一键 revert 到任意快照。

最小版:只做"文件系统状态"的快照/回滚(工作区内容),不追进程/DB 状态
(完整版在迭代 5)。git 不可用时优雅降级(不阻断执行)。
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SHADOW_ROOT = Path("~/.tianshu/shadow").expanduser()
# git 环境变量:用固定身份提交,不读用户 gitconfig(避免签名/hook 干扰)
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "tianshu-shadow",
    "GIT_AUTHOR_EMAIL": "shadow@tianshu.local",
    "GIT_COMMITTER_NAME": "tianshu-shadow",
    "GIT_COMMITTER_EMAIL": "shadow@tianshu.local",
    "GIT_CONFIG_GLOBAL": "/dev/null",  # 隔离用户全局 gitconfig
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


@dataclass(frozen=True)
class Snapshot:
    sha: str
    label: str
    created_at: str


class ShadowSnapshotError(RuntimeError):
    pass


class ShadowSnapshot:
    """一个工作区的影子快照仓(独立 GIT_DIR,与工作区物理分离)。"""

    def __init__(self, work_tree: Path, edict_id: str, *, root: Path | None = None) -> None:
        self._work_tree = Path(work_tree).resolve()
        self._git_dir = (root or _SHADOW_ROOT) / edict_id / "gitdir"

    @property
    def git_dir(self) -> Path:
        return self._git_dir

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        import os

        env = {**os.environ, **_GIT_ENV}
        return subprocess.run(
            [
                "git",
                f"--git-dir={self._git_dir}",
                f"--work-tree={self._work_tree}",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=check,
            env=env,
        )

    def init(self) -> bool:
        """初始化影子仓;git 不可用或失败时返回 False(优雅降级)。"""
        try:
            self._git_dir.mkdir(parents=True, exist_ok=True)
            if not (self._git_dir / "HEAD").exists():
                self._git("init", "-q")
                # 独立仓需要自己的 excludesfile 语义;默认忽略 .git 自身即可
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            logger.warning("[shadow] init failed for %s: %s", self._work_tree, e)
            return False

    def snapshot(self, label: str) -> Snapshot | None:
        """打一次快照(add -A + commit);无变更时也提交空 commit 保留节点时间线。"""
        try:
            self._git("add", "-A")
            # --allow-empty:执行节点即便没改文件也留一个快照锚点
            self._git("commit", "-q", "--allow-empty", "-m", label)
            sha = self._git("rev-parse", "HEAD").stdout.strip()
            created = self._git("show", "-s", "--format=%cI", sha).stdout.strip()
            logger.info("[shadow] snapshot %s (%s) @ %s", sha[:10], label, self._work_tree)
            return Snapshot(sha=sha, label=label, created_at=created)
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            logger.warning("[shadow] snapshot failed: %s", e)
            return None

    def list_snapshots(self) -> list[Snapshot]:
        try:
            out = self._git("log", "--format=%H%x1f%s%x1f%cI", check=False)
            if out.returncode != 0:
                return []
            snaps: list[Snapshot] = []
            for line in out.stdout.splitlines():
                parts = line.split("\x1f")
                if len(parts) == 3:
                    snaps.append(Snapshot(sha=parts[0], label=parts[1], created_at=parts[2]))
            return snaps
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return []

    def revert(self, sha: str) -> bool:
        """把工作区恢复到某快照的**精确**状态(含删除该快照之后新增的文件)。

        `checkout <sha> -- .` 只还原快照里有的路径,快照之后 commit 进来的新文件
        仍是 tracked、clean 删不掉。正确做法:read-tree 把 index 设为目标树 →
        checkout-index 写回工作区 → clean 删掉 index 里没有的文件 → 提交一个
        "revert" 节点保持时间线线性(快照不丢,可再向前)。
        """
        try:
            self._git("read-tree", sha)
            self._git("checkout-index", "-a", "-f")
            self._git("clean", "-fd", check=False)
            self._git("commit", "-q", "--allow-empty", "-m", f"revert to {sha[:10]}")
            logger.info("[shadow] reverted %s to %s", self._work_tree, sha[:10])
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
            logger.warning("[shadow] revert failed: %s", e)
            return False
