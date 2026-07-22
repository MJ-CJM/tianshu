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
from dataclasses import dataclass
from pathlib import Path

from tianshu.executor.git_backend import (
    GitBackend,
    GitBackendError,
    GitIdentity,
    GitLocation,
)

logger = logging.getLogger(__name__)

_SHADOW_ROOT = Path("~/.tianshu/shadow").expanduser()
_SHADOW_IDENTITY = GitIdentity("tianshu-shadow", "shadow@tianshu.local")


@dataclass(frozen=True)
class Snapshot:
    sha: str
    label: str
    created_at: str


class ShadowSnapshotError(RuntimeError):
    pass


class ShadowSnapshot:
    """一个工作区的影子快照仓(独立 GIT_DIR,与工作区物理分离)。"""

    def __init__(
        self,
        work_tree: Path,
        edict_id: str,
        *,
        root: Path | None = None,
        git_backend: GitBackend | None = None,
    ) -> None:
        self._work_tree = Path(work_tree).resolve()
        self._git_dir = (root or _SHADOW_ROOT) / edict_id / "gitdir"
        self._git_backend = git_backend or GitBackend()
        self._git_location = GitLocation(self._work_tree, git_dir=self._git_dir)

    @property
    def git_dir(self) -> Path:
        return self._git_dir

    def init(self) -> bool:
        """初始化影子仓;git 不可用或失败时返回 False(优雅降级)。"""
        try:
            self._git_dir.mkdir(parents=True, exist_ok=True)
            if not (self._git_dir / "HEAD").exists():
                self._git_backend.init_repository(self._git_location)
                # 独立仓需要自己的 excludesfile 语义;默认忽略 .git 自身即可
            return True
        except (GitBackendError, ValueError, OSError) as e:
            logger.warning("[shadow] init failed for %s: %s", self._work_tree, e)
            return False

    def snapshot(self, label: str) -> Snapshot | None:
        """打一次快照(add -A + commit);无变更时也提交空 commit 保留节点时间线。"""
        try:
            self._git_backend.stage_all(self._git_location)
            # --allow-empty:执行节点即便没改文件也留一个快照锚点
            sha = self._git_backend.commit(
                self._git_location,
                label,
                identity=_SHADOW_IDENTITY,
                allow_empty=True,
            )
            created = self._git_backend.commit_timestamp(self._git_location, sha)
            logger.info("[shadow] snapshot %s (%s) @ %s", sha[:10], label, self._work_tree)
            return Snapshot(sha=sha, label=label, created_at=created)
        except (GitBackendError, ValueError, OSError) as e:
            logger.warning("[shadow] snapshot failed: %s", e)
            return None

    def list_snapshots(self) -> list[Snapshot]:
        try:
            return [
                Snapshot(sha=entry.sha, label=entry.subject, created_at=entry.committed_at)
                for entry in self._git_backend.list_log(self._git_location)
            ]
        except (GitBackendError, ValueError, OSError):
            return []

    def revert(self, sha: str) -> bool:
        """把工作区恢复到某快照的**精确**状态(含删除该快照之后新增的文件)。

        `checkout <sha> -- .` 只还原快照里有的路径,快照之后 commit 进来的新文件
        仍是 tracked、clean 删不掉。正确做法:read-tree 把 index 设为目标树 →
        checkout-index 写回工作区 → clean 删掉 index 里没有的文件 → 提交一个
        "revert" 节点保持时间线线性(快照不丢,可再向前)。
        """
        try:
            self._git_backend.restore_snapshot(
                self._git_location,
                sha,
                identity=_SHADOW_IDENTITY,
            )
            logger.info("[shadow] reverted %s to %s", self._work_tree, sha[:10])
            return True
        except (GitBackendError, ValueError, OSError) as e:
            logger.warning("[shadow] revert failed: %s", e)
            return False
